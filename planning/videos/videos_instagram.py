"""Instagram (Meta planner) video composer driver for the weekly-video orchestrator.

Schedules one clip through Meta's **Reels** composer (issue #118): planner →
hover day column → Schedule menu → "Create reel" → Add Video → caption →
Next → Scheduling options → pick "Schedule" → fill date+time → Schedule.

The feed-post composer is deliberately not used for video: Instagram rejects
vertical 9:16 clips there ("doesn't fit within Instagram's accepted aspect
ratio range of 4:5 to 16:9", Schedule never enables) and duplicates a single
upload into two tiles. The Reels composer accepts 9:16 and attaches one tile
per file. The reel-specific composer helpers (and the shared date/time +
caption helpers) all live in ``planning.instagram.schedule_instagram_posts``.
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
from planning.instagram.instagram_session import (  # noqa: E402
    InstagramSession,
    LoginRequiredError,
    load_instagram_config,
)
from planning.instagram.schedule_instagram_posts import (  # noqa: E402
    _cancel_composer,
    _click_reel_footer,
    _dismiss_reel_success_dialog,
    _fill_post_text,
    _open_day_schedule_menu,
    _reel_add_video,
    _select_reel_schedule_option,
    _set_reel_schedule_datetime,
    _wait_reel_composer_closes,
    _wait_reel_footer_enabled,
    _wait_reels_composer_ready,
    dismiss_meta_verified_modal,
    return_to_planner,
)
from planning.videos.videos_session import ClipPayload  # noqa: E402

logger = logging.getLogger("videos_instagram")


@dataclass
class VideoRow:
    page_id: str
    day: date
    payload: ClipPayload
    existing_post_url: Optional[str]

    @property
    def day_title(self) -> str:
        return self.day.strftime("%Y%m%d")


def schedule_one_video(
    session: InstagramSession,
    ig_cfg: dict,
    video_cfg: dict,
    row: VideoRow,
    *,
    dry_run: bool,
) -> str:
    page = session.page
    label = row.day_title

    # Issue #118 — schedule via the Reels composer, not the feed-post composer.
    # Instagram rejects vertical 9:16 clips in the post composer ("doesn't fit
    # within Instagram's accepted aspect ratio range of 4:5 to 16:9", Schedule
    # never enables) and IG now routes video to reels anyway. "Create reel" is a
    # full-page wizard that accepts 9:16 and attaches one tile per file (no
    # duplicate-attach). See the Reels-composer section in
    # ``schedule_instagram_posts`` for the verified-live selectors.
    _open_day_schedule_menu(page, row.day, "Create reel")
    _wait_reels_composer_ready(page)

    _reel_add_video(page, row.payload.video_path)
    # Caption lives on the reel-details step (fillable before the upload
    # finishes processing).
    _fill_post_text(page, row.payload.caption_short)

    # Footer "Next" stays aria-disabled until Meta finishes processing the
    # upload (the left rail counts up to 100%). A multi-MB clip can take a
    # while, so wait generously before advancing.
    _wait_reel_footer_enabled(page, "Next", timeout_ms=120000)
    _click_reel_footer(page, "Next")

    # Scheduling-options step: choose "Schedule" (vs "Share now") to reveal the
    # date/time inputs, then set them. The reels date field is a segmented
    # editor that must be set via its calendar popup (typing corrupts it) — see
    # _set_reel_schedule_datetime.
    _select_reel_schedule_option(page)
    _set_reel_schedule_datetime(
        page, row.day,
        video_cfg["post_hour_local"], video_cfg["post_minute_local"],
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-ig-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s IG: reel composer ready, screenshot → %s", label, shot)
        _cancel_composer(page)
        return "IG:DRY"

    # Footer flips to "Schedule" once the Schedule option is selected; it stays
    # aria-disabled until the date/time are valid and the upload is done.
    _wait_reel_footer_enabled(page, "Schedule", timeout_ms=120000)
    _click_reel_footer(page, "Schedule")
    if not _wait_reel_composer_closes(page, timeout_ms=30000):
        shot = out_dir / f"{label}-ig-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"IG reel composer did not close — see {shot}")
    # On success Meta may stay on /reels_composer/ behind a "Reel scheduled"
    # confirmation dialog rather than navigating back — dismiss it so the next
    # day starts from a clean planner state (issue #125).
    _dismiss_reel_success_dialog(page)
    page.wait_for_timeout(1500)
    logger.info("✅ LIVE %s IG video scheduled as reel", label)
    return "IG:LIVE"


def run(rows: list[VideoRow], video_cfg: dict, *, dry_run: bool) -> list[dict]:
    if not rows:
        return []
    ig_cfg = load_instagram_config()
    results: list[dict] = []
    with InstagramSession(ig_cfg) as session:
        try:
            session.goto_with_login_check(ig_cfg["feed_url"])
        except LoginRequiredError as err:
            logger.error("❌ IG login required: %s", err)
            for row in rows:
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
            return results
        session.page.wait_for_timeout(4500)
        dismiss_meta_verified_modal(session.page)
        session.page.wait_for_timeout(500)

        for row in rows:
            return_to_planner(session.page, ig_cfg["feed_url"])
            try:
                status = schedule_one_video(session, ig_cfg, video_cfg, row, dry_run=dry_run)
                results.append({
                    "day": row.day_title,
                    "status": "DRY" if dry_run else "LIVE",
                    "detail": status,
                })
            except (RuntimeError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-ig-video-error")
                logger.error("❌ IG %s failed: %s (screenshot %s)", row.day_title, err, shot)
                _cancel_composer(session.page)
                return_to_planner(session.page, ig_cfg["feed_url"])
                results.append({
                    "day": row.day_title,
                    "status": "FAIL",
                    "detail": f"{err} (screenshot {shot})",
                })
    return results
