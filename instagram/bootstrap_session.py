"""One-time interactive login that prepares a dedicated Chrome profile for the Meta planner.

Usage:
    python -m instagram.bootstrap_session [--debug]

Launches **real Chrome** (channel="chrome") pointed at the project-local
profile directory configured as ``instagram.user_data_dir`` (default:
``instagram/chrome_user_data/``). The user's regular Chrome profile is **not
touched**: a separate, dedicated on-disk directory is created by Playwright
under the repo, and Chrome writes its session cookies there.

After login the dedicated profile retains the session, so subsequent runs of
``schedule_instagram_posts`` reuse it without prompting. Mirror of
``linkedin/bootstrap_session.py``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.append(str(Path(__file__).parent.parent))
from config.chrome_launch import STEALTH_INIT_SCRIPT, stealth_launch_kwargs  # noqa: E402
from instagram.instagram_session import (  # noqa: E402
    _resolve_user_data_dir,
    configure_logger,
    load_instagram_config,
)

LOGIN_URL_DEFAULT = "https://business.facebook.com/"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-time Meta (FB+IG) session bootstrap.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = configure_logger("instagram_bootstrap", debug=args.debug)

    cfg = load_instagram_config()
    user_data_dir = _resolve_user_data_dir(cfg["user_data_dir"])
    user_data_dir.mkdir(parents=True, exist_ok=True)

    login_url = cfg.get("login_url", LOGIN_URL_DEFAULT)

    logger.info("🚀 Meta (Instagram + Facebook) session bootstrap")
    logger.info("📁 Dedicated Chrome profile directory: %s", user_data_dir)
    logger.info("   (this is SEPARATE from your normal Chrome profile)")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            **stealth_launch_kwargs(str(user_data_dir), headless=False),
        )
        context.add_init_script(STEALTH_INIT_SCRIPT)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(login_url, wait_until="domcontentloaded")
        # Intentional print (the bootstrap pause is the one place this is OK).
        print(
            "\n>>> Log in to Meta inside the opened Chrome window.\n"
            "    Make sure both Facebook and the connected Instagram business account are accessible.\n"
            "    Then return here and press Enter to save the session...\n"
        )
        try:
            input()
        except KeyboardInterrupt:
            logger.warning("❌ Bootstrap cancelled before login.")
            context.close()
            return 2

        context.close()
        logger.info("✅ Chrome profile saved → %s", user_data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
