"""Playwright session manager for LinkedIn automation.

Uses **real Chrome** (channel="chrome") with a **dedicated, separate**
user-data directory configured under ``linkedin.user_data_dir``. This profile
is created by the bootstrap script and is completely independent of the user's
regular Chrome installation:

* Default location is ``linkedin/chrome_user_data/`` inside this repo
  (gitignored).
* The user's normal Chrome profile at e.g.
  ``%LOCALAPPDATA%\\Google\\Chrome\\User Data`` is never opened, read, or
  written by anything in this package.

Why real Chrome + persistent profile: Playwright's bundled Chromium is
trivially fingerprinted by LinkedIn anti-bot checks, which can block sign-in
and the post composer. Real Chrome with a stable profile presents a normal
browser environment so the human-driven login at bootstrap actually
completes, and subsequent automated runs reuse the same session.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    Playwright,
    sync_playwright,
)

sys.path.append(str(Path(__file__).parent.parent.parent))
from config.chrome_launch import STEALTH_INIT_SCRIPT, stealth_launch_kwargs  # noqa: E402
from config.logger_config import setup_logger  # noqa: E402

logger = logging.getLogger("linkedin_session")

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.json"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def normalize_day(date_str: Optional[str]) -> str:
    """Accept 'YYYYMMDD' or 'YYYY-MM-DD' (or None) and return 'YYYYMMDD'."""
    if not date_str:
        return datetime.now().strftime("%Y%m%d")
    s = date_str.strip()
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y%m%d")
    return datetime.strptime(s, "%Y%m%d").strftime("%Y%m%d")


def _force_utf8_stdout() -> None:
    """Force stdout/stderr to UTF-8 so emoji log lines don't crash Windows cp1252 consoles."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def configure_logger(name: str = "linkedin", debug: bool = False) -> logging.Logger:
    """Set up a logger using the project-wide pattern."""
    _force_utf8_stdout()
    level = logging.DEBUG if debug else logging.INFO
    return setup_logger(name, level=level, file_logging=True)


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
                "'linkedin/chrome_user_data')."
            )
    return p


def _user_data_dir_initialized(user_data_dir: Path) -> bool:
    """A persistent-profile directory is 'ready' once Chrome has written its Default subdir."""
    return user_data_dir.exists() and (user_data_dir / "Default").exists()


# Playwright surfaces a locked persistent profile with one of these phrases.
# Match defensively (lowercased substring) — the wording has drifted between
# Playwright versions ("Opening in existing browser session" / "already in use").
_LOCKED_PROFILE_SIGNATURES = (
    "opening in existing browser session",
    "already in use",
    "process singleton",
)


def _is_locked_profile_error(err: Exception) -> bool:
    """True when a Playwright launch error means the profile dir is already locked."""
    msg = str(err).lower()
    return any(sig in msg for sig in _LOCKED_PROFILE_SIGNATURES)


