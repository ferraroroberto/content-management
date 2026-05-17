"""Thin layer over the existing Notion client helpers, scoped to the editorial DB.

Column names in the editorial database are never hardcoded — callers pass a
role→column map (from config) and we resolve role names like ``text_body`` or
``follower_count`` to actual Notion property names at call time.

Used by every per-platform scheduler (``substack/``, ``linkedin/``,
``twitter/``, ``threads/``, ``instagram/``). The helper is platform-agnostic;
each caller passes its own ``notion_columns`` map from ``config.json``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

from notion_client import Client

sys.path.append(str(Path(__file__).parent.parent.parent))
from reporting.notion import notion_update as _nu  # noqa: E402
from reporting.notion.notion_update import (  # noqa: E402
    extract_property_value,
    format_database_id,
    init_notion_client,
    prepare_notion_update,
)

# notion_update.py uses a module-level `logger` that only gets initialized when
# its own main() runs. When we reuse its helpers from elsewhere, configure that
# logger once on import so calls like init_notion_client / prepare_notion_update
# don't blow up on `logger.debug(...)`.
if _nu.logger is None:
    _nu.configure_logger(debug_mode=False)

logger = logging.getLogger("notion_editorial")


def _resolve_column(role: str, columns_map: dict) -> str:
    if role not in columns_map:
        raise KeyError(
            f"Role '{role}' not present in notion_columns map. "
            f"Available roles: {sorted(columns_map.keys())}"
        )
    return columns_map[role]


def get_row_by_day(notion: Client, db_id: str, day_yyyymmdd: str, columns_map: dict) -> Optional[dict]:
    """Find the editorial row whose title (``title_day`` role) equals ``day_yyyymmdd``."""
    title_col = _resolve_column("title_day", columns_map)
    formatted_db = format_database_id(db_id)
    logger.debug("🔍 Querying editorial DB for %s == %s", title_col, day_yyyymmdd)
    response = notion.databases.query(
        database_id=formatted_db,
        filter={"property": title_col, "title": {"equals": day_yyyymmdd}},
    )
    results = response.get("results", [])
    if not results:
        logger.warning("⚠️ No editorial row found for day=%s", day_yyyymmdd)
        return None
    if len(results) > 1:
        logger.warning("⚠️ Multiple editorial rows for %s — using the first", day_yyyymmdd)
    return results[0]


def query_rows_by_filter(notion: Client, db_id: str, filter_obj: dict) -> list[dict]:
    """Generic paginated query — returns all results (handles ``next_cursor``)."""
    formatted_db = format_database_id(db_id)
    results: list[dict] = []
    cursor: Optional[str] = None
    while True:
        kwargs: dict = {"database_id": formatted_db, "filter": filter_obj}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = notion.databases.query(**kwargs)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
        if not cursor:
            break
    return results


def get_field(row: dict, role: str, columns_map: dict) -> Any:
    """Read the value of a role-named column from a Notion row."""
    col = _resolve_column(role, columns_map)
    props = row.get("properties", {})
    if col not in props:
        logger.warning("⚠️ Column '%s' missing from row %s", col, row.get("id"))
        return None
    return extract_property_value(props[col])


def get_property_type(row: dict, role: str, columns_map: dict) -> str:
    """Return the Notion property type for a role-named column ('rich_text' by default)."""
    col = _resolve_column(role, columns_map)
    return row.get("properties", {}).get(col, {}).get("type", "rich_text")


def set_field(
    notion: Client,
    page_id: str,
    role: str,
    value: Any,
    columns_map: dict,
    property_type: str,
) -> Optional[dict]:
    """Write `value` to the page's role-named column using the existing payload helper."""
    col = _resolve_column(role, columns_map)
    payload = prepare_notion_update(property_type, value)
    if payload is None:
        logger.warning("⚠️ Nothing to write to %s (value=%r)", col, value)
        return None
    logger.info("📝 Notion update: page=%s field=%s (%s) value=%r", page_id, col, property_type, value)
    return notion.pages.update(page_id=page_id, properties={col: payload})


def retrieve_page(notion: Client, page_id: str) -> dict:
    """Fetch a full page (used to follow a relation into another DB)."""
    return notion.pages.retrieve(page_id=page_id)


__all__ = [
    "init_notion_client",
    "get_row_by_day",
    "query_rows_by_filter",
    "get_field",
    "get_property_type",
    "set_field",
    "retrieve_page",
]
