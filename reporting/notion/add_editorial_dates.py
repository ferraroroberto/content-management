r"""Seed missing editorial-calendar day-rows in the Notion editorial database.

For every day in a date range, create the editorial row if it does not already
exist — writing the ``day`` title (``YYYYMMDD``), the ``date`` property
(``YYYY-MM-DD``) and the ``DoW`` lowercase-weekday rich-text. The run is
idempotent: only missing dates are created, so re-running is a no-op.

Default range is the **current calendar month start → next calendar month end**,
i.e. the rows the upcoming planning/reporting runs will need. This is the spine
the rest of the pipelines read from (see ``reporting/notion/editorial.py``).

CLI-compatible — runnable standalone and from the control-panel app's
📅 editorial tab through one code path::

    & .\.venv\Scripts\python.exe -m reporting.notion.add_editorial_dates
    & .\.venv\Scripts\python.exe -m reporting.notion.add_editorial_dates --debug
    & .\.venv\Scripts\python.exe -m reporting.notion.add_editorial_dates --database-id <id>

Authenticates and resolves the editorial DB from this repo's ``config.json``
(``notion.api_token`` + ``notion.databases``), reusing the existing
``reporting/notion`` helpers rather than re-implementing client setup.
"""

from __future__ import annotations

import argparse
import logging
import sys
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Importing editorial configures notion_update's module-level logger on import,
# so the reused init_notion_client / query helpers below don't blow up.
from config.loader import load_full_config  # noqa: E402
from reporting.notion import notion_update as _nu  # noqa: E402
from reporting.notion._client import (  # noqa: E402
    format_database_id,
    init_notion_client,
)
from reporting.notion.editorial import query_rows_by_filter  # noqa: E402

logger = logging.getLogger("add_editorial_dates")

# Editorial-DB property names are structural; defaults match the live DB and can
# be overridden via ``notion.editorial_date_columns`` in config.json (per the
# repo's no-hardcoded-column-names convention).
DEFAULT_COLUMNS = {"title_day": "day", "date": "date", "day_of_week": "DoW"}

_DATE_FMT = "%Y-%m-%d"      # ISO 8601 for the Notion date property
_DAY_TEXT_FMT = "%Y%m%d"    # YYYYMMDD title text
_DOW_FMT = "%a"             # short weekday name (lowercased)


def setup_logging(debug: bool = False) -> None:
    """Configure stdout logging (UTF-8) for both this module and the reused helpers."""
    sys.stdout.reconfigure(encoding="utf-8")
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # init_notion_client / format_database_id log via notion_update's own logger.
    _nu.configure_logger(debug_mode=debug)


def editorial_date_range(now: Optional[datetime] = None) -> tuple[str, str]:
    """First day of the current calendar month → last day of the next month (YYYY-MM-DD)."""
    today = now or datetime.now()
    start = datetime(today.year, today.month, 1)
    if today.month == 12:
        end_year, end_month = today.year + 1, 1
    else:
        end_year, end_month = today.year, today.month + 1
    last_day = monthrange(end_year, end_month)[1]
    end = datetime(end_year, end_month, last_day)
    return start.strftime(_DATE_FMT), end.strftime(_DATE_FMT)


def load_config(database_id_override: Optional[str] = None) -> tuple[str, str, dict]:
    """Return ``(api_token, database_id, columns)`` from config.json.

    The editorial DB is the first entry in ``notion.databases`` (matching
    ``notion_update.py``), unless ``database_id_override`` is given.
    """
    cfg = load_full_config()
    notion_cfg = cfg.get("notion", {})

    api_token = notion_cfg.get("api_token")
    if not api_token:
        raise ValueError("notion.api_token missing from config.json")

    if database_id_override:
        database_id = database_id_override
    else:
        databases = notion_cfg.get("databases", [])
        if not databases:
            raise ValueError("notion.databases is empty in config.json")
        database_id = databases[0]["id"]

    columns = {**DEFAULT_COLUMNS, **notion_cfg.get("editorial_date_columns", {})}
    return api_token, database_id, columns


def get_existing_dates(notion, database_id: str, start_date: str, end_date: str, date_col: str) -> set[str]:
    """Return the set of ``YYYY-MM-DD`` dates already present in the range."""
    date_filter = {
        "and": [
            {"property": date_col, "date": {"on_or_after": start_date}},
            {"property": date_col, "date": {"on_or_before": end_date}},
        ]
    }
    rows = query_rows_by_filter(notion, database_id, date_filter)
    existing: set[str] = set()
    for row in rows:
        date_prop = row.get("properties", {}).get(date_col, {}).get("date")
        if date_prop and date_prop.get("start"):
            value = date_prop["start"]
            if "T" in value:
                value = value.split("T")[0]
            existing.add(value)
    return existing


def add_date_record(notion, database_id: str, day_text: str, date_str: str, day_of_week: str, columns: dict) -> Optional[dict]:
    """Create one editorial row; returns the created page or ``None`` on error."""
    try:
        return notion.pages.create(
            parent={"database_id": format_database_id(database_id)},
            properties={
                columns["title_day"]: {"title": [{"text": {"content": day_text}}]},
                columns["date"]: {"date": {"start": date_str}},
                columns["day_of_week"]: {"rich_text": [{"text": {"content": day_of_week}}]},
            },
        )
    except Exception as exc:  # noqa: BLE001 — log and continue past a single bad row
        logger.error("❌ Error adding record for %s: %s", date_str, exc)
        return None


def add_missing_dates(notion, database_id: str, start_date: str, end_date: str, columns: dict) -> int:
    """Create every missing day-row in ``[start_date, end_date]``. Returns the count added."""
    start = datetime.strptime(start_date, _DATE_FMT)
    end = datetime.strptime(end_date, _DATE_FMT)
    logger.info("📅 Range: %s → %s", start_date, end_date)

    existing = get_existing_dates(notion, database_id, start_date, end_date, columns["date"])
    logger.info("📊 Found %d existing date(s) in range", len(existing))

    added = 0
    current = start
    while current <= end:
        date_str = current.strftime(_DATE_FMT)
        if date_str not in existing:
            day_text = current.strftime(_DAY_TEXT_FMT)
            day_of_week = current.strftime(_DOW_FMT).lower()
            entry = add_date_record(notion, database_id, day_text, date_str, day_of_week, columns)
            if entry:
                logger.info("📝 Added: day=%s, date=%s, DoW=%s", day_text, date_str, day_of_week)
                added += 1
        else:
            logger.debug("Skipping existing date: %s", date_str)
        current += timedelta(days=1)

    logger.info("✅ Total new records added: %d", added)
    return added


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed missing editorial-calendar rows for the current + next calendar month."
    )
    parser.add_argument("--database-id", type=str, default=None, help="Override the editorial DB ID from config.json")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    setup_logging(args.debug)
    logger.info("🚀 Editorial DB sync starting (current month + next month)")

    try:
        api_token, database_id, columns = load_config(args.database_id)
        notion = init_notion_client(api_token)
        if notion is None:
            logger.error("❌ Failed to initialize Notion client")
            sys.exit(1)
        start_date, end_date = editorial_date_range()
        add_missing_dates(notion, database_id, start_date, end_date, columns)
    except Exception as exc:  # noqa: BLE001
        logger.error("❌ Fatal error: %s", exc)
        if args.debug:
            logger.exception("Full traceback:")
        sys.exit(1)


if __name__ == "__main__":
    main()
