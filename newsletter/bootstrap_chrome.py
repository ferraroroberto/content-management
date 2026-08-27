"""Targeted, idempotent Chrome bootstrap on :9222 for the newsletter profile.

Ensures a Chrome bound to ``newsletter/chrome_user_data`` is listening on the
CDP debug port :9222 — **without ever touching the everyday browser**. This
replaces the old ``bootstrap_chrome.bat`` that did ``taskkill /IM chrome.exe``
(kill *every* Chrome). Supersedes the stop-gap from issue #57; see issue #59.

Behaviour (idempotent):

* If :9222 already responds → reuse it, do nothing. Honours the fleet rule
  "never kill a live holder" — your debug Chrome and its open tabs are left
  alone.
* Else, if the dedicated newsletter profile is held by a non-debug Chrome →
  **wait**, never kill (via :func:`config.chrome_profile_lock.pids_holding_profile`,
  which matches only ``--user-data-dir=<this profile>``, and the same
  exponential backoff schedule as ``config.chrome_profile_lock``). Re-checks
  each cycle whether the holder has exited on its own; only after the full
  schedule is a still-held profile reported as a precise, non-fatal error —
  the holder is treated as genuinely wedged, not force-killed.
* Launch Chrome with the debug port + the dedicated ``--user-data-dir`` and
  poll until the port responds.

This is the **CDP-attach** mechanism — the archive step connects over CDP via
``newsletter/chrome_tabs.py``. It is intentionally distinct from
``launch_persistent_context`` (the Playwright stealth path) and must not be
folded into it.

Usage:
    .venv\\Scripts\\python -m newsletter.bootstrap_chrome
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

from config.chrome_profile_lock import (  # noqa: E402
    DEFAULT_LOCK_BACKOFF_SECONDS,
    pids_holding_profile,
)

USER_DATA_DIR = Path(__file__).parent / "chrome_user_data"
DEBUG_PORT = 9222
DEBUG_URL = f"http://127.0.0.1:{DEBUG_PORT}/json/version"

# Chrome install locations, in probe order (mirrors the old bat).
_CHROME_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
)

logger = logging.getLogger("newsletter.bootstrap_chrome")


def debug_port_up(timeout: float = 1.0) -> bool:
    """True when Chrome's CDP endpoint answers on :9222."""
    try:
        return requests.get(DEBUG_URL, timeout=timeout).status_code == 200
    except requests.RequestException:
        return False


def _find_chrome_exe() -> Path:
    for candidate in _CHROME_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "chrome.exe not found at the expected install paths "
        f"({', '.join(str(c) for c in _CHROME_CANDIDATES)}). "
        "Edit _CHROME_CANDIDATES in newsletter/bootstrap_chrome.py."
    )


def _wait_for_profile_free(
    *, backoff_seconds: Sequence[int] = DEFAULT_LOCK_BACKOFF_SECONDS,
) -> list[int]:
    """Wait out a non-debug Chrome holding the newsletter profile — never kill it.

    Re-checks :func:`pids_holding_profile` after each backoff step; returns as
    soon as the holder exits on its own (empty list). If it is still held
    after the full schedule, returns the still-held PIDs so the caller can
    report a precise, non-fatal error — the holder is a genuinely wedged (or
    simply forgotten-open) process, never force-killed.
    """
    held = pids_holding_profile(USER_DATA_DIR)
    if not held:
        return []
    for delay in backoff_seconds:
        logger.warning(
            "⏳ Newsletter profile %s is held by non-debug Chrome PID(s) %s — "
            "waiting %ds, then re-checking (never auto-killed; close it "
            "yourself if you want the debug Chrome up sooner)",
            USER_DATA_DIR, held, delay,
        )
        time.sleep(delay)
        held = pids_holding_profile(USER_DATA_DIR)
        if not held:
            logger.info("✅ profile %s freed on its own — continuing", USER_DATA_DIR)
            return []
    return held


def ensure_chrome(*, timeout_s: int = 15) -> int:
    """Ensure Chrome is up on :9222 against the newsletter profile.

    Returns 0 on success (already up, or launched and reachable), 3 if the
    port never came up within ``timeout_s`` seconds after launch, 4 if the
    profile is still held by a non-debug Chrome after the full wait schedule
    (a likely-wedged holder, reported rather than force-killed).
    """
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    if debug_port_up():
        logger.info("✅ Chrome already up on :%d — reusing it (open tabs untouched)", DEBUG_PORT)
        return 0

    still_held = _wait_for_profile_free()
    if still_held:
        logger.error(
            "❌ Newsletter profile %s is still held by live process(es) %s after "
            "waiting %ds across %d backoff step(s) — likely wedged. Close it "
            "manually and re-run (fleet rule: never auto-kill a live holder).",
            USER_DATA_DIR, still_held, sum(DEFAULT_LOCK_BACKOFF_SECONDS),
            len(DEFAULT_LOCK_BACKOFF_SECONDS),
        )
        return 4

    chrome = _find_chrome_exe()
    logger.info("🚀 Launching Chrome on :%d  ·  profile %s", DEBUG_PORT, USER_DATA_DIR)
    subprocess.Popen(
        [
            str(chrome),
            f"--remote-debugging-port={DEBUG_PORT}",
            f"--user-data-dir={USER_DATA_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        # Detach so Chrome outlives this launcher (and the app subprocess).
        creationflags=(
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        ),
    )

    for _ in range(timeout_s):
        if debug_port_up():
            logger.info("✅ Chrome debug port is UP on %s", DEBUG_URL)
            logger.info("   Open your newsletter article tabs in that window, then archive.")
            return 0
        time.sleep(1)

    logger.error("❌ :%d not reachable after %ds — bootstrap failed", DEBUG_PORT, timeout_s)
    return 3


def main() -> int:
    # Force UTF-8 stdio so emoji log lines don't crash Windows' cp1252 console.
    from config.console import force_utf8_stdio
    force_utf8_stdio()
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
    return ensure_chrome()


if __name__ == "__main__":
    raise SystemExit(main())
