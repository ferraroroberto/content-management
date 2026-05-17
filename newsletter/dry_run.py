"""Single-article dry-run / live-run entrypoint.

Examples:
    python -m newsletter.dry_run --first-non-gmail-tab --no-write
    python -m newsletter.dry_run --single-url https://elenaverna.com/p/ic-work-is-the-new-career-flex
    python -m newsletter.dry_run --first-non-gmail-tab           # writes to Notion + closes tab
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Console emojis crash on Windows' default cp1252; force UTF-8 stdio.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from config.logger_config import setup_logger  # noqa: E402
from newsletter import chrome_tabs, notion_io  # noqa: E402
from newsletter.pipeline import load_config, process_url  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the newsletter archive against ONE tab.")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--first-non-gmail-tab", action="store_true",
                     help="Pick the first non-skipped tab from your Chrome (:9222)")
    src.add_argument("--single-url", type=str,
                     help="Process a single URL (exact or substring match on the open tab list)")
    parser.add_argument("--no-write", action="store_true",
                        help="Skip Notion writes; only log what would happen")
    parser.add_argument("--keep-tab", action="store_true",
                        help="Do not close the tab even on a successful write")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    # Use the package-root logger name so child module loggers
    # (newsletter_archive.notion_io, .author_resolver, ...) propagate
    # into the same file via standard Python logging inheritance.
    logger = setup_logger(
        "newsletter_archive", file_logging=True,
        level=logging.DEBUG if args.debug else logging.INFO,
    )

    cfg = load_config()
    if "newsletter_archive" not in cfg:
        logger.error("❌ config.json is missing the 'newsletter_archive' section")
        return 1
    archive_cfg = cfg["newsletter_archive"]
    client = notion_io.init_client(cfg["notion"]["api_token"])

    cache = notion_io.hydrate_cache(
        client,
        articles_db_id=archive_cfg["articles_db_id"],
        connections_db_id=archive_cfg["connections_db_id"],
        fuzzy_threshold=archive_cfg["fuzzy_author_threshold"],
    )

    browser = chrome_tabs.connect(archive_cfg["chrome_debug_port"])
    try:
        tabs = chrome_tabs.list_tabs(browser)
        candidates = [
            t for t in tabs
            if not chrome_tabs.should_skip(t.url, archive_cfg["skip_url_substrings"])
        ]
        if not candidates:
            logger.error("❌ No processable tabs (after filtering Gmail/Notion/etc.)")
            logger.info("All open tabs: %s", [t.url for t in tabs])
            return 2

        if args.single_url:
            chosen = next((t for t in candidates if t.url == args.single_url), None)
            if not chosen:
                chosen = next((t for t in candidates if args.single_url in t.url), None)
            if not chosen:
                logger.error("❌ No open tab matches: %s", args.single_url)
                logger.info("Candidate tabs: %s", [t.url for t in candidates])
                return 3
        else:
            chosen = candidates[0]

        logger.info("🎯 Chosen tab: %s", chosen.url)
        write = not args.no_write

        created = process_url(
            url=chosen.url, page=chosen.page, archive_cfg=archive_cfg,
            client=client, cache=cache, write=write, logger=logger,
        )

        if write and created and not args.keep_tab:
            chosen.page.close()
            logger.info("🗑️  Closed tab")
        elif args.keep_tab:
            logger.info("👁️  --keep-tab set; leaving tab open")
        elif not write:
            logger.info("🧪 --no-write set; tab left open")
    finally:
        chrome_tabs.close_browser(browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
