"""Newsletter-archive orchestrator: open Chrome tabs → Notion archive rows.

Two entrypoints:

* ``run_batch(write=True)`` — walk every non-skipped tab in your Chrome,
  classify + summarise + write each article, close the tab on success.
* ``archive.newsletter.dry_run`` (CLI) — single-tab variant for testing.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Console emojis crash on Windows' default cp1252; force UTF-8 stdio.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

from config.logger_config import setup_logger  # noqa: E402
from archive.newsletter import (  # noqa: E402
    author_resolver, chrome_tabs, classifier, extractor, notion_io, summarizer,
)
from archive.newsletter.cache import CacheState  # noqa: E402


def load_config() -> Dict[str, Any]:
    cfg_path = REPO_ROOT / "config" / "config.json"
    with cfg_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def process_url(
    *, url: str, page, archive_cfg: Dict[str, Any], client, cache: CacheState,
    write: bool, logger: logging.Logger,
) -> bool:
    """Process a single tab. Returns True if a page was (or would be) created."""
    if cache.find_article(url):
        logger.info("⏭️  Already in Notion (duplicate URL): %s", url)
        return False

    logger.info("📥 Extracting: %s", url)
    art = extractor.extract(page)
    logger.info("📝 Title: %s", art.title)
    logger.info("✍️  Byline (raw): %s", art.author or "<none>")
    logger.info("📄 Body length: %d chars", len(art.body_text))

    topic = classifier.classify(
        base_url=archive_cfg["llm_hub_base_url"],
        model=archive_cfg["llm_model"],
        title=art.title, body_text=art.body_text,
    )
    logger.info("🏷️  Topic: %s", topic)

    summary = summarizer.summarize(
        base_url=archive_cfg["llm_hub_base_url"],
        model=archive_cfg["llm_model"],
        title=art.title, body_text=art.body_text,
    )
    logger.info("📜 Summary:\n%s", summary)

    resolution = author_resolver.resolve(
        byline=art.author,
        title=art.title,
        body_text=art.body_text,
        cache=cache,
        fallback_name=archive_cfg.get("author_fallback_name", "not classified"),
        llm_base_url=archive_cfg["llm_hub_base_url"],
        llm_model=archive_cfg["llm_model"],
    )
    author_conn = resolution.connection

    if resolution.via == "byline-match":
        logger.info("👤 Byline matched DB: %s (%s)",
                    author_conn.name, author_conn.page_id)
    elif resolution.via == "byline-create":
        if write:
            author_conn = notion_io.create_connection(
                client, connections_db_id=archive_cfg["connections_db_id"],
                name=resolution.raw_byline, topic=topic, cache=cache,
            )
        else:
            logger.info("👤 [DRY-RUN] Would create new connection: %s (topic=%s)",
                        resolution.raw_byline, topic)
    elif resolution.via == "llm-match":
        logger.info("🤖→👤 LLM-picked author matched DB: %s (LLM said: %s)",
                    author_conn.name, resolution.llm_choice)
    elif resolution.via == "fallback":
        logger.info("↩️  Using fallback author '%s' (raw byline: %s, LLM said: %s)",
                    author_conn.name, resolution.raw_byline or "<none>",
                    resolution.llm_choice or "<none>")
    else:  # "none"
        logger.warning("👤 No author resolution + no fallback in DB — article saved without author")

    newsletter = notion_io.pick_newsletter(
        client,
        newsletter_db_id=archive_cfg["newsletter_db_id"],
        topic=topic,
        topic_to_rollup=archive_cfg["topic_to_rollup"],
        category_cap=archive_cfg["newsletter_category_cap"],
    )
    if not newsletter:
        logger.error("❌ No future newsletter has room for topic '%s' — stopping", topic)
        return False
    nl_number = (
        newsletter.get("properties", {}).get("number", {}).get("title", [{}])[0]
        .get("plain_text", "?")
    )
    logger.info("📰 Target newsletter: %s (%s)", nl_number, newsletter["id"])

    if not write:
        logger.info("🧪 [DRY-RUN] Would create article page now; skipping write")
        return True

    notion_io.create_article(
        client,
        articles_db_id=archive_cfg["articles_db_id"],
        title=art.title or url,
        link=url,
        summary=summary,
        topic=topic,
        author_id=(author_conn.page_id if author_conn else None),
        newsletter_id=newsletter["id"],
        body_text=art.body_text,
        cache=cache,
    )
    return True


def run_batch(*, write: bool, debug: bool = False) -> int:
    # Configure the package-root logger so children
    # (newsletter_archive.notion_io, .author_resolver, .classifier, ...) all
    # propagate handlers and land in the same log file.
    logger = setup_logger(
        "newsletter_archive", file_logging=True,
        level=logging.DEBUG if debug else logging.INFO,
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
        targets = [
            t for t in tabs
            if not chrome_tabs.should_skip(t.url, archive_cfg["skip_url_substrings"])
        ]
        logger.info("📑 Tabs total=%d; to process=%d", len(tabs), len(targets))

        for t in targets:
            logger.info("▶️  Tab: %s", t.url)
            try:
                created = process_url(
                    url=t.url, page=t.page, archive_cfg=archive_cfg,
                    client=client, cache=cache, write=write, logger=logger,
                )
                if write and created:
                    t.page.close()
                    logger.info("🗑️  Closed tab")
            except Exception:
                logger.exception("❌ Failed on tab %s — leaving it open", t.url)
    finally:
        chrome_tabs.close_browser(browser)

    logger.info("🎉 Done")
    return 0


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run the newsletter archive pipeline.")
    parser.add_argument("--live", action="store_true",
                        help="Write to Notion + close tabs (otherwise dry-run)")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    return run_batch(write=args.live, debug=args.debug)


if __name__ == "__main__":
    raise SystemExit(main())
