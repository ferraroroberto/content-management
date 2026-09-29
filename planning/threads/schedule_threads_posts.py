"""Schedule next week's Threads content via the native New-thread composer.

Reads rows where ``work in progress TH`` is checked, then for each in-scope
day drives the Threads composer at
``https://www.threads.com/@ferraroroberto`` to schedule a single-image post
at 15:00 local. No carousel handling (the ``clone_to_other_platforms``
step has already collapsed Sunday into a single illustration + canonical
caption).

This is a planner, not a bot. No likes, comments, follows, or DMs are
automated. The script only places pre-written, already-illustrated content
into the Threads native scheduler.

CLI mirrors ``instagram.schedule_instagram_posts``:

    python -m threads.schedule_threads_posts \
        [--week-start YYYY-MM-DD]
        [--date YYYYMMDD]
        [--all-wip]              # schedule every WIP-TH row, no date filter
        [--dry-run | --live]
        [--force]
        [--debug]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page

sys.path.append(str(Path(__file__).parent.parent.parent))
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
from planning.threads.threads_session import (  # noqa: E402
    ThreadsSession,
    configure_logger,
    load_notion_token,
    load_threads_config,
)

logger = logging.getLogger("threads_schedule")


# ---------- Row model / payload resolution ----------

from planning._wip_rows import (  # noqa: E402
    PostPayload,
    ScheduleRow,
    fetch_wip_rows as _fetch_wip_rows,
    resolve_payload as _resolve_payload,
)
from planning._failure import PostMayBeLiveError  # noqa: E402
from planning._scheduler_main import run_single_post_scheduler  # noqa: E402


def fetch_wip_th_rows(notion, db_id: str, ed_cols: dict, days: Optional[list[date]]) -> list[ScheduleRow]:
    return _fetch_wip_rows(notion, db_id, ed_cols, days, logger=logger)


def resolve_payload(notion, cfg: dict, row: ScheduleRow) -> PostPayload:
    return _resolve_payload(notion, cfg, row, platform_label="TH")


# ---------- Threads composer helpers ----------
#
# Shared with the video driver (``planning/videos/videos_threads.py``) —
# live in ``planning.threads.threads_composer``, not here (issue #324).


def return_to_profile(page: Page, feed_url: str) -> None:
    """Hard-refresh between days."""
    try:
        page.goto(feed_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
    except Exception as err:
        logger.warning("⚠️ Could not return to profile: %s", err)


# ---------- High-level per-day driver ----------

def schedule_post(
    session: ThreadsSession,
    cfg: dict,
    row: ScheduleRow,
    payload: PostPayload,
    *,
    dry_run: bool,
) -> str:
    page = session.page
    label = row.day_title

    open_composer(page)
    type_caption(page, payload.caption)
    upload_image(page, payload.image_path)
    open_three_dots_menu(page)
    click_schedule_menuitem(page)
    navigate_calendar_month(page, row.day)
    click_calendar_day(page, row.day)
    set_calendar_time(page, cfg["post_hour_local"], cfg["post_minute_local"])

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "threads"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-post-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info(
            "✅ DRY-RUN %s: calendar populated, screenshot → %s", label, shot
        )
        cancel_composer(page)
        # Discard prompt after Cancel.
        for _ in range(3):
            try:
                btn = page.get_by_role("button", name=DISCARD_BTN_RE)
                if btn.count():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                pass
        return "post:DRY-OK"

    click_calendar_done(page)

    # ---- point of no return (issue #235) ----
    # Past the final Schedule click a failure no longer proves the post was not
    # scheduled, so these raise PostMayBeLiveError and the row loop refuses to
    # retry them. See planning/_failure.py for the contract.
    try:
        click_final_schedule_action(page)
        if not wait_composer_closes(page, timeout_ms=25000):
            shot = out_dir / f"{label}-post-FAIL.png"
            page.screenshot(path=str(shot), full_page=False)
            raise RuntimeError(f"Composer did not close — see {shot}")
    except Exception as err:
        raise PostMayBeLiveError(str(err)) from err
    page.wait_for_timeout(1500)
    logger.info("✅ LIVE %s post scheduled on Threads", label)
    return "post:LIVE"


# ---------- Main ----------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule Threads content via /@profile composer.")
    parser.add_argument("--week-start", type=str, default=None,
                        help="Monday of the target week (YYYY-MM-DD). Default: next Monday.")
    parser.add_argument("--date", type=str, default=None,
                        help="Single-day mode (YYYYMMDD or YYYY-MM-DD). Overrides --week-start.")
    parser.add_argument("--all-wip", action="store_true",
                        help="Schedule every WIP-TH row in the editorial DB, no date filter "
                             "(supports multi-week planning runs).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Walk the flow up to Done; do NOT submit.")
    mode.add_argument("--live", action="store_true", help="Actually click Done + Schedule.")
    parser.add_argument("--force", action="store_true", help="Schedule even if link TH is already populated.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> tuple[int, list[dict]]:
    args = parse_args()
    configure_logger("threads_schedule", debug=args.debug)
    cfg = load_threads_config()
    return run_single_post_scheduler(
        args,
        platform="TH",
        log=logger,
        cfg=cfg,
        load_token=load_notion_token,
        session_cls=ThreadsSession,
        fetch_rows=fetch_wip_th_rows,
        resolve_payload=resolve_payload,
        schedule_post=schedule_post,
        cancel_composer=cancel_composer,
        return_home=return_to_profile,
    )


if __name__ == "__main__":
    raise SystemExit(main()[0])
