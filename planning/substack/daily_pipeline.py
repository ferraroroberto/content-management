"""Daily Substack orchestrator: post the Note (image + optional video).

CLI:
    python -m substack.daily_pipeline [--date YYYYMMDD] [--dry-run]
                                      [--skip-post] [--force] [--debug]

Follower-count scraping was folded into
``reporting/scrape_client/substack.py::fetch_profile`` and now flows through
the standard reporting pipeline (``data_processor`` → ``profile_aggregator``
→ ``notion_update``) like every other platform's metrics.
"""

from __future__ import annotations

import argparse
import logging
import sys
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

logger = logging.getLogger("substack_daily_pipeline")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily Substack note pipeline.")
    parser.add_argument("--date", type=str, default=None, help="Target day (YYYYMMDD); defaults to today (local).")
    parser.add_argument("--dry-run", action="store_true", help="Compose Note but do not click Post.")
    parser.add_argument("--skip-post", action="store_true", help="Skip step 1 (publish Note).")
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
        "substack_session",
        "substack_notion_editorial",
        "videos_session",
    ):
        configure_logger(name, debug=args.debug)
    cfg = load_substack_config()
    target_day = normalize_day(args.date)

    logger.info("🚀 Substack daily pipeline — day=%s dry_run=%s", target_day, args.dry_run)

    rc_post = 0

    with SubstackSession(cfg) as session:
        if args.skip_post:
            logger.info("⏭️ Skipping step 1 (post Note).")
        else:
            # Substack publishes both flows independently on a video day:
            # the image/text Note writes to ``link SB`` and the video Note
            # writes to ``link SB(v)``. Each call has its own idempotency
            # check, so re-running is safe.
            try:
                rc_image = post_note(
                    cfg, target_day,
                    dry_run=args.dry_run,
                    force=args.force,
                    session=session,
                )
            except Exception as err:
                logger.exception("❌ Image Note branch raised: %s", err)
                rc_image = 99

            try:
                video_rc = post_video_note_if_applicable(
                    target_day,
                    dry_run=args.dry_run, force=args.force, session=session,
                )
            except Exception as err:
                logger.exception("❌ Video-day branch raised: %s", err)
                video_rc = 99

            if video_rc is None:
                logger.info("🖼️ Not a video day — only image Note ran (rc=%d).", rc_image)
                rc_post = rc_image
            else:
                logger.info("📹 Video day — image rc=%d, video rc=%d.", rc_image, video_rc)
                rc_post = rc_image or video_rc

    logger.info("📊 Pipeline result: post=%d", rc_post)
    return rc_post


if __name__ == "__main__":
    raise SystemExit(main())
