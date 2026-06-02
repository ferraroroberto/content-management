"""Playwright session manager for Threads.

Drives the Threads composer + native scheduler at
``https://www.threads.com/@ferraroroberto``. Uses **real Chrome**
(channel="chrome") with a **dedicated, separate** user-data directory
configured under ``threads.user_data_dir``. The user's regular Chrome
profile is never read or written by anything in this package.

Threads authenticates via Instagram, so login redirects may bounce through
``instagram.com/accounts/login`` as well as ``threads.com/login``.

The persistent-context lifecycle, the ``_resolve_user_data_dir`` safety guard,
the failure-screenshot helper, the logger factory and ``LoginRequiredError``
all live in :mod:`planning._session_base`; this module only declares what is
specific to Threads.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

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
    "ThreadsSession",
    "LoginRequiredError",
    "configure_logger",
    "load_threads_config",
    "load_notion_token",
    "_resolve_user_data_dir",
    "_user_data_dir_initialized",
]


def configure_logger(name: str = "threads", debug: bool = False) -> logging.Logger:
    """Set up a logger using the project-wide pattern."""
    return _configure_logger(name, debug=debug)


def load_threads_config() -> dict:
    """Load and return the `threads` block from config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    block = cfg.get("threads")
    if not block:
        raise RuntimeError("Missing 'threads' block in config.json")
    return block


def load_notion_token() -> str:
    """Load Notion API token from config.json (reuses existing notion block)."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    token = cfg.get("notion", {}).get("api_token")
    if not token:
        raise RuntimeError("Missing 'notion.api_token' in config.json")
    return token


class ThreadsSession(PlatformSession):
    """Persistent-context Threads session — see :class:`PlatformSession`."""

    platform_name = "threads"
    session_display = "Threads"
    default_timeout_ms = 45000
    login_markers = (
        "threads.com/login",
        "threads.net/login",
        "instagram.com/accounts/login",
        "/accounts/login",
    )
