"""Serialize access to a shared persistent Chrome profile — wait, never kill.

A persistent Chrome profile (``channel="chrome"`` + a dedicated ``user-data-dir``)
allows only **one live instance**. Several unattended jobs in this suite target the
*same* profile — e.g. the engagement scrape and the reporting follower-scrape both
drive ``planning/linkedin/chrome_user_data`` and both fire from the app-launcher
around the same time. The second job to launch gets Playwright's "Opening in
existing browser session" error and dies (see issue #54).

The holder is almost always a **legitimately-running sibling job**, not a stale
zombie, so we must **not** kill it — that would corrupt the sibling's run. Instead
we wait for the profile to free with exponential backoff, re-attempting the launch
each cycle, and only raise (never kill) if it is still held after the full schedule.
A process holding the profile longer than that is genuinely hung and worth
surfacing.

Note: Chrome's profile lock on Windows is a live-process kernel object, **not** the
POSIX ``SingletonLock`` / ``SingletonCookie`` / ``SingletonSocket`` files — deleting
those is a no-op on Windows, which is why the only safe remedy here is to wait for
the holding process to exit.

Single source of truth: every session module imports
``launch_persistent_context_with_lock_wait`` from here — never re-inline a
launch-with-retry in a new module.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Playwright

from config.chrome_launch import stealth_launch_kwargs

# Exponential backoff between launch re-attempts, in seconds (~15 min total).
DEFAULT_LOCK_BACKOFF_SECONDS = (60, 120, 240, 480)

# Playwright surfaces a locked persistent profile with one of these phrases.
# Match defensively (lowercased substring) — the wording has drifted between
# Playwright versions ("Opening in existing browser session" / "already in use").
_LOCKED_PROFILE_SIGNATURES = (
    "opening in existing browser session",
    "already in use",
    "process singleton",
)


def is_locked_profile_error(err: Exception) -> bool:
    """True when a Playwright launch error means the profile dir is already in use."""
    msg = str(err).lower()
    return any(sig in msg for sig in _LOCKED_PROFILE_SIGNATURES)


def pids_holding_profile(user_data_dir: Path) -> list[int]:
    """Return PIDs of Chrome process(es) bound to **this exact** ``user_data_dir``.

    Matches only Chrome whose command line carries ``--user-data-dir=<this dir>`` so
    the user's everyday browser (a different profile) is never implicated. Detection
    is Windows-only for now (this is where the suite runs); other platforms return an
    empty list. This function only *observes* — it never terminates anything.
    """
    if sys.platform != "win32":
        logger = logging.getLogger("chrome_profile_lock")
        logger.debug("holder detection is Windows-only — skipping on %s", sys.platform)
        return []
    target = str(user_data_dir).replace("'", "''")
    # PowerShell: list chrome.exe whose CommandLine references our profile dir
    # (regex-escaped, optional surrounding quote), echoing each matching PID.
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$t=[regex]::Escape('{target}');"
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match ('--user-data-dir=\"?' + $t) } | "
        "ForEach-Object { $_.ProcessId }"
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
        logging.getLogger("chrome_profile_lock").warning(
            "⚠️ could not detect Chrome holding the profile: %s", err
        )
        return []
    pids: list[int] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def launch_persistent_context_with_lock_wait(
    playwright: Playwright,
    user_data_dir: Path,
    *,
    headless: bool,
    logger: logging.Logger,
    backoff_seconds: Sequence[int] = DEFAULT_LOCK_BACKOFF_SECONDS,
) -> BrowserContext:
    """Launch a persistent context, waiting out a sibling that holds the profile.

    On a locked-profile error, log which process holds the profile and wait with
    exponential backoff (``backoff_seconds``), re-attempting the launch after each
    wait. Returns the context as soon as the profile frees. Never kills the holder:
    if the profile is still locked after the whole schedule, raise a precise error
    (a holder lasting that long is almost certainly hung). Non-lock launch errors
    propagate unchanged.
    """
    kwargs = stealth_launch_kwargs(str(user_data_dir), headless=headless)

    try:
        return playwright.chromium.launch_persistent_context(**kwargs)
    except PlaywrightError as err:
        if not is_locked_profile_error(err):
            raise
        last_err: PlaywrightError = err

    for delay in backoff_seconds:
        pids = pids_holding_profile(user_data_dir)
        if pids:
            logger.warning(
                "⏳ Chrome profile %s is held by live process(es) %s — waiting %ds, then retrying",
                user_data_dir, pids, delay,
            )
        else:
            logger.warning(
                "⏳ Chrome profile %s is locked but no live holder was detected "
                "(teardown race) — waiting %ds, then retrying",
                user_data_dir, delay,
            )
        time.sleep(delay)
        try:
            ctx = playwright.chromium.launch_persistent_context(**kwargs)
            logger.info("✅ profile %s is free — launch succeeded after waiting", user_data_dir)
            return ctx
        except PlaywrightError as retry_err:
            if not is_locked_profile_error(retry_err):
                raise
            last_err = retry_err

    held_by = pids_holding_profile(user_data_dir)
    raise RuntimeError(
        f"Chrome profile at {user_data_dir} is still locked after waiting "
        f"{sum(backoff_seconds)}s across {len(backoff_seconds)} backoff steps. "
        f"Live holder PID(s): {held_by or 'none detected'}. A process holding the "
        "profile this long is likely hung — close it and re-run."
    ) from last_err


__all__ = [
    "DEFAULT_LOCK_BACKOFF_SECONDS",
    "is_locked_profile_error",
    "pids_holding_profile",
    "launch_persistent_context_with_lock_wait",
]
