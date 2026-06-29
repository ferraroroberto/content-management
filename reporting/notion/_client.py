"""Shared Notion client helpers.

Single-source for the two helpers that were previously copy-pasted across
``notion_update.py`` and ``notion_database_structure.py`` (and imported by
``editorial.py``): initializing the Notion ``Client`` and formatting a
32-char database id into hyphenated UUID form.

The logger is configured at import (mirroring ``supabase_uploader.py``) so
``init_notion_client`` never hits an uninitialized module-level logger,
regardless of which caller imports it.
"""

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
