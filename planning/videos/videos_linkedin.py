"""LinkedIn video composer driver for the weekly-video orchestrator.

Walks the LinkedIn feed's native ``Video`` composer for one clip:
feed → Video → upload .mp4 → Next → caption (long body) → Schedule →
date+time → Next → final Schedule. Mirrors the photo flow in
``planning.linkedin.schedule_linkedin_posts`` and re-uses its schedule-dialog
helpers; the photo flow's ALT step is intentionally skipped (videos have no
ALT in LinkedIn's UI). The session, login check, and date/time helpers are
all imported from the sister package — no duplication.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.linkedin.linkedin_session import (  # noqa: E402
    LinkedInSession,
    LoginRequiredError,
    load_linkedin_config,
)
from planning.linkedin.schedule_linkedin_posts import (  # noqa: E402
    _click_final_schedule,
    _click_schedule_next,
    _close_dialogs,
    _open_schedule_dialog,
    _set_schedule_datetime,
)
from planning.videos.videos_session import ClipPayload  # noqa: E402

logger = logging.getLogger("videos_linkedin")


# A LinkedIn mention starts at "@" and runs through one or more capitalized
# tokens separated by single spaces. We intentionally STOP at the first
# non-letter (punctuation, newline, lowercase) so "@Hannah Wilson. " resolves
# the mention "Hannah Wilson" and leaves the period + space as literal text.
# Periods/apostrophes inside names (e.g. "@O'Connor") are NOT supported by
# this regex; extend if you hit a real case.
_MENTION_RE = re.compile(r"@([A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)*)")

# Selector candidates for the LinkedIn mention typeahead dropdown, in
# specificity order. LinkedIn's UI varies across rollouts; the generic
# fallbacks at the bottom catch builds that drop the testid / aria hooks.
_MENTION_DROPDOWN_SELECTORS = (
    'div.mentions-typeahead-content [role="option"]',
    'div[data-test-id="mentions-typeahead"] [role="option"]',
    '[aria-label*="mention" i] [role="option"]',
    '.artdeco-typeahead__results-list li',
    '[role="listbox"] [role="option"]',
)


def _click_mention_suggestion(page: Page, name: str, *, timeout_ms: int = 6000) -> bool:
    """Wait for the LI mention typeahead and click the matching option.

    Strategy: poll the candidate selectors above until at least one returns
    a visible option list, then pick the first item whose visible text
    contains the typed name (case-insensitive). If no name match within
    the first five items, click the topmost (LinkedIn's typeahead ranks
    relevant matches first). Returns True on click, False on timeout.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    name_lower = name.lower()
    while page.evaluate("() => Date.now()") < deadline:
        for sel in _MENTION_DROPDOWN_SELECTORS:
            try:
                items = page.locator(sel)
                count = items.count()
                if count == 0:
                    continue
                # Prefer an item whose text contains the typed name.
                for i in range(min(count, 6)):
                    item = items.nth(i)
                    try:
                        text = (item.inner_text(timeout=400) or "").lower()
                    except Exception:
                        continue
                    if name_lower in text:
                        try:
                            item.click(timeout=2000)
                            return True
                        except Exception:
                            continue
                # No name match in the first six — click the top-ranked.
                try:
                    items.first.click(timeout=2000)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
        page.wait_for_timeout(200)
    return False


