"""Instagram (Meta planner) video composer driver for the weekly-video orchestrator.

Schedules one clip through Meta's **Reels** composer (issue #118): planner →
hover day column → Schedule menu → "Create reel" → Add Video → caption →
Next → Scheduling options → pick "Schedule" → fill date+time → Schedule.

The feed-post composer is deliberately not used for video: Instagram rejects
vertical 9:16 clips there ("doesn't fit within Instagram's accepted aspect
ratio range of 4:5 to 16:9", Schedule never enables) and duplicates a single
upload into two tiles. The Reels composer accepts 9:16 and attaches one tile
per file. The reel-specific composer helpers live in
``planning.instagram.instagram_reels``; the shared caption/menu/session
helpers reused here still live in ``planning.instagram.schedule_instagram_posts``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.instagram.instagram_session import (  # noqa: E402
    InstagramSession,
    load_instagram_config,
)
from planning.instagram.instagram_reels import (  # noqa: E402
    _click_reel_footer,
    _dismiss_reel_success_dialog,
    _reel_add_video,
    _select_reel_schedule_option,
    _set_reel_schedule_datetime,
    _wait_reel_composer_closes,
    _wait_reel_footer_enabled,
    _wait_reels_composer_ready,
)
from planning.instagram.schedule_instagram_posts import return_to_planner  # noqa: E402
from planning.instagram.instagram_composer import (  # noqa: E402
    cancel_composer,
    dismiss_meta_verified_modal,
    fill_post_text,
    open_day_schedule_menu,
)
from planning.videos.videos_session import VideoRow, run_video_driver  # noqa: E402

logger = logging.getLogger("videos_instagram")


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
    open_day_schedule_menu(page, row.day, "Create reel")
    _wait_reels_composer_ready(page)

    _reel_add_video(page, row.payload.video_path)
    # Caption lives on the reel-details step (fillable before the upload
    # finishes processing).
    fill_post_text(page, row.payload.caption_short)

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
        cancel_composer(page)
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


def _after_login(page) -> None:
    # Let the planner SPA mount, then dismiss any first-load upsell modal.
    dismiss_meta_verified_modal(page)
    page.wait_for_timeout(500)


def run(rows: list[VideoRow], video_cfg: dict, *, dry_run: bool) -> list[dict]:
    return run_video_driver(
        rows, video_cfg, dry_run=dry_run, platform="IG", log=logger,
        load_cfg=load_instagram_config, session_cls=InstagramSession, schedule_one=schedule_one_video,
        return_home=return_to_planner, cancel_composer=cancel_composer, login_settle_ms=4500,
        after_login=_after_login,
    )
