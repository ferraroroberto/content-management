"""Daily Substack orchestrator: post the Note, then scrape follower count.

CLI:
    python -m substack.daily_pipeline [--date YYYYMMDD] [--dry-run]
                                      [--skip-post] [--skip-followers]
                                      [--force] [--debug]

Both steps share a single browser session. A failure in step 1 does NOT abort
step 2 — the two data points are independent.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.substack.post_substack_note import post_note  # noqa: E402
from planning.substack.post_substack_video_note import post_video_note_if_applicable  # noqa: E402
from planning.substack.substack_session import (  # noqa: E402
    SubstackSession,
    configure_logger,
    load_substack_config,
    normalize_day,
)
from planning.substack.update_substack_followers import update_followers  # noqa: E402

logger = logging.getLogger("substack_daily_pipeline")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily Substack note + followers pipeline.")
    parser.add_argument("--date", type=str, default=None, help="Target day (YYYYMMDD); defaults to today (local).")
    parser.add_argument("--dry-run", action="store_true", help="Compose Note but do not click Post.")
    parser.add_argument("--skip-post", action="store_true", help="Skip step 1 (publish Note).")
    parser.add_argument("--skip-followers", action="store_true", help="Skip step 2 (scrape followers).")
    parser.add_argument("--force", action="store_true", help="Re-post even if post_url already filled.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # Configure every substack-module logger up front so child-step output
    # propagates when called from inside the orchestrator (not just as CLIs).
    for name in (
        "substack_daily_pipeline",
        "substack_post_note",
        "substack_post_video_note",
        "substack_update_followers",
        "substack_session",
        "substack_notion_editorial",
        "videos_session",
    ):
        configure_logger(name, debug=args.debug)
    cfg = load_substack_config()
    target_day = normalize_day(args.date)

    logger.info("🚀 Substack daily pipeline — day=%s dry_run=%s", target_day, args.dry_run)

    rc_post = 0
    rc_followers = 0

    with SubstackSession(cfg) as session:
        if args.skip_post:
            logger.info("⏭️ Skipping step 1 (post Note).")
        else:
            # On weekly-video days the editorial row's ``Work in Progress
            # Video`` checkbox is set AND ``clip SB(v)`` is populated. In
            # that case the daily Note is REPLACED by a video Note (same
            # composer, video toolbar icon, .mp4 from the clip's
            # clipPC/filePC). The image Note is paused for the day.
            try:
                video_rc = post_video_note_if_applicable(
                    target_day,
                    dry_run=args.dry_run, force=args.force, session=session,
                )
            except Exception as err:
                logger.exception("❌ Video-day branch raised: %s", err)
                video_rc = 99

            if video_rc is not None:
                logger.info("📹 Video day handled by video-note branch (rc=%d). Skipping image Note.", video_rc)
                rc_post = video_rc
            else:
                try:
                    rc_post = post_note(
                        cfg, target_day,
                        dry_run=args.dry_run,
                        force=args.force,
                        session=session,
                    )
                except Exception as err:
                    logger.exception("❌ Step 1 raised: %s", err)
                    rc_post = 99

        if args.skip_followers:
            logger.info("⏭️ Skipping step 2 (followers).")
        else:
            try:
                rc_followers = update_followers(cfg, target_day, session=session)
            except Exception as err:
                logger.exception("❌ Step 2 raised: %s", err)
                rc_followers = 99

    logger.info("📊 Pipeline result: post=%d followers=%d", rc_post, rc_followers)
    return rc_post or rc_followers


if __name__ == "__main__":
    raise SystemExit(main())
