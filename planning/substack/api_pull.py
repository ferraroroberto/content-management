r"""Manual CLI — pull the publication's published posts into a local archive (P1).

Uses the native HTTP API (cookie auth). NOT part of the daily cron — run it by
hand when you want an archive snapshot.

Usage (from the repo root):
    & .\.venv\Scripts\python.exe -m planning.substack.api_pull [--limit N] [--with-body] [--out PATH]

    --limit N      how many recent posts to pull (default 50)
    --with-body    also fetch each post's full body_html (one extra GET per post)
    --out PATH     output JSON path (default results/substack/archive_<date>.json)
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from planning.substack.api_client import SubstackAPI
from planning.substack.substack_session import configure_logger, load_substack_config

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pull Substack posts into a local archive.")
    parser.add_argument("--limit", type=int, default=50, help="How many recent posts to pull.")
    parser.add_argument("--with-body", action="store_true", help="Also fetch full body_html per post.")
    parser.add_argument("--out", type=str, default=None, help="Output JSON path.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = configure_logger("substack_api_pull", debug=args.debug)
    cfg = load_substack_config()

    out_path = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "results" / "substack" / f"archive_{datetime.now():%Y%m%d}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("🚀 Substack archive pull — limit=%d with_body=%s", args.limit, args.with_body)
    api = SubstackAPI(publication_url=cfg.get("publish_url"))
    archive = api.build_archive(limit=args.limit, with_body=args.with_body)

    out_path.write_text(json.dumps(archive, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info("✅ Wrote %d posts → %s", len(archive), out_path)
    if archive:
        newest = archive[0]
        logger.info("   newest: %s (%s)", newest.get("title"), newest.get("post_date"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
