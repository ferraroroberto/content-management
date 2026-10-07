"""Schedule next week's Twitter (X) content via the native /home composer.

Reads rows where ``Work in Progress TW`` is checked, then for each in-scope
day drives the X composer at ``https://x.com/home`` to schedule a single-image
post at 15:00 local. There is no thread/carousel handling here (the
``clone_to_other_platforms`` step has already collapsed Sunday into a single
illustration + canonical caption).

This is a planner, not a bot. No likes, comments, follows, or DMs are
automated. The script only places pre-written, already-illustrated content
into the X native scheduler.

CLI mirrors ``instagram.schedule_instagram_posts``:

    python -m twitter.schedule_twitter_posts \
        [--week-start YYYY-MM-DD]
        [--date YYYYMMDD]
        [--all-wip]              # schedule every WIP-TW row, no date filter
        [--dry-run | --live]
        [--force]                # ignore link TW idempotency
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
from planning.twitter.twitter_composer import (  # noqa: E402
    cancel_composer,
    click_compose_area,
    click_confirm_in_modal,
    click_final_schedule_action,
    click_schedule_toolbar,
    dismiss_blocking_modals,
    set_schedule_modal,
    type_caption,
    upload_image,
    wait_composer_clears,
)
from planning.twitter.twitter_labels import CANCEL_CLOSE_BTN_RES  # noqa: E402
from planning.twitter.twitter_session import (  # noqa: E402
    TwitterSession,
    configure_logger,
    load_notion_token,
    load_twitter_config,
)
from planning._failure import PostMayBeLiveError  # noqa: E402
from planning._scheduler_main import (  # noqa: E402
    build_scheduler_parser,
    run_single_post_scheduler,
)

logger = logging.getLogger("twitter_schedule")

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


# ---------- Row model / payload resolution ----------

from planning._wip_rows import (  # noqa: E402
    PostPayload,
    ScheduleRow,
    fetch_wip_rows as _fetch_wip_rows,
    resolve_payload as _resolve_payload,
)


def fetch_wip_tw_rows(notion, db_id: str, ed_cols: dict, days: Optional[list[date]]) -> list[ScheduleRow]:
    return _fetch_wip_rows(notion, db_id, ed_cols, days, logger=logger)


def resolve_payload(notion, cfg: dict, row: ScheduleRow) -> PostPayload:
    """Build the (image path, caption) for the day's 15:00 X post."""
    return _resolve_payload(notion, cfg, row, platform_label="TW")


# ---------- X composer helpers ----------
#
# Shared with the video driver (``planning/videos/videos_twitter.py``) —
# live in ``planning.twitter.twitter_composer``, not here (issue #324).


def return_to_home(page: Page, feed_url: str) -> None:
    """Hard-refresh between days so the composer is in a clean state."""
    try:
        page.goto(feed_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        dismiss_blocking_modals(page)
        page.wait_for_timeout(400)
    except Exception as err:
        logger.warning("⚠️ Could not return to home: %s", err)


# ---------- High-level per-day driver ----------

def schedule_post(
    session: TwitterSession,
    cfg: dict,
    row: ScheduleRow,
    payload: PostPayload,
    *,
    dry_run: bool,
) -> str:
    """Open compose → caption → upload image → Schedule modal → Confirm →
    final Schedule.
    """
    page = session.page
    label = row.day_title

    click_compose_area(page)
    type_caption(page, payload.caption)
    upload_image(page, payload.image_path)
    click_schedule_toolbar(page)
    set_schedule_modal(
        page, row.day, cfg["post_hour_local"], cfg["post_minute_local"]
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "twitter"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-post-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info(
            "✅ DRY-RUN %s: composer + schedule modal ready, screenshot → %s",
            label, shot,
        )
        # Cancel the schedule modal AND the composer to leave clean state.
        for name_re in CANCEL_CLOSE_BTN_RES:
            try:
                btn = page.get_by_role("button", name=name_re)
                if btn.count():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                pass
        cancel_composer(page)
        return "post:DRY-OK"

    click_confirm_in_modal(page)

    # ---- point of no return (issue #235) ----
    # From the final Schedule click onward a failure no longer means "nothing
    # happened" — the post may be scheduled and we merely lost sight of it. So
    # everything below raises PostMayBeLiveError, which the row loop refuses to
    # retry. Losing one row to a manual re-run beats double-posting it.
    try:
        click_final_schedule_action(page)
        if not wait_composer_clears(page, timeout_ms=25000):
            shot = out_dir / f"{label}-post-FAIL.png"
            page.screenshot(path=str(shot), full_page=False)
            raise RuntimeError(f"Composer did not clear — see {shot}")
    except Exception as err:
        raise PostMayBeLiveError(str(err)) from err
    page.wait_for_timeout(1200)
    logger.info("✅ LIVE %s post scheduled on X", label)
    return "post:LIVE"


# ---------- Main ----------

def parse_args() -> argparse.Namespace:
    return build_scheduler_parser(
        "Schedule X (Twitter) content via /home composer.",
        all_wip_help=("Schedule every WIP-TW row in the editorial DB, no date filter "
                         "(supports multi-week planning runs)."),
        dry_run_help="Walk the flow up to Confirm; do NOT submit.",
        live_help="Actually click Confirm + Schedule.",
        force_help="Schedule even if link TW is already populated.",
    ).parse_args()


def main() -> tuple[int, list[dict]]:
    args = parse_args()
    configure_logger("twitter_schedule", debug=args.debug)
    cfg = load_twitter_config()
    def _after_login(page: Page) -> None:
        dismiss_blocking_modals(page)
        page.wait_for_timeout(400)

    return run_single_post_scheduler(
        args,
        platform="TW",
        log=logger,
        cfg=cfg,
        load_token=load_notion_token,
        session_cls=TwitterSession,
        fetch_rows=fetch_wip_tw_rows,
        resolve_payload=resolve_payload,
        schedule_post=schedule_post,
        cancel_composer=cancel_composer,
        return_home=return_to_home,
        after_login=_after_login,
    )


if __name__ == "__main__":
    raise SystemExit(main()[0])
