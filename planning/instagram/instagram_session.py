"""Playwright session manager for the Meta Business Suite content calendar.

Drives the Instagram + Facebook scheduling planner at
``https://business.facebook.com/latest/content_calendar``. Uses **real Chrome**
(channel="chrome") with a **dedicated, separate** user-data directory
configured under ``instagram.user_data_dir``. The user's regular Chrome
profile is never read or written by anything in this package.

Mirror of ``linkedin/linkedin_session.py`` — see that file for the rationale
behind real-Chrome + persistent profile + the stealth flags. The only
substantive difference is the login-detection heuristic: Meta bounces an
unauthenticated user to ``facebook.com/login.php`` or a checkpoint page.
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

logger = logging.getLogger("instagram_session")

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.json"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def configure_logger(name: str = "instagram", debug: bool = False) -> logging.Logger:
    """Set up a logger using the project-wide pattern."""
    level = logging.DEBUG if debug else logging.INFO
    return setup_logger(name, level=level, file_logging=True)


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


def _resolve_user_data_dir(rel_or_abs: str) -> Path:
    """Resolve `user_data_dir`; refuse paths that look like the user's real Chrome profile."""
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = REPO_ROOT / p
    resolved = p.resolve() if p.exists() else p
    danger_substrings = (
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
                "'instagram/chrome_user_data')."
            )
    return p


def _user_data_dir_initialized(user_data_dir: Path) -> bool:
    """A persistent-profile directory is 'ready' once Chrome has written its Default subdir."""
    return user_data_dir.exists() and (user_data_dir / "Default").exists()


class InstagramSession:
    """Persistent-context wrapper around real Chrome with a dedicated profile.

    Usage:
        with InstagramSession(cfg) as session:
            page = session.page
            session.goto_with_login_check(url)
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
            raise RuntimeError("Session not entered — use `with InstagramSession(cfg) as s:`")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Session not entered")
        return self._context

    def __enter__(self) -> "InstagramSession":
        if not _user_data_dir_initialized(self.user_data_dir):
            raise FileNotFoundError(
                f"Chrome profile at {self.user_data_dir} is empty or missing. "
                "Run `python -m instagram.bootstrap_session` first."
            )
        self._playwright = sync_playwright().start()
        self._context = launch_persistent_context_with_lock_wait(
            self._playwright, self.user_data_dir, headless=self.headless, logger=logger,
        )
        self._context.add_init_script(STEALTH_INIT_SCRIPT)
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        logger.info(
            "🌐 Instagram (Meta planner) session started (channel=chrome, headless=%s, profile=%s)",
            self.headless, self.user_data_dir,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            logger.info("👋 Instagram session closed")

    def goto_with_login_check(self, url: str, *, timeout_ms: int = 45000) -> None:
        """Navigate to `url`. Raise LoginRequiredError if Meta redirected to login.

        Meta is slower than LinkedIn and the planner SPA initialises several
        XHRs before the calendar grid mounts, so we use a longer default
        timeout and wait_until='domcontentloaded' rather than networkidle.
        """
        logger.debug("➡️ Navigating to %s", url)
        self.page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        current = (self.page.url or "").lower()
        login_markers = (
            "facebook.com/login",
            "/checkpoint",
            "/two_step_verification",
            "/recover/",
        )
        if any(m in current for m in login_markers):
            raise LoginRequiredError(
                "Meta redirected to login/checkpoint — the saved Chrome profile session expired. "
                "Re-run `python -m instagram.bootstrap_session` to log in again."
            )

    def screenshot_failure(self, label: str) -> Path:
        """Save a debug screenshot under results/instagram/."""
        out_dir = REPO_ROOT / "results" / "instagram"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"{ts}-{label}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            logger.warning("📸 Failure screenshot saved → %s", path)
        except Exception as err:
            logger.error("❌ Could not save failure screenshot: %s", err)
        return path


class LoginRequiredError(RuntimeError):
    """Raised when the saved Meta session is no longer valid."""
