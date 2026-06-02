"""Shared Playwright session base for every per-platform planning session.

Each ``planning/<P>/<P>_session.py`` module drives a single social platform
through **real Chrome** (``channel="chrome"``) with a **dedicated, separate**
user-data directory. The lifecycle (persistent-context ``__enter__`` /
``__exit__``), the safety guard that refuses to point at the user's real
Chrome profile (:func:`_resolve_user_data_dir`), the readiness check, the
failure-screenshot helper, the logger factory, and :class:`LoginRequiredError`
are all identical across platforms — they live here, once.

A platform module subclasses :class:`PlatformSession` and declares only what
actually differs:

* ``platform_name`` — short key (e.g. ``"twitter"``); names the results
  subdir, the bootstrap module, and the default logger.
* ``login_markers`` — substrings that, if present in the post-navigation URL,
  mean the saved session expired.
* ``session_display`` — human label used in the start/close log lines
  (defaults to ``platform_name.title()``).
* ``default_timeout_ms`` — per-platform navigation timeout.

Security note: ``_resolve_user_data_dir`` is the single guard protecting the
user's real Chrome profile from being opened by automation. It must stay
single-source here — never re-inline it in a platform module.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from config.chrome_launch import STEALTH_INIT_SCRIPT
from config.chrome_profile_lock import launch_persistent_context_with_lock_wait
from config.logger_config import setup_logger

# Repo root is two levels up from this file: planning/_session_base.py -> repo.
REPO_ROOT = Path(__file__).resolve().parent.parent


class LoginRequiredError(RuntimeError):
    """Raised when a saved platform session is no longer valid.

    Single-source for every platform — modules re-export this name so existing
    ``from planning.<P>.<P>_session import LoginRequiredError`` imports and
    ``except LoginRequiredError`` handlers keep working unchanged.
    """


def configure_logger(name: str, debug: bool = False) -> logging.Logger:
    """Set up a logger using the project-wide pattern."""
    level = logging.DEBUG if debug else logging.INFO
    return setup_logger(name, level=level, file_logging=True)


def _resolve_user_data_dir(rel_or_abs: str, *, example_subdir: str = "<platform>/chrome_user_data") -> Path:
    """Resolve ``user_data_dir``; refuse paths that look like the user's real Chrome profile.

    SECURITY-relevant: this is the one guard that stops a mis-configured
    ``user_data_dir`` from opening (and corrupting) the user's real Chrome
    profile. Single-source — do not re-inline.
    """
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
                f"'{example_subdir}')."
            )
    return p


def _user_data_dir_initialized(user_data_dir: Path) -> bool:
    """A persistent-profile directory is 'ready' once Chrome has written its Default subdir."""
    return user_data_dir.exists() and (user_data_dir / "Default").exists()


class PlatformSession:
    """Persistent-context wrapper around real Chrome with a dedicated profile.

    Subclasses declare ``platform_name``, ``login_markers`` and (optionally)
    ``session_display`` / ``default_timeout_ms``. Usage:

        with TwitterSession(cfg) as session:
            page = session.page
            session.goto_with_login_check(url)
    """

    #: Short platform key — names the results subdir, bootstrap module, logger.
    platform_name: str = ""
    #: URL substrings that mean the saved session expired (lowercased compare).
    login_markers: Sequence[str] = ()
    #: Human label used in the start/close log lines.
    session_display: Optional[str] = None
    #: Default navigation timeout in ms for ``goto_with_login_check``.
    default_timeout_ms: int = 45000

    def __init__(self, cfg: dict, *, headless: Optional[bool] = None):
        if not self.platform_name:
            raise NotImplementedError("Subclass must set `platform_name`.")
        self.cfg = cfg
        self.user_data_dir = _resolve_user_data_dir(
            cfg["user_data_dir"],
            example_subdir=f"{self.platform_name}/chrome_user_data",
        )
        self.headless = cfg.get("headless", False) if headless is None else headless
        self.logger = logging.getLogger(f"{self.platform_name}_session")
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def _display(self) -> str:
        return self.session_display or self.platform_name.title()

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError(
                f"Session not entered — use `with {type(self).__name__}(cfg) as s:`"
            )
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Session not entered")
        return self._context

    def __enter__(self) -> "PlatformSession":
        if not _user_data_dir_initialized(self.user_data_dir):
            raise FileNotFoundError(
                f"Chrome profile at {self.user_data_dir} is empty or missing. "
                f"Run `python -m {self.platform_name}.bootstrap_session` first."
            )
        self._playwright = sync_playwright().start()
        self._context = launch_persistent_context_with_lock_wait(
            self._playwright, self.user_data_dir, headless=self.headless, logger=self.logger,
        )
        self._context.add_init_script(STEALTH_INIT_SCRIPT)
        # `launch_persistent_context` opens one default page already.
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self.logger.info(
            "🌐 %s session started (channel=chrome, headless=%s, profile=%s)",
            self._display, self.headless, self.user_data_dir,
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
            self.logger.info("👋 %s session closed", self._display)

    def goto_with_login_check(self, url: str, *, timeout_ms: Optional[int] = None) -> None:
        """Navigate to ``url``. Raise :class:`LoginRequiredError` on a login redirect."""
        self.logger.debug("➡️ Navigating to %s", url)
        self.page.goto(url, timeout=timeout_ms or self.default_timeout_ms, wait_until="domcontentloaded")
        current = (self.page.url or "").lower()
        if any(marker in current for marker in self.login_markers):
            raise LoginRequiredError(
                f"{self._display} redirected to login — the saved Chrome profile session "
                f"expired. Re-run `python -m {self.platform_name}.bootstrap_session` to log in again."
            )

    def screenshot_failure(self, label: str) -> Path:
        """Save a debug screenshot under ``results/<platform_name>/``."""
        out_dir = REPO_ROOT / "results" / self.platform_name
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = out_dir / f"{ts}-{label}.png"
        try:
            self.page.screenshot(path=str(path), full_page=True)
            self.logger.warning("📸 Failure screenshot saved → %s", path)
        except Exception as err:  # pragma: no cover
            self.logger.error("❌ Could not save failure screenshot: %s", err)
        return path


__all__ = [
    "REPO_ROOT",
    "LoginRequiredError",
    "PlatformSession",
    "configure_logger",
    "_resolve_user_data_dir",
    "_user_data_dir_initialized",
]
