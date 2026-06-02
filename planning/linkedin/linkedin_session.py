"""Playwright session manager for LinkedIn automation.

Uses **real Chrome** (channel="chrome") with a **dedicated, separate**
user-data directory configured under ``linkedin.user_data_dir``. This profile
is created by the bootstrap script and is completely independent of the user's
regular Chrome installation — the user's normal Chrome profile is never
opened, read, or written by anything in this package.

Why real Chrome + persistent profile: Playwright's bundled Chromium is
trivially fingerprinted by LinkedIn anti-bot checks, which can block sign-in
and the post composer. Real Chrome with a stable profile presents a normal
browser environment so the human-driven login at bootstrap actually
completes, and subsequent automated runs reuse the same session.

The persistent-context lifecycle, the ``_resolve_user_data_dir`` safety guard,
the failure-screenshot helper, the logger factory and ``LoginRequiredError``
all live in :mod:`planning._session_base`; this module only declares what is
specific to LinkedIn.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning._session_base import (  # noqa: E402
    LoginRequiredError,
    PlatformSession,
    _resolve_user_data_dir,
    _user_data_dir_initialized,
)
from planning._session_base import configure_logger as _configure_logger  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.json"

__all__ = [
    "LinkedInSession",
    "LoginRequiredError",
    "configure_logger",
    "normalize_day",
    "load_linkedin_config",
    "load_notion_token",
    "_resolve_user_data_dir",
    "_user_data_dir_initialized",
]


def normalize_day(date_str: Optional[str]) -> str:
    """Accept 'YYYYMMDD' or 'YYYY-MM-DD' (or None) and return 'YYYYMMDD'."""
    if not date_str:
        return datetime.now().strftime("%Y%m%d")
    s = date_str.strip()
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y%m%d")
    return datetime.strptime(s, "%Y%m%d").strftime("%Y%m%d")


def configure_logger(name: str = "linkedin", debug: bool = False) -> logging.Logger:
    """Set up a logger using the project-wide pattern."""
    return _configure_logger(name, debug=debug)


def load_linkedin_config() -> dict:
    """Load and return the `linkedin` block from config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    block = cfg.get("linkedin")
    if not block:
        raise RuntimeError("Missing 'linkedin' block in config.json")
    return block


def load_notion_token() -> str:
    """Load Notion API token from config.json (reuses existing notion block)."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    token = cfg.get("notion", {}).get("api_token")
    if not token:
        raise RuntimeError("Missing 'notion.api_token' in config.json")
    return token


class LinkedInSession(PlatformSession):
    """Persistent-context LinkedIn session — see :class:`PlatformSession`."""

    platform_name = "linkedin"
    session_display = "LinkedIn"
    default_timeout_ms = 30000
    # LinkedIn bounces unauthenticated users to /login, /uas/login, or /checkpoint.
    login_markers = ("/login", "/uas/login", "/checkpoint/lg/login")
