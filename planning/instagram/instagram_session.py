"""Playwright session manager for the Meta Business Suite content calendar.

Drives the Instagram + Facebook scheduling planner at
``https://business.facebook.com/latest/content_calendar``. Uses **real Chrome**
(channel="chrome") with a **dedicated, separate** user-data directory
configured under ``instagram.user_data_dir``. The user's regular Chrome
profile is never read or written by anything in this package.

Meta bounces an unauthenticated user to ``facebook.com/login.php`` or a
checkpoint page. Meta is also slower than the other platforms — the SPA fires
several XHRs before the calendar grid mounts — so this session keeps the base
class's longer default navigation timeout.

The persistent-context lifecycle, the ``_resolve_user_data_dir`` safety guard,
the failure-screenshot helper, the logger factory and ``LoginRequiredError``
all live in :mod:`planning._session_base`; this module only declares what is
specific to Meta.
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
    "InstagramSession",
    "LoginRequiredError",
    "configure_logger",
    "load_instagram_config",
    "load_clone_config",
    "load_notion_token",
    "_resolve_user_data_dir",
    "_user_data_dir_initialized",
]


def configure_logger(name: str = "instagram", debug: bool = False) -> logging.Logger:
    """Set up a logger using the project-wide pattern."""
    return _configure_logger(name, debug=debug)


def load_instagram_config() -> dict:
    """Load and return the `instagram` block from config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    block = cfg.get("instagram")
    if not block:
        raise RuntimeError("Missing 'instagram' block in config.json")
    return block


def load_clone_config() -> dict:
    """Load and return the `clone_ig_to_others` block from config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    block = cfg.get("clone_ig_to_others")
    if not block:
        raise RuntimeError("Missing 'clone_ig_to_others' block in config.json")
    return block


def load_notion_token() -> str:
    """Load Notion API token from config.json (reuses existing notion block)."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    token = cfg.get("notion", {}).get("api_token")
    if not token:
        raise RuntimeError("Missing 'notion.api_token' in config.json")
    return token


class InstagramSession(PlatformSession):
    """Persistent-context Meta (Instagram + Facebook) session — see :class:`PlatformSession`."""

    platform_name = "instagram"
    session_display = "Instagram (Meta planner)"
    default_timeout_ms = 45000
    login_markers = (
        "facebook.com/login",
        "/checkpoint",
        "/two_step_verification",
        "/recover/",
    )
