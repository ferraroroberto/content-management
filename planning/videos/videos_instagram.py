"""Instagram (Meta planner) video composer driver for the weekly-video orchestrator.

Walks the Meta Business planner for one clip: planner → hover day column →
Schedule menu → Schedule post → close sub-modal → upload .mp4 → caption
(short) → Set date and time → fill date+time → Schedule. Reuses every
helper from ``planning.instagram.schedule_instagram_posts`` — Meta's
composer accepts .mp4 through the same FileChooser path as images.
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
    _click_action_button,
    _dismiss_initial_schedule_submodal,
    _ensure_set_date_toggle_on,
    _fill_post_text,
    _open_day_schedule_menu,
    _set_all_visible_date_time,
    _upload_files,
    _wait_composer_closes,
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

    _open_day_schedule_menu(page, row.day, "Schedule post")
    _dismiss_initial_schedule_submodal(page)
    # Meta's "Add photo/video" accepts .mp4 — give the same helper a list of one.
    _upload_files(page, [row.payload.video_path])
    # Video transcoding in Meta's composer takes noticeably longer than images.
    page.wait_for_timeout(6000)
    _fill_post_text(page, row.payload.caption_short)
    _ensure_set_date_toggle_on(page)
    _set_all_visible_date_time(
        page, row.day,
        video_cfg["post_hour_local"], video_cfg["post_minute_local"],
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-ig-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s IG: composer ready, screenshot → %s", label, shot)
        _cancel_composer(page)
        return "IG:DRY"

    _click_action_button(page, "Schedule")
    if not _wait_composer_closes(page, ig_cfg["feed_url"], timeout_ms=30000):
        shot = out_dir / f"{label}-ig-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"IG composer did not close — see {shot}")
    page.wait_for_timeout(1500)
    logger.info("✅ LIVE %s IG video scheduled", label)
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
