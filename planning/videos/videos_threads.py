"""Threads video composer driver for the weekly-video orchestrator.

Walks the Threads composer for one clip: profile → What's-new modal →
upload .mp4 → caption (short) → 3-dots → Schedule → calendar → time →
Done → final Schedule. Mirrors the photo flow in
``planning.threads.schedule_threads_posts`` and re-uses every composer
helper from that module.
"""

from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.threads.threads_session import (  # noqa: E402
    LoginRequiredError,
    ThreadsSession,
    load_threads_config,
)
from planning.threads.schedule_threads_posts import (  # noqa: E402
    _cancel_composer,
    _click_calendar_day,
    _click_calendar_done,
    _click_final_schedule_action,
    _click_schedule_menuitem,
    _navigate_calendar_month,
    _open_composer,
    _open_three_dots_menu,
    _set_calendar_time,
    _type_caption,
    _upload_image,
    _wait_composer_closes,
    return_to_profile,
)
from planning.videos.videos_session import ClipPayload  # noqa: E402

logger = logging.getLogger("videos_threads")


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
    session: ThreadsSession,
    th_cfg: dict,
    video_cfg: dict,
    row: VideoRow,
    *,
    dry_run: bool,
) -> str:
    page = session.page
    label = row.day_title

    _open_composer(page)
    _type_caption(page, row.payload.caption_short)
    # Threads composer's fileInput accepts .mp4 directly — same helper as image.
    _upload_image(page, row.payload.video_path)
    page.wait_for_timeout(2500)
    _open_three_dots_menu(page)
    _click_schedule_menuitem(page)
    _navigate_calendar_month(page, row.day)
    _click_calendar_day(page, row.day)
    _set_calendar_time(
        page, video_cfg["post_hour_local"], video_cfg["post_minute_local"],
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "videos"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-th-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s TH: calendar populated, screenshot → %s", label, shot)
        _cancel_composer(page)
        for _ in range(3):
            try:
                btn = page.get_by_role("button", name=re.compile(r"^discard$", re.I))
                if btn.count():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                pass
        return "TH:DRY"

    _click_calendar_done(page)
    _click_final_schedule_action(page)
    if not _wait_composer_closes(page, timeout_ms=25000):
        shot = out_dir / f"{label}-th-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"TH composer did not close — see {shot}")
    page.wait_for_timeout(1500)
    logger.info("✅ LIVE %s TH video scheduled", label)
    return "TH:LIVE"


def run(rows: list[VideoRow], video_cfg: dict, *, dry_run: bool) -> list[dict]:
    if not rows:
        return []
    th_cfg = load_threads_config()
    results: list[dict] = []
    with ThreadsSession(th_cfg) as session:
        try:
            session.goto_with_login_check(th_cfg["feed_url"])
        except LoginRequiredError as err:
            logger.error("❌ TH login required: %s", err)
            for row in rows:
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
            return results
        session.page.wait_for_timeout(3500)

        for row in rows:
            return_to_profile(session.page, th_cfg["feed_url"])
            try:
                status = schedule_one_video(session, th_cfg, video_cfg, row, dry_run=dry_run)
                results.append({
                    "day": row.day_title,
                    "status": "DRY" if dry_run else "LIVE",
                    "detail": status,
                })
            except (RuntimeError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-th-video-error")
                logger.error("❌ TH %s failed: %s (screenshot %s)", row.day_title, err, shot)
                _cancel_composer(session.page)
                return_to_profile(session.page, th_cfg["feed_url"])
                results.append({
                    "day": row.day_title,
                    "status": "FAIL",
                    "detail": f"{err} (screenshot {shot})",
                })
    return results
