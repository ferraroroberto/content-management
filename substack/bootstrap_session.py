"""One-time interactive login that prepares a dedicated Chrome profile for Substack.

Usage:
    python -m substack.bootstrap_session [--debug]

Launches **real Chrome** (channel="chrome") pointed at the project-local profile
directory configured as ``substack.user_data_dir`` (default:
``substack/chrome_user_data/``). The user's regular Chrome profile is **not
touched**: a separate, dedicated on-disk directory is created by Playwright
under the repo, and Chrome writes its session cookies there.

After login the dedicated profile retains the session, so subsequent runs of
``post_substack_note`` and ``update_substack_followers`` reuse it without
prompting.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.append(str(Path(__file__).parent.parent))
from substack.substack_session import (  # noqa: E402
    _resolve_user_data_dir,
    configure_logger,
    load_substack_config,
)

SIGN_IN_URL = "https://substack.com/sign-in"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-time Substack session bootstrap.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = configure_logger("substack_bootstrap", debug=args.debug)

    cfg = load_substack_config()
    user_data_dir = _resolve_user_data_dir(cfg["user_data_dir"])
    user_data_dir.mkdir(parents=True, exist_ok=True)

    logger.info("🚀 Substack session bootstrap")
    logger.info("📁 Dedicated Chrome profile directory: %s", user_data_dir)
    logger.info("   (this is SEPARATE from your normal Chrome profile)")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(SIGN_IN_URL, wait_until="domcontentloaded")
        # Intentional print (the issue allows this single bootstrap pause).
        print("\n>>> Log in inside the opened Chrome window, then press Enter here to save the session...\n")
        try:
            input()
        except KeyboardInterrupt:
            logger.warning("❌ Bootstrap cancelled before login.")
            context.close()
            return 2

        # Persistent context flushes the profile to disk on close.
        context.close()
        logger.info("✅ Chrome profile saved → %s", user_data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
