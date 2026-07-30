"""Shared Notion client helpers.

Single-source for the helpers that were previously copy-pasted across
``notion_update.py``, ``notion_database_structure.py`` and
``notion_supabase_sync.py`` (and imported by ``editorial.py``): initializing
the Notion ``Client``, formatting a 32-char database id into hyphenated UUID
form, and converting a raw Notion property object into a plain Python value.

The logger is configured at import (mirroring ``supabase_uploader.py``) so
``init_notion_client`` never hits an uninitialized module-level logger,
regardless of which caller imports it.
"""

from typing import Any
import logging
import sys
from pathlib import Path

from notion_client import Client

# Add the repo root to sys.path to allow importing from sibling packages
sys.path.append(str(Path(__file__).parent.parent.parent))
from config.logger_config import setup_logger

# Set up logger - will use existing logger if available
logger = logging.getLogger("notion_client_helpers")
if not logger.handlers:
    # Only set up if no handlers exist (i.e., not already configured)
    logger = setup_logger("notion_client_helpers", file_logging=False)


def init_notion_client(api_token):
    """Initialize Notion Client using the provided API token."""
    logger.debug("🔑 Initializing Notion client")
    try:
        client = Client(auth=api_token)
        logger.info("✅ Notion client initialized successfully")
        return client
    except Exception as e:
        logger.error(f"❌ Error initializing Notion client: {e}")
        return None


def format_database_id(database_id):
    """Format database ID with hyphens if needed."""
    if len(database_id) == 32:
        # Insert hyphens to convert into UUID format
        return f"{database_id[:8]}-{database_id[8:12]}-{database_id[12:16]}-{database_id[16:20]}-{database_id[20:]}"
    return database_id


def _extract_formula_value(formula: dict) -> Any:
    """Extract value from a Notion ``formula`` property's inner object."""
    formula_type = formula.get("type")
    if formula_type == "string":
        return formula.get("string")
    elif formula_type == "number":
        return formula.get("number")
    elif formula_type == "boolean":
        return formula.get("boolean")
    elif formula_type == "date":
        date_obj = formula.get("date")
        return date_obj.get("start") if date_obj else None
    return None


def _extract_rollup_value(rollup: dict) -> Any:
    """Extract value from a Notion ``rollup`` property's inner object."""
    rollup_type = rollup.get("type")
    if rollup_type == "number":
        return rollup.get("number")
    elif rollup_type == "array":
        return [extract_property_value(item) for item in rollup.get("array", [])]
    return None


def extract_property_value(prop: dict) -> Any:
    """Convert a raw Notion property object into a plain Python value.

    Single-source for the property-value converter that had **drifted** into
    three independent implementations (``notion_update.py``,
    ``notion_database_structure.py``, ``notion_supabase_sync.py``) with
    different type coverage in each. This is the most complete of the three
    (adds ``rollup`` array recursion, ``email``/``phone_number``/``status``,
    full title/rich_text concatenation instead of first-segment-only, and a
    raw-dict JSONB-style fallback for unrecognized types instead of losing
    the value to ``None``) — every caller now goes through this one.
    """
    prop_type = prop.get("type")

    if prop_type == "title":
        texts = prop.get("title", [])
        return "".join(t.get("plain_text", "") for t in texts)
    elif prop_type == "rich_text":
        texts = prop.get("rich_text", [])
        return "".join(t.get("plain_text", "") for t in texts)
    elif prop_type == "number":
        return prop.get("number")
    elif prop_type == "select":
        select = prop.get("select")
        return select.get("name") if select else None
    elif prop_type == "multi_select":
        return [opt.get("name") for opt in prop.get("multi_select", [])]
    elif prop_type == "date":
        date_obj = prop.get("date")
        if date_obj:
            start_date = date_obj.get("start")
            return start_date if start_date else None
        return None
    elif prop_type == "checkbox":
        return prop.get("checkbox", False)
    elif prop_type == "url":
        url = prop.get("url")
        return url if url else None
    elif prop_type == "email":
        email = prop.get("email")
        return email if email else None
    elif prop_type == "phone_number":
        phone = prop.get("phone_number")
        return phone if phone else None
    elif prop_type == "formula":
        return _extract_formula_value(prop.get("formula", {}))
    elif prop_type == "relation":
        return [rel.get("id") for rel in prop.get("relation", [])]
    elif prop_type == "rollup":
        return _extract_rollup_value(prop.get("rollup", {}))
    elif prop_type == "people":
        return [person.get("id") for person in prop.get("people", [])]
    elif prop_type == "files":
        files = prop.get("files", [])
        return [f.get("file", {}).get("url") or f.get("external", {}).get("url") for f in files]
    elif prop_type == "created_time":
        created = prop.get("created_time")
        return created if created else None
    elif prop_type == "created_by":
        return prop.get("created_by", {}).get("id")
    elif prop_type == "last_edited_time":
        edited = prop.get("last_edited_time")
        return edited if edited else None
    elif prop_type == "last_edited_by":
        return prop.get("last_edited_by", {}).get("id")
    elif prop_type == "status":
        status = prop.get("status")
        return status.get("name") if status else None
    else:
        # Unknown/unhandled type — return the raw property object rather
        # than silently losing the value to None.
        return prop
