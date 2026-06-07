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


def _wait_tweet_button_enabled(page, timeout_ms: int) -> None:
    """Wait until X's final Schedule (tweetButton) is no longer disabled.

    X keeps the composer's primary button ``aria-disabled`` while an uploaded
    video is still processing server-side. ``_click_final_schedule_action`` uses
    a JS-click fallback that *bypasses* the disabled state, so clicking during
    that window is a silent no-op — the schedule never submits and the composer
    never clears (issue #106). Poll until it enables (mirrors the Instagram /
    LinkedIn ready-waits). Raises ``RuntimeError`` if it never enables.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    last = None
    while page.evaluate("() => Date.now()") < deadline:
        loc = page.locator(
            '[data-testid="tweetButton"], button[data-testid="tweetButtonInline"]'
        ).last
        state = "not found"
        try:
            if loc.count():
                aria = loc.get_attribute("aria-disabled")
                dis = loc.get_attribute("disabled")
                if aria in (None, "false") and dis is None:
                    return
                state = f"aria-disabled={aria} disabled={dis}"
        except Exception:
            state = "error reading state"
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
