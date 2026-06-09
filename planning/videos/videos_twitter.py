"""X (Twitter) video composer driver for the weekly-video orchestrator.

Walks the X /home composer for one clip: side-nav Post → upload .mp4 →
caption (short) → schedule modal → date+time → Confirm → final Schedule.
Mirrors the photo flow in ``planning.twitter.schedule_twitter_posts`` and
re-uses every composer helper from that module — the only platform-level
difference is that the file pushed through ``fileInput`` is an .mp4.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.twitter.twitter_session import (  # noqa: E402
    LoginRequiredError,
    TwitterSession,
    load_twitter_config,
)
from planning.twitter.schedule_twitter_posts import (  # noqa: E402
    _cancel_composer,
    _click_compose_area,
    _click_confirm_in_modal,
    _click_final_schedule_action,
    _click_schedule_toolbar,
    _dismiss_blocking_modals,
    _set_schedule_modal,
    _type_caption,
    _upload_image,
    _wait_composer_clears,
    return_to_home,
)
from planning.videos.videos_session import (  # noqa: E402
    VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS,
    ClipPayload,
)

logger = logging.getLogger("videos_twitter")


@dataclass
class VideoRow:
    page_id: str
    day: date
    payload: ClipPayload
    existing_post_url: Optional[str]

    @property
    def day_title(self) -> str:
        return self.day.strftime("%Y%m%d")


def _primary_button_enabled(page) -> Optional[bool]:
    """Tri-state for X's composer primary action button.

    Returns True if enabled, False if present-but-disabled, None if not found.
    X keeps this button (``tweetButton``) disabled while an attached video is
    still uploading / processing and re-enables it once the clip is ready (we
    always type a caption first, so a disabled button after attaching media
    means the upload is still in flight, not an empty composer).

    Scoped to the composer ``[role="dialog"]`` on purpose: the home timeline's
    inline composer (``tweetButtonInline``, outside any dialog) is always present
    and permanently disabled because it's empty. An unscoped ``.last`` selector
    picks *that* button and so never sees the real, enabled Schedule button —
    the actual root cause of the "X never schedules the video" hang (issue #107,
    correcting #106). Confirmed live: the in-dialog ``tweetButton`` enables ~8 s
    after the upload's STATUS=succeeded while the inline one stays disabled.
    """
    loc = page.locator(
        '[role="dialog"] [data-testid="tweetButton"], '
        '[role="dialog"] [data-testid="tweetButtonInline"]'
    ).last
    try:
        if not loc.count():
            return None
        aria = loc.get_attribute("aria-disabled")
        dis = loc.get_attribute("disabled")
        return aria in (None, "false") and dis is None
    except Exception:
        return None


def _video_upload_in_progress(page) -> bool:
    """True while X is still uploading / processing the attached clip.

    Signal: the composer's primary action button is present-but-disabled. We
    always type a caption before attaching media, so a disabled button means the
    clip is still uploading/processing, not an empty composer. (We deliberately
    do NOT key off ``[role="progressbar"]`` — the composer's video *player* mounts
    its own scrubber/volume progressbars once the preview renders, so that signal
    is a false positive that never clears; confirmed live in issue #107.)
    """
    return _primary_button_enabled(page) is False


def _wait_for_video_upload_ready(page, timeout_ms: int) -> None:
    """Block until X finishes uploading / processing the clip, BEFORE scheduling.

    ``_upload_image`` only waits for the preview thumbnail to render — X keeps
    uploading the full clip in the background. Opening the schedule modal and
    confirming *during* that window stalls finalization: the final Schedule
    button then never enables, even for a small, platform-safe clip (issue #107,
    on top of #106). So we wait for the upload to settle here, before touching
    the schedule flow — mirroring LinkedIn's ``_wait_for_video_ready`` and the
    IG upload settle. Raises ``RuntimeError`` if it never settles.
    """
    # Give the upload a moment to register an in-progress signal — a tiny clip
    # may already be done, in which case we proceed straight away.
    appear_deadline = page.evaluate("() => Date.now()") + 10000
    saw_progress = False
    while page.evaluate("() => Date.now()") < appear_deadline:
        if _video_upload_in_progress(page):
            saw_progress = True
            break
        page.wait_for_timeout(250)
    if not saw_progress:
        logger.info("ℹ️ TW: no upload-in-progress signal seen — clip appears ready.")
        return

    logger.info("⏳ TW clip still uploading/processing — waiting before schedule…")
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    last = None
    while page.evaluate("() => Date.now()") < deadline:
        if not _video_upload_in_progress(page):
            logger.info("✅ TW clip upload/processing complete — proceeding to schedule.")
            page.wait_for_timeout(800)
            return
        state = f"progress/button-disabled (btn_enabled={_primary_button_enabled(page)})"
        if state != last:
            logger.debug("⏳ TW upload not ready (%s) — polling…", state)
            last = state
        page.wait_for_timeout(500)
    raise RuntimeError(
        "TW clip upload/processing never settled — X did not finish accepting the "
        "clip within the wait window. The .mp4 may still exceed X's limits "
        "(≈512 MB / 2:20 / ~25 Mbps) or X is degraded; retry later."
    )


def _wait_tweet_button_enabled(page, timeout_ms: int) -> None:
    """Wait until X's final Schedule (tweetButton) is no longer disabled.

    Safety net after Confirm in the schedule modal — by this point the upload
    has already settled (see ``_wait_for_video_upload_ready``), so this normally
    returns immediately. ``_click_final_schedule_action`` uses a JS-click
    fallback that *bypasses* the disabled state, so clicking while still disabled
    is a silent no-op — the schedule never submits and the composer never clears
    (issue #106). Raises ``RuntimeError`` if it never enables.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    last = None
    while page.evaluate("() => Date.now()") < deadline:
        enabled = _primary_button_enabled(page)
        if enabled is True:
            return
        state = "not found" if enabled is None else "disabled"
        if state != last:
            logger.debug("⏳ TW final Schedule not ready (%s) — polling…", state)
            last = state
        page.wait_for_timeout(500)
    raise RuntimeError(
        "TW final Schedule button never enabled — X has not accepted the clip. "
        "Most often the .mp4 exceeds X's limits (≈512 MB / 2:20 / ~25 Mbps) or "
        "carries an embedded subtitle track; re-encode the clip lower / strip "
        "extra tracks for X."
    )


def schedule_one_video(
    session: TwitterSession,
    tw_cfg: dict,
    video_cfg: dict,
    row: VideoRow,
    *,
    dry_run: bool,
) -> str:
    """Drive the X UI for one clip; returns a one-line status string."""
    page = session.page
    label = row.day_title

    _click_compose_area(page)
    _type_caption(page, row.payload.caption_short)
    # X's fileInput accepts .mp4 directly — same helper as image upload.
    _upload_image(page, row.payload.video_path)
    page.wait_for_timeout(2500)
    # Wait for X to finish uploading/processing the clip BEFORE opening the
    # schedule modal — confirming a schedule mid-upload stalls finalization and
    # the final Schedule button then never enables (issue #107).
    _wait_for_video_upload_ready(page, VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS)
    _click_schedule_toolbar(page)
    _set_schedule_modal(
        page, row.day,
        video_cfg["post_hour_local"], video_cfg["post_minute_local"],
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-tw-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s TW: schedule modal ready, screenshot → %s", label, shot)
        import re
        for name_re in (r"^cancel$", r"^close$"):
            try:
                btn = page.get_by_role("button", name=re.compile(name_re, re.I))
                if btn.count():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                pass
        _cancel_composer(page)
        return "TW:DRY"

    _click_confirm_in_modal(page)
    # X keeps the final Schedule button disabled until the video finishes
    # processing; clicking before then is a silent no-op (issue #106).
    _wait_tweet_button_enabled(page, VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS)
    _click_final_schedule_action(page)
    # A large clip is still committing server-side after Schedule; the inline
    # composer only reverts once that finishes. Use the video budget, not the
    # image-sized default (issue #106).
    if not _wait_composer_clears(page, timeout_ms=VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS):
        shot = out_dir / f"{label}-tw-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"TW composer did not clear — see {shot}")
    page.wait_for_timeout(1200)
    logger.info("✅ LIVE %s TW video scheduled", label)
    return "TW:LIVE"


def run(rows: list[VideoRow], video_cfg: dict, *, dry_run: bool) -> list[dict]:
    if not rows:
        return []
    tw_cfg = load_twitter_config()
    results: list[dict] = []
    with TwitterSession(tw_cfg) as session:
        try:
            session.goto_with_login_check(tw_cfg["feed_url"])
        except LoginRequiredError as err:
            logger.error("❌ TW login required: %s", err)
            for row in rows:
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
            return results
        session.page.wait_for_timeout(3500)
        _dismiss_blocking_modals(session.page)
        session.page.wait_for_timeout(400)

        for row in rows:
            return_to_home(session.page, tw_cfg["feed_url"])
            try:
                status = schedule_one_video(session, tw_cfg, video_cfg, row, dry_run=dry_run)
                results.append({
                    "day": row.day_title,
                    "status": "DRY" if dry_run else "LIVE",
                    "detail": status,
                })
            except (RuntimeError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-tw-video-error")
                logger.error("❌ TW %s failed: %s (screenshot %s)", row.day_title, err, shot)
                _cancel_composer(session.page)
                return_to_home(session.page, tw_cfg["feed_url"])
                results.append({
                    "day": row.day_title,
                    "status": "FAIL",
                    "detail": f"{err} (screenshot {shot})",
                })
    return results
