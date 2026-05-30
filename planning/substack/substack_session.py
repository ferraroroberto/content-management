"""Playwright session manager for Substack automation.

Uses **real Chrome** (channel="chrome") with a **dedicated, separate**
user-data directory configured under ``substack.user_data_dir``. This profile
is created by the bootstrap script and is completely independent of the user's
regular Chrome installation:

* Default location is ``substack/chrome_user_data/`` inside this repo
  (gitignored).
* The user's normal Chrome profile at e.g.
  ``%LOCALAPPDATA%\\Google\\Chrome\\User Data`` is never opened, read, or
  written by anything in this package.

Why real Chrome + persistent profile: Playwright's bundled Chromium is
trivially fingerprinted by reCAPTCHA, which blocks the sign-in flow. Real
Chrome with a stable profile presents a normal browser environment so the
human-driven login at bootstrap actually completes.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

sys.path.append(str(Path(__file__).parent.parent.parent))
from config.chrome_launch import STEALTH_INIT_SCRIPT  # noqa: E402
from config.chrome_profile_lock import launch_persistent_context_with_lock_wait  # noqa: E402
from config.logger_config import setup_logger  # noqa: E402

logger = logging.getLogger("substack_session")

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.json"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


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


def _force_utf8_stdout() -> None:
    """Force stdout/stderr to UTF-8 so emoji-laden log lines don't crash on Windows cp1252 consoles."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def configure_logger(name: str = "substack", debug: bool = False) -> logging.Logger:
    """Set up a logger using the project-wide pattern."""
    _force_utf8_stdout()
    level = logging.DEBUG if debug else logging.INFO
    return setup_logger(name, level=level, file_logging=True)


def load_substack_config() -> dict:
    """Load and return the `substack` block from config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    block = cfg.get("substack")
    if not block:
        raise RuntimeError("Missing 'substack' block in config.json")
    return block


def load_notion_token() -> str:
    """Load Notion API token from config.json (reuses existing notion block)."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    token = cfg.get("notion", {}).get("api_token")
    if not token:
        raise RuntimeError("Missing 'notion.api_token' in config.json")
    return token


def _resolve_user_data_dir(rel_or_abs: str) -> Path:
    """Resolve `user_data_dir`; refuse paths that look like the user's real Chrome profile."""
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = REPO_ROOT / p
    resolved = p.resolve() if p.exists() else p
    danger_substrings = (
        # Common real-Chrome profile locations across OSes — guard against
        # accidental config that would point at the user's real profile.
        "Google/Chrome/User Data",
        "Google\\Chrome\\User Data",
        "Chromium/User Data",
        "Chromium\\User Data",
        "Library/Application Support/Google/Chrome",
        ".config/google-chrome",
    )
    as_str = str(resolved).replace("\\", "/")
    for marker in danger_substrings:
        if marker.replace("\\", "/") in as_str:
            raise RuntimeError(
                f"Refusing to use user_data_dir={resolved!r} — it looks like the "
                "main Chrome profile. Pick a dedicated directory (e.g. "
                "'substack/chrome_user_data')."
            )
    return p


def _user_data_dir_initialized(user_data_dir: Path) -> bool:
    """A persistent-profile directory is 'ready' once Chrome has written its Default subdir."""
    return user_data_dir.exists() and (user_data_dir / "Default").exists()


class SubstackSession:
    """Persistent-context wrapper around real Chrome with a dedicated profile.

    Usage:
        with SubstackSession(cfg) as session:
            page = session.page
            session.goto_with_login_check(url)

    No browser binary download is required — Playwright drives the user's
    already-installed Chrome via ``channel="chrome"``.
    """

    def __init__(self, cfg: dict, *, headless: Optional[bool] = None):
        self.cfg = cfg
        self.user_data_dir = _resolve_user_data_dir(cfg["user_data_dir"])
        self.headless = cfg.get("headless", False) if headless is None else headless
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Session not entered — use `with SubstackSession(cfg) as s:`")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Session not entered")
        return self._context

    def __enter__(self) -> "SubstackSession":
        if not _user_data_dir_initialized(self.user_data_dir):
            raise FileNotFoundError(
                f"Chrome profile at {self.user_data_dir} is empty or missing. "
                "Run `python -m substack.bootstrap_session` first."
            )
        self._playwright = sync_playwright().start()
        self._context = launch_persistent_context_with_lock_wait(
            self._playwright, self.user_data_dir, headless=self.headless, logger=logger,
        )
        self._context.add_init_script(STEALTH_INIT_SCRIPT)
        # `launch_persistent_context` opens one default page already.
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        logger.info(
            "🌐 Substack session started (channel=chrome, headless=%s, profile=%s)",
            self.headless, self.user_data_dir,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Persistent profile is written to disk automatically; just close cleanly.
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            logger.info("👋 Substack session closed")

    def goto_with_login_check(self, url: str, *, timeout_ms: int = 30000) -> None:
        """Navigate to `url`. Raise LoginRequiredError if Substack redirected to sign-in."""
        logger.debug("➡️ Navigating to %s", url)
        self.page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        current = self.page.url or ""
        if "sign-in" in current.lower():
            raise LoginRequiredError(
                "Substack redirected to sign-in — the saved Chrome profile session expired. "
                "Re-run `python -m substack.bootstrap_session` to log in again."
            )

    def screenshot_failure(self, label: str) -> Path:
        """Save a debug screenshot under results/substack/."""
        out_dir = REPO_ROOT / "results" / "substack"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"{ts}-{label}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            logger.warning("📸 Failure screenshot saved → %s", path)
        except Exception as err:  # pragma: no cover
            logger.error("❌ Could not save failure screenshot: %s", err)
        return path


class LoginRequiredError(RuntimeError):
    """Raised when the saved Substack session is no longer valid."""
