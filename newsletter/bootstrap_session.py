"""One-time interactive bootstrap for the newsletter-archive Chrome profile.

Launches Chrome against ``newsletter/chrome_user_data/`` and opens Gmail so
you can sign in. Press Enter once you're logged into Gmail (and any other
newsletter sources you'd like a persistent session for). The profile is
written to disk on close; future ``bootstrap_chrome.bat`` runs reuse it.

Usage:
    .venv\\Scripts\\python -m newsletter.bootstrap_session

Pattern mirrors ``planning/linkedin/bootstrap_session.py`` and friends.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from config.chrome_launch import STEALTH_INIT_SCRIPT, stealth_launch_kwargs  # noqa: E402

USER_DATA_DIR = Path(__file__).parent / "chrome_user_data"
LANDING_URL = "https://mail.google.com/"


def main() -> int:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📁 Newsletter Chrome profile: {USER_DATA_DIR}")
    print("   (this profile is separate from your normal Chrome — same pattern as")
    print("   planning/linkedin/chrome_user_data, planning/instagram/..., etc.)")
    print()

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            **stealth_launch_kwargs(str(USER_DATA_DIR), headless=False),
        )
        context.add_init_script(STEALTH_INIT_SCRIPT)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LANDING_URL, wait_until="domcontentloaded")

        print(">>> Sign in to Gmail (and any other newsletter sources you want) in")
        print(">>> the Chrome window that just opened.")
        print(">>> When done, press Enter here to save the session and exit.\n")
        try:
            input()
        except KeyboardInterrupt:
            print("❌ Cancelled before save.")
            context.close()
            return 2

        context.close()
        print(f"✅ Profile saved → {USER_DATA_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
