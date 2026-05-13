"""Scrape Substack total-followers count and write it to the Notion editorial row.

CLI:
    python -m substack.update_substack_followers [--date YYYYMMDD] [--debug]

Always overwrites the editorial row's ``follower_count`` column.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).parent.parent))
from substack.notion_editorial import (  # noqa: E402
    get_property_type,
    get_row_by_day,
    init_notion_client,
    set_field,
)
from substack.substack_session import (  # noqa: E402
    LoginRequiredError,
    SubstackSession,
    configure_logger,
    load_notion_token,
    load_substack_config,
    normalize_day,
)

logger = logging.getLogger("substack_update_followers")

# Match e.g. "Total followers (12,345)" or "Total followers (1234)"
TOTAL_FOLLOWERS_RE = re.compile(r"Total followers\s*\(([\d,]+)\)", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Substack total followers into Notion editorial.")
    parser.add_argument("--date", type=str, default=None, help="Target day (YYYYMMDD); defaults to today (local).")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def _extract_total_followers(page) -> Optional[int]:
    """Find a heading whose text matches the 'Total followers (N)' pattern."""
    # Wait for the stats page to render any element containing "Total followers".
    try:
        page.get_by_text(re.compile(r"Total followers\s*\(", re.IGNORECASE)).first.wait_for(
            state="visible", timeout=20000
        )
    except Exception as err:
        logger.warning("⚠️ 'Total followers (…)' element did not appear: %s", err)
        return None

    # Read all candidate texts and apply the regex to be robust to layout changes.
    candidates = page.get_by_text(re.compile(r"Total followers\s*\(", re.IGNORECASE)).all()
    for el in candidates:
        try:
            text = el.inner_text(timeout=2000)
        except Exception:
            continue
        m = TOTAL_FOLLOWERS_RE.search(text or "")
        if m:
            raw = m.group(1).replace(",", "")
            try:
                return int(raw)
            except ValueError:
                continue
    return None


def update_followers(
    cfg: dict,
    target_day: str,
    *,
    session: Optional[SubstackSession] = None,
) -> int:
    notion = init_notion_client(load_notion_token())
    if notion is None:
        logger.error("❌ Could not initialize Notion client.")
        return 3

    columns = cfg["notion_columns"]
    row = get_row_by_day(notion, cfg["editorial_db_id"], target_day, columns)
    if row is None:
        logger.error("❌ No editorial row for day=%s — aborting.", target_day)
        return 4

    page_id = row["id"]

    owned_session = session is None
    s = session or SubstackSession(cfg)
    if owned_session:
        s.__enter__()

    try:
        try:
            s.goto_with_login_check(cfg["stats_audience_url"])
        except LoginRequiredError as err:
            logger.error("❌ %s", err)
            return 6

        logger.debug("📍 After navigation, page URL is: %s", s.page.url)
        logger.debug("📑 Page title: %s", s.page.title())
        count = _extract_total_followers(s.page)
        if count is None:
            s.screenshot_failure(f"{target_day}-followers-not-found")
            logger.error("❌ Could not parse total followers from stats page.")
            return 11

        logger.info("👥 Total followers: %d", count)
        prop_type = get_property_type(row, "follower_count", columns)
        set_field(notion, page_id, "follower_count", count, columns, prop_type)
        logger.info("✅ Wrote follower_count=%d to Notion editorial row.", count)
        return 0
    finally:
        if owned_session:
            s.__exit__(None, None, None)


def main() -> int:
    args = parse_args()
    configure_logger("substack_update_followers", debug=args.debug)
    cfg = load_substack_config()
    target_day = normalize_day(args.date)
    logger.info("🚀 Substack followers scrape — day=%s", target_day)
    return update_followers(cfg, target_day)


if __name__ == "__main__":
    raise SystemExit(main())
