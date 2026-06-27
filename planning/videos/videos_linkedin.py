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
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.linkedin.linkedin_composer import (  # noqa: E402
    click_feed_entry,
    fill_caption_with_mentions,
    wait_for_upload_complete,
)
from planning.linkedin.linkedin_labels import VIDEO_TEXT_RE  # noqa: E402
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


# Re-exported for backward compatibility with any caller that imported these
# names from videos_linkedin in the past. New code should import directly
# from ``planning.linkedin.linkedin_composer``.
_fill_caption_with_mentions = fill_caption_with_mentions
_wait_for_upload_complete = wait_for_upload_complete


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
    ``Video`` affordance — a ``<p>Video</p>`` label inside the redesigned,
    role-less ``<a>`` share-box wrapper, matched by visible text (issue #140;
    matching by accessible name caught a feed "Play video" button instead).

    ``click_feed_entry`` re-resolves on each attempt, so it absorbs both the
    cold-start race on the first feed action of a freshly-navigated session
    (issue #27) and the share-box rehydration swap; a warm affordance still
    returns on the first attempt in ~1 s.
    """
    click_feed_entry(page, VIDEO_TEXT_RE, "Video")


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
            next_btn = page.locator('[role="dialog"] button:has-text("Next"), [role="dialog"] button:has-text("Siguiente")').first
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
        page.locator('[role="dialog"] button:has-text("Next"), [role="dialog"] button:has-text("Siguiente")').first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Next' in the video editor: {err}")


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
