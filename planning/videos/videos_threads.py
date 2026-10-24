"""Threads video composer driver for the weekly-video orchestrator.

Walks the Threads composer for one clip: profile → What's-new modal →
upload .mp4 → caption (short) → 3-dots → Schedule → calendar → time →
Done → final Schedule. Mirrors the photo flow in
``planning.threads.schedule_threads_posts`` and re-uses every composer
helper from that module.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning._session_base import results_dir  # noqa: E402
from planning.threads.threads_session import (  # noqa: E402
    ThreadsSession,
    load_threads_config,
)
from planning.threads.schedule_threads_posts import return_to_profile  # noqa: E402
from planning.threads.threads_composer import (  # noqa: E402
    cancel_composer,
    click_calendar_day,
    click_calendar_done,
    click_final_schedule_action,
    click_schedule_menuitem,
    navigate_calendar_month,
    open_composer,
    open_three_dots_menu,
    set_calendar_time,
    type_caption,
    upload_image,
    wait_composer_closes,
)
from planning.threads.threads_labels import DISCARD_BTN_RE  # noqa: E402
from planning.videos.videos_session import (  # noqa: E402
    VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS,
    VideoRow,
    run_video_driver,
)

logger = logging.getLogger("videos_threads")


def schedule_one_video(
    session: ThreadsSession,
    th_cfg: dict,
    video_cfg: dict,
    row: VideoRow,
    *,
    dry_run: bool,
) -> str:
    page = session.page
    label = row.day_title

    open_composer(page)
    type_caption(page, row.payload.caption_short)
    # Threads composer's fileInput accepts .mp4 directly — same helper as image.
    upload_image(page, row.payload.video_path)
    page.wait_for_timeout(2500)
    open_three_dots_menu(page)
    click_schedule_menuitem(page)
    navigate_calendar_month(page, row.day)
    click_calendar_day(page, row.day)
    set_calendar_time(
        page, video_cfg["post_hour_local"], video_cfg["post_minute_local"],
    )

    out_dir = results_dir("videos")
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-th-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s TH: calendar populated, screenshot → %s", label, shot)
        cancel_composer(page)
        for _ in range(3):
            try:
                btn = page.get_by_role("button", name=DISCARD_BTN_RE)
                if btn.count():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                pass
        return "TH:DRY"

    click_calendar_done(page)
    click_final_schedule_action(page)
    # A large clip keeps uploading after Schedule; the New-thread dialog only
    # closes once it finalizes. Use the video budget, not the image-sized
    # default.
    if not wait_composer_closes(page, timeout_ms=VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS):
        shot = out_dir / f"{label}-th-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"TH composer did not close — see {shot}")
    page.wait_for_timeout(1500)
    logger.info("✅ LIVE %s TH video scheduled", label)
    return "TH:LIVE"


def run(rows: list[VideoRow], video_cfg: dict, *, dry_run: bool) -> list[dict]:
    return run_video_driver(
        rows, video_cfg, dry_run=dry_run, platform="TH", log=logger,
        load_cfg=load_threads_config, session_cls=ThreadsSession, schedule_one=schedule_one_video,
        return_home=return_to_profile, cancel_composer=cancel_composer, login_settle_ms=3500,
    )
