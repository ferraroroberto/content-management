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
  kill **only those PIDs** (via :func:`config.chrome_profile_lock.pids_holding_profile`,
  which matches only ``--user-data-dir=<this profile>``), then relaunch. The
  persistent profile (logins) survives; only that window's live tabs are lost.
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

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import requests  # noqa: E402

from config.chrome_profile_lock import pids_holding_profile  # noqa: E402

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


def _kill_pids(pids: list[int]) -> None:
    for pid in pids:
        # /T also kills child processes (Chrome's renderer/GPU helpers).
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F", "/T"],
            capture_output=True, text=True,
        )


def ensure_chrome(*, timeout_s: int = 15) -> int:
    """Ensure Chrome is up on :9222 against the newsletter profile.

    Returns 0 on success (already up, or launched and reachable), 3 if the
    port never came up within ``timeout_s`` seconds.
    """
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

    if debug_port_up():
        logger.info("✅ Chrome already up on :%d — reusing it (open tabs untouched)", DEBUG_PORT)
        return 0

    held = pids_holding_profile(USER_DATA_DIR)
    if held:
        logger.warning(
            "⚠️ Newsletter profile is held by a non-debug Chrome (PID(s) %s) — "
            "relaunching it with the debug port. That window's open tabs will "
            "close; your logins persist.", held,
        )
        _kill_pids(held)
        # Give the OS a moment to release the profile-dir lock.
        time.sleep(2)

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
