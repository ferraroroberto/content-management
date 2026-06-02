"""Read-only preview for the 'next'-relation auto-fill.

Loads the editorial database id from config.json, looks up the row for the
target date and the row for the day after, and prints what a real run would
write. No Notion writes are performed.

Usage:
    python notion/next_relation_check.py            # uses today (local date)
    python notion/next_relation_check.py 20260513   # explicit YYYYMMDD or YYYY-MM-DD
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).parent.parent.parent))
from config.console import force_utf8_stdio  # noqa: E402
force_utf8_stdio()
from config.logger_config import setup_logger  # noqa: E402
from reporting.notion import notion_update as _nu
from reporting.notion.notion_update import (
    format_database_id,
    init_notion_client,
    parse_date,
)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.json"


def load_editorial_db() -> tuple[str, str]:
    """Return (api_token, editorial_database_id) from config.json."""
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    notion_cfg = cfg.get("notion", {})
    databases = notion_cfg.get("databases", [])
    if not databases:
        raise RuntimeError("No databases configured under notion.databases")
    return notion_cfg["api_token"], databases[0]["id"]


def query_row_by_date(notion, database_id: str, iso_date: str) -> Optional[dict]:
    """Query the editorial DB for the single row matching the `date` property."""
    resp = notion.databases.query(
        database_id=format_database_id(database_id),
        filter={"property": "date", "date": {"equals": iso_date}},
    )
    results = resp.get("results", [])
    if not results:
        return None
    if len(results) > 1:
        logger.warning(f"⚠️ Multiple rows for {iso_date}; using first")
    return results[0]


def extract_title(page: dict) -> str:
    """Return the title text of a Notion page (the `day` column)."""
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            arr = prop.get("title", [])
            return arr[0].get("plain_text", "") if arr else ""
    return ""


def extract_next_relation_id(page: dict) -> Optional[str]:
    """Return the page id currently in the `next` relation, or None."""
    next_prop = page.get("properties", {}).get("next", {})
    if next_prop.get("type") != "relation":
        return None
    rel = next_prop.get("relation", [])
    return rel[0].get("id") if rel else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target_date",
        nargs="?",
        default=None,
        help="Target date (YYYYMMDD or YYYY-MM-DD). Defaults to today.",
    )
    args = parser.parse_args()

    global logger
    logger = setup_logger("next_relation_check", file_logging=False, level=logging.INFO)
    _nu.configure_logger(debug_mode=False)

    target_str = args.target_date or date.today().strftime("%Y%m%d")
    today_dt: datetime = parse_date(target_str)
    tomorrow_dt = today_dt + timedelta(days=1)
    today_iso = today_dt.strftime("%Y-%m-%d")
    tomorrow_iso = tomorrow_dt.strftime("%Y-%m-%d")

    logger.info("🧪 Read-only preview — no writes will occur")
    logger.info(f"📅 Target (today)    : {today_iso}")
    logger.info(f"📅 Target (tomorrow) : {tomorrow_iso}")

    api_token, db_id = load_editorial_db()
    notion = init_notion_client(api_token)
    if notion is None:
        return 1

    today_row = query_row_by_date(notion, db_id, today_iso)
    tomorrow_row = query_row_by_date(notion, db_id, tomorrow_iso)

    if today_row is None:
        logger.error(f"❌ No row found for today ({today_iso}). Nothing to do.")
        return 2
    today_page_id = today_row["id"]
    today_title = extract_title(today_row)
    current_next_id = extract_next_relation_id(today_row)
    logger.info(f"📄 today's row       : page_id={today_page_id}  title='{today_title}'")
    logger.info(f"🔗 today's 'next'    : {current_next_id or '(empty)'}")

    if tomorrow_row is None:
        logger.warning(
            f"⚠️ No row exists yet for tomorrow ({tomorrow_iso}). "
            "Cannot set the relation — would skip."
        )
        return 0
    tomorrow_page_id = tomorrow_row["id"]
    tomorrow_title = extract_title(tomorrow_row)
    logger.info(f"📄 tomorrow's row    : page_id={tomorrow_page_id}  title='{tomorrow_title}'")

    logger.info("=" * 60)
    if current_next_id == tomorrow_page_id:
        logger.info("✅ Already set correctly — a real run would skip (no-op).")
    elif current_next_id is None:
        logger.info(f"➡️  Would set today.next.relation = [{tomorrow_page_id}]  ('{tomorrow_title}')")
    else:
        logger.info(
            f"➡️  Would replace today.next.relation: {current_next_id} → "
            f"{tomorrow_page_id}  ('{tomorrow_title}')"
        )
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