def _fill_caption_with_mentions(page: Page, caption: str) -> None:
    """Type the caption into the LI composer, resolving every @mention.

    For each ``@CapitalizedName`` (or ``@First Last``) in the caption:
      1. Type the literal text up to the @.
      2. Type @, then the name letter-by-letter (typeahead populates per
         keystroke; ~80ms delay per char is enough for LinkedIn to fetch).
      3. Click the matching suggestion in the dropdown.
      4. Resume typing the tail.

    If a mention can't be resolved (no dropdown, no matching suggestion),
    the @<name> stays as literal text and a warning is logged — we don't
    fail the whole row over one missing mention.
    """
    if not caption:
        return
    editor = page.locator('div[role="textbox"][contenteditable="true"]')
    try:
        editor.first.wait_for(state="visible", timeout=10000)
        editor.first.click()
    except Exception as err:
        raise RuntimeError(f"Could not focus the LinkedIn caption editor: {err}")

    pos = 0
    mention_count = 0
    resolved_count = 0
    for m in _MENTION_RE.finditer(caption):
        if m.start() > pos:
            page.keyboard.type(caption[pos:m.start()], delay=4)
        mention_count += 1
        name = m.group(1)
        # Type @ first, then the name letter-by-letter with a small delay so
        # LinkedIn's typeahead has time to query as we type.
        page.keyboard.type("@", delay=20)
        page.wait_for_timeout(250)
        page.keyboard.type(name, delay=80)
        page.wait_for_timeout(400)
        clicked = _click_mention_suggestion(page, name)
        if clicked:
            resolved_count += 1
            logger.info("🔗 Resolved LinkedIn mention @%s", name)
        else:
            logger.warning(
                "⚠️ Could not resolve LinkedIn mention @%s — left as literal text. "
                "The composer may still show the unresolved @<name>.", name,
            )
        pos = m.end()
    if pos < len(caption):
        page.keyboard.type(caption[pos:], delay=4)
    if mention_count:
        logger.info("📝 Caption typed (%d mentions, %d resolved).",
                    mention_count, resolved_count)
    else:
        logger.debug("📝 Caption typed (%d chars, no mentions).", len(caption))


@dataclass
class VideoRow:
    """One editorial row's worth of input for the per-platform driver."""

    page_id: str
    day: date
    payload: ClipPayload
    existing_post_url: Optional[str]

    @property
    def day_title(self) -> str:
        return self.day.strftime("%Y%m%d")


def _click_video_button(page: Page) -> None:
    """Open the post composer in Video mode from the feed.

    Mirrors ``_click_add_photo`` from the photo flow but targets the
    ``Video`` button. Same DOM shape — a ``<p>Video</p>`` text node inside a
    ``<div role="button">`` wrapper.
    """
    try:
        page.get_by_role("button", name=re.compile(r"^video$", re.I)).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Video' on the LinkedIn feed: {err}")


def _upload_video(page: Page, video_path: Path) -> None:
    """Push the .mp4 into LinkedIn's video Editor dialog.

    LinkedIn's photo flow auto-mounts an ``input[type=file]`` the moment
    you click "Photo". The video flow does NOT: the "Video" button opens an
    Editor dialog that shows "Select files to begin" with an "Upload from
    computer" button, and the file chooser only opens when that button is
    clicked. Strategy:
      1. Fast path: if any ``input[type=file]`` is already attached in the
         DOM, just push the file at it (catches future LinkedIn rebuilds).
      2. Otherwise: click the dialog's "Upload from computer" button and
         intercept the OS file chooser via ``expect_file_chooser``.
    """
    # LinkedIn's video Editor mounts a hidden ``input#media-editor-file-selector__file-input``
    # with ``accept="...video/mp4..."`` and a visually-hidden wrapper. Push the file
    # straight at that input — the visible "Upload from computer" button is purely
    # decorative and is intercepted by a styled <div> overlay (clicking it errors out).
    inp = page.locator('input#media-editor-file-selector__file-input, input[type="file"]').first
    try:
        inp.wait_for(state="attached", timeout=15000)
        inp.set_input_files(str(video_path))
    except Exception as err:
        raise RuntimeError(f"Could not upload video to LinkedIn editor: {err}")


