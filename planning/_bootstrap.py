"""Shared implementation for the per-platform Chrome-profile bootstrap scripts.

Five near-verbatim ~60-line modules (twitter, threads, instagram, linkedin,
substack) differed only in logger name, config loader, default login URL and
prompt text. This collapses them into one ``run_bootstrap`` plus five thin
``main()`` shims, mirroring how ``PlatformSession`` already collapsed the
per-platform session modules.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Callable

from playwright.sync_api import sync_playwright

from config.chrome_launch import STEALTH_INIT_SCRIPT, stealth_launch_kwargs


def parse_bootstrap_args(description: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def run_bootstrap(
    *,
    platform_label: str,
    logger_name: str,
    load_config: Callable[[], dict],
    resolve_user_data_dir: Callable[[str], Path],
    configure_logger: Callable[..., logging.Logger],
    default_login_url: str,
    login_prompt: str,
    debug: bool,
) -> int:
    """Drive the interactive one-time login that prepares a dedicated Chrome
    profile for one platform.

    Launches **real Chrome** (channel="chrome") pointed at the project-local
    profile directory configured as ``<platform>.user_data_dir``. The user's
    regular Chrome profile is **not touched**: a separate, dedicated on-disk
    directory is created by Playwright under the repo, and Chrome writes its
    session cookies there. After login the dedicated profile retains the
    session, so subsequent scheduler runs reuse it without prompting.
    """
    logger = configure_logger(logger_name, debug=debug)

    cfg = load_config()
    user_data_dir = resolve_user_data_dir(cfg["user_data_dir"])
    user_data_dir.mkdir(parents=True, exist_ok=True)

    login_url = cfg.get("login_url", default_login_url)

    logger.info("🚀 %s session bootstrap", platform_label)
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
        print(login_prompt)
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