def _kill_chrome_holding_profile(user_data_dir: Path) -> int:
    """Terminate Chrome process(es) bound to **this exact** `user_data_dir`.

    Targets only Chrome whose command line carries `--user-data-dir=<this dir>`,
    never a blanket `taskkill chrome.exe` — the user's everyday browser runs on a
    different profile and must not be touched. Windows-only for now (this is where
    the suite runs); returns the number of processes terminated.
    """
    if sys.platform != "win32":
        logger.warning("⚠️ locked-profile cleanup is Windows-only — skipping kill on %s", sys.platform)
        return 0
    target = str(user_data_dir).replace("'", "''")
    # PowerShell: match chrome.exe whose CommandLine references our profile dir
    # (regex-escaped, optional surrounding quote) and kill by PID, echoing each PID.
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$t=[regex]::Escape('{target}');"
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match ('--user-data-dir=\"?' + $t) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }"
    )
    # Use the Windows PowerShell 5.1 absolute path — the `pwsh` on PATH is a
    # WindowsApps stub that fails when spawned non-interactively.
    powershell = str(
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    try:
        proc = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as err:
        logger.warning("⚠️ could not run Chrome-cleanup helper: %s", err)
        return 0
    return len([ln for ln in (proc.stdout or "").splitlines() if ln.strip()])


def _clear_stale_singleton_locks(user_data_dir: Path) -> int:
    """Remove Chrome's profile lock files left behind by a process that's now gone.

    Only call this after `_kill_chrome_holding_profile` — removing these while a
    live Chrome still holds the profile would corrupt its state. Returns the count
    of lock files removed.
    """
    removed = 0
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock = user_data_dir / name
        try:
            # `is_symlink()` catches the dangling-symlink form used on POSIX, where
            # `exists()` returns False; on Windows these are plain files.
            if lock.exists() or lock.is_symlink():
                lock.unlink()
                removed += 1
        except OSError as err:
            logger.debug("could not remove stale lock %s: %s", lock, err)
    return removed


class LinkedInSession:
    """Persistent-context wrapper around real Chrome with a dedicated profile.

    Usage:
        with LinkedInSession(cfg) as session:
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
            raise RuntimeError("Session not entered — use `with LinkedInSession(cfg) as s:`")
        return self._page

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Session not entered")
        return self._context

    def __enter__(self) -> "LinkedInSession":
        if not _user_data_dir_initialized(self.user_data_dir):
            raise FileNotFoundError(
                f"Chrome profile at {self.user_data_dir} is empty or missing. "
                "Run `python -m linkedin.bootstrap_session` first."
            )
        self._playwright = sync_playwright().start()
        self._context = self._launch_context_with_recovery()
        self._context.add_init_script(STEALTH_INIT_SCRIPT)
        # `launch_persistent_context` opens one default page already.
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        logger.info(
            "🌐 LinkedIn session started (channel=chrome, headless=%s, profile=%s)",
            self.headless, self.user_data_dir,
        )
        return self

    def _launch_context(self) -> BrowserContext:
        return self._playwright.chromium.launch_persistent_context(
            **stealth_launch_kwargs(str(self.user_data_dir), headless=self.headless),
        )

    def _launch_context_with_recovery(self) -> BrowserContext:
        """Launch the persistent context, self-healing a locked profile once.

        A stale/zombie Chrome from a previous run (or an overlapping run) can keep
        the dedicated profile locked, which Playwright reports as "Opening in
        existing browser session". Rather than crash the whole run, terminate the
        stale holder bound to this profile, clear any leftover lock files, and
        retry the launch exactly once. Non-lock launch errors propagate unchanged.
        """
        try:
            return self._launch_context()
        except PlaywrightError as err:
            if not _is_locked_profile_error(err):
                raise
            logger.warning(
                "⚠️ Chrome profile %s is locked — self-healing (kill stale holder + retry)",
                self.user_data_dir,
            )
            killed = _kill_chrome_holding_profile(self.user_data_dir)
            logger.info("🔪 terminated %d stale Chrome process(es) holding the profile", killed)
            removed = _clear_stale_singleton_locks(self.user_data_dir)
            if removed:
                logger.info("🧹 cleared %d stale lock file(s) from the profile", removed)
            time.sleep(1.5)
            try:
                ctx = self._launch_context()
            except PlaywrightError as retry_err:
                raise RuntimeError(
                    f"Chrome profile at {self.user_data_dir} is still locked after self-heal "
                    "(killed stale holders + retried once). Close any Chrome window using this "
                    "profile and re-run."
                ) from retry_err
            logger.info("✅ profile lock cleared — launch succeeded on retry")
            return ctx

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            logger.info("👋 LinkedIn session closed")

    def goto_with_login_check(self, url: str, *, timeout_ms: int = 30000) -> None:
        """Navigate to `url`. Raise LoginRequiredError if LinkedIn redirected to login."""
        logger.debug("➡️ Navigating to %s", url)
        self.page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        current = (self.page.url or "").lower()
        # LinkedIn bounces unauthenticated users to /login, /uas/login, or /checkpoint.
        if any(s in current for s in ("/login", "/uas/login", "/checkpoint/lg/login")):
            raise LoginRequiredError(
                "LinkedIn redirected to login — the saved Chrome profile session expired. "
                "Re-run `python -m linkedin.bootstrap_session` to log in again."
            )

    def screenshot_failure(self, label: str) -> Path:
        """Save a debug screenshot under results/linkedin/."""
        out_dir = REPO_ROOT / "results" / "linkedin"
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
    """Raised when the saved LinkedIn session is no longer valid."""