def _wait_for_video_ready(page: Page, timeout_ms: int = 180000) -> None:
    """Wait for LinkedIn to finish processing the uploaded video.

    Signal: the editor's primary ``Next`` button becomes enabled and the
    progress bar (when present) is gone. We poll for the Next button being
    clickable inside the open dialog with a generous timeout (LinkedIn
    transcoding can take a couple of minutes for longer clips).
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline:
        try:
            next_btn = page.locator('[role="dialog"] button:has-text("Next")').first
            if next_btn.count():
                disabled = next_btn.get_attribute("disabled")
                aria_dis = next_btn.get_attribute("aria-disabled")
                if not disabled and (aria_dis is None or aria_dis.lower() == "false"):
                    return
        except Exception:
            pass
        page.wait_for_timeout(1500)
    raise RuntimeError("LinkedIn video processing did not finish within the timeout window.")


def _click_video_next(page: Page) -> None:
    """Click 'Next' in the video editor → goes to the composer."""
    try:
        page.locator('[role="dialog"] button:has-text("Next")').first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Next' in the video editor: {err}")


# After clicking the final Schedule, LinkedIn closes the composer immediately
# but keeps uploading the video in the background. If we tear down the
# Playwright context before the upload finishes, the scheduled post is created
# with no media attached and the user sees "Something went wrong" when opening
# the scheduled-post detail. The signals LinkedIn uses for the background
# upload vary across rollouts; we hunt several of them defensively, then fall
# back to a fixed conservative wait if none is found.
_UPLOAD_IN_PROGRESS_SELECTORS = (
    'div[aria-label*="upload" i]:not([aria-label*="complete" i])',
    'div[role="status"]:has-text("Uploading")',
    'div[role="alert"]:has-text("upload" i)',
    'div:has-text("don\'t close")',
    'div:has-text("Don\'t close")',
    'div:has-text("Do not close")',
    'div:has-text("video is uploading")',
    'div:has-text("Uploading your video")',
    # The post-schedule toast widget LI shows at the bottom-left of the feed.
    'div.global-alert',
    '[data-test-global-alert-id]',
)

_UPLOAD_COMPLETE_TEXT_RE = re.compile(
    r"(upload\s*complete|video\s*uploaded|successfully\s*scheduled|post\s*scheduled|"
    r"your\s*post\s*will\s*be\s*published)",
    re.I,
)


def _upload_in_progress_visible(page: Page) -> bool:
    for sel in _UPLOAD_IN_PROGRESS_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            continue
    return False


def _wait_for_upload_complete(page: Page, *, timeout_ms: int = 420000) -> bool:
    """Block until LI's background video upload signals completion.

    Strategy:
      1. Settle for ~2s post-Schedule so the toast/banner has a chance to mount.
      2. If an explicit "upload complete" / "post scheduled" text appears, return.
      3. Otherwise, if an in-progress indicator was at any point visible, poll
         until it disappears (then add a 3s safety buffer).
      4. If no indicator EVER showed (LI may have already finished by the time
         we looked), fall back to a 60s safety wait — better than tearing down
         immediately and orphaning a partially-uploaded video.

    ``timeout_ms`` is the hard cap on the in-progress polling loop (default 7
    minutes) — long enough for a 30-50 MB clip on a slow uplink.
    """
    page.wait_for_timeout(2000)

    # Fast path: explicit "complete" text already visible.
    try:
        if page.get_by_text(_UPLOAD_COMPLETE_TEXT_RE).count() > 0:
            logger.info("✅ LI upload-complete signal already visible — proceeding.")
            page.wait_for_timeout(2000)
            return True
    except Exception:
        pass

    saw_in_progress = _upload_in_progress_visible(page)
    if not saw_in_progress:
        # Look for a few more seconds in case the toast is slow to mount.
        for _ in range(8):  # ~4s
            page.wait_for_timeout(500)
            if _upload_in_progress_visible(page):
                saw_in_progress = True
                break
            try:
                if page.get_by_text(_UPLOAD_COMPLETE_TEXT_RE).count() > 0:
                    logger.info("✅ LI upload-complete signal appeared during settle.")
                    page.wait_for_timeout(2000)
                    return True
            except Exception:
                pass

    if not saw_in_progress:
        logger.info(
            "ℹ️ No LI upload-in-progress indicator detected; holding the browser "
            "open for 60s as a safety buffer so a slow background upload can finish."
        )
        page.wait_for_timeout(60000)
        return True

    logger.info(
        "⏳ LI background video upload in progress — polling for completion "
        "(max %ds). Browser will stay open until done.", timeout_ms // 1000,
    )
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    last_log = 0
    while page.evaluate("() => Date.now()") < deadline:
        # Explicit success text wins immediately.
        try:
            if page.get_by_text(_UPLOAD_COMPLETE_TEXT_RE).count() > 0:
                logger.info("✅ LI upload-complete text detected.")
                page.wait_for_timeout(3000)
                return True
        except Exception:
            pass
        if not _upload_in_progress_visible(page):
            logger.info("✅ LI upload-in-progress indicator gone; upload likely complete.")
            page.wait_for_timeout(3000)
            return True
        now = page.evaluate("() => Date.now()")
        if now - last_log > 15000:
            logger.info("⏳ ...still uploading (%ds elapsed).",
                        (now - (deadline - timeout_ms)) // 1000)
            last_log = now
        page.wait_for_timeout(2000)

    logger.warning(
        "⚠️ LI upload-complete signal not received within %ds. "
        "The scheduled post MAY be incomplete — verify in Scheduled posts.",
        timeout_ms // 1000,
    )
    return False


def schedule_one_video(
    session: LinkedInSession,
    li_cfg: dict,
    video_cfg: dict,
    row: VideoRow,
    *,
    dry_run: bool,
) -> str:
    """Drive the LinkedIn UI for one clip; returns a one-line status string."""
    page = session.page
    label = row.day_title

    session.goto_with_login_check(li_cfg["feed_url"])
    _click_video_button(page)
    page.wait_for_timeout(1500)
    _upload_video(page, row.payload.video_path)
    _wait_for_video_ready(page)
    _click_video_next(page)
    page.wait_for_timeout(2500)

    if not row.payload.caption_long:
        raise RuntimeError("LinkedIn requires the clip page body as the caption — body is empty.")
    _fill_caption_with_mentions(page, row.payload.caption_long)
    page.wait_for_timeout(800)

    _open_schedule_dialog(page)
    page.wait_for_timeout(1500)
    _set_schedule_datetime(
        page, row.day,
        video_cfg["post_hour_local"], video_cfg["post_minute_local"],
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-li-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s LI: schedule dialog ready, screenshot → %s", label, shot)
        _close_dialogs(page)
        return "LI:DRY"

    _click_schedule_next(page)
    page.wait_for_timeout(1500)

    composer_locator = page.locator(
        '[role="dialog"]:has(div[role="textbox"][contenteditable="true"])'
    )
    pre_count = composer_locator.count()

    _click_final_schedule(page)

    deadline = page.evaluate("() => Date.now()") + 20000
    while page.evaluate("() => Date.now()") < deadline:
        if composer_locator.count() < pre_count:
            break
        page.wait_for_timeout(400)
    else:
        shot = out_dir / f"{label}-li-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(
            f"LI composer did not close within 20s after Schedule — see {shot}"
        )

    page.wait_for_timeout(1500)
    # CRITICAL: wait for LI's background video upload to finish before letting
    # the Playwright session close. Without this, LinkedIn creates the
    # scheduled post but the .mp4 never finishes uploading -> opening the
    # scheduled post shows "Something went wrong, please try reloading".
    _wait_for_upload_complete(page)
    logger.info("✅ LIVE %s LI video scheduled (upload settled)", label)
    return "LI:LIVE"


def run(
    rows: list[VideoRow],
    video_cfg: dict,
    *,
    dry_run: bool,
) -> list[dict]:
    """Open a single LI session, drive every row, return per-row status dicts.

    Status dict shape (matches sister schedulers): ``{"day", "status", "detail"}``
    where status ∈ ``LIVE`` / ``DRY`` / ``FAIL`` / ``LOGIN-REQUIRED``.
    """
    if not rows:
        return []
    li_cfg = load_linkedin_config()
    results: list[dict] = []
    with LinkedInSession(li_cfg) as session:
        for row in rows:
            try:
                status = schedule_one_video(session, li_cfg, video_cfg, row, dry_run=dry_run)
                results.append({
                    "day": row.day_title,
                    "status": "DRY" if dry_run else "LIVE",
                    "detail": status,
                })
            except LoginRequiredError as err:
                logger.error("❌ LI login required: %s", err)
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
                # Login error is fatal for the whole platform — mark remaining rows.
                for remaining in rows[rows.index(row) + 1:]:
                    results.append({
                        "day": remaining.day_title,
                        "status": "LOGIN-REQUIRED",
                        "detail": "LI login required (set on a prior row).",
                    })
                break
            except (RuntimeError, FileNotFoundError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-li-video-error")
                logger.error("❌ LI %s failed: %s (screenshot %s)", row.day_title, err, shot)
                results.append({
                    "day": row.day_title,
                    "status": "FAIL",
                    "detail": f"{err} (screenshot {shot})",
                })
                try:
                    _close_dialogs(session.page)
                except Exception:
                    pass
    return results
