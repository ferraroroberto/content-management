"""Playwright session manager for Substack automation.

Uses **real Chrome** (channel="chrome") with a **dedicated, separate**
user-data directory configured under ``substack.user_data_dir``. This profile
is created by the bootstrap script and is completely independent of the user's
regular Chrome installation — the user's normal Chrome profile is never
opened, read, or written by anything in this package.

Why real Chrome + persistent profile: Playwright's bundled Chromium is
trivially fingerprinted by reCAPTCHA, which blocks the sign-in flow. Real
Chrome with a stable profile presents a normal browser environment so the
human-driven login at bootstrap actually completes. No browser binary
download is required — Playwright drives the user's already-installed Chrome
via ``channel="chrome"``.

The persistent-context lifecycle, the ``_resolve_user_data_dir`` safety guard,
the failure-screenshot helper, the logger factory and ``LoginRequiredError``
all live in :mod:`planning._session_base`; this module only declares what is
specific to Substack.
"""

from __future__ import annotations

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
    load_config_block,
    load_notion_token,
)
from planning._session_base import configure_logger as _configure_logger  # noqa: E402

__all__ = [
    "SubstackSession",
    "LoginRequiredError",
    "configure_logger",
    "normalize_day",
    "load_substack_config",
    "load_notion_token",
    "_resolve_user_data_dir",
    "_user_data_dir_initialized",
]


def normalize_day(date_str: Optional[str]) -> str:
    """Accept 'YYYYMMDD' or 'YYYY-MM-DD' (or None) and return 'YYYYMMDD'.

    The Notion editorial title is stored as YYYYMMDD; init.py passes dates in
    YYYY-MM-DD; CLI users typically pass YYYYMMDD. We normalize at the entry.
    """
    if not date_str:
        return datetime.now().strftime("%Y%m%d")
    s = date_str.strip()
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y%m%d")
    return datetime.strptime(s, "%Y%m%d").strftime("%Y%m%d")


def configure_logger(name: str = "substack", debug: bool = False) -> logging.Logger:
    """Set up a logger using the project-wide pattern."""
    return _configure_logger(name, debug=debug)


def load_substack_config() -> dict:
    """Load and return the `substack` block from config.json."""
    return load_config_block("substack")


class SubstackSession(PlatformSession):
    """Persistent-context Substack session — see :class:`PlatformSession`."""

    platform_name = "substack"
    session_display = "Substack"
    default_timeout_ms = 30000
    login_markers = ("sign-in",)
