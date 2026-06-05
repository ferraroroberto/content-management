r"""Harvest the Substack session cookie(s) from the dedicated Chrome profile.

This is the once-per-cookie-lifetime chore for the **native HTTP API** path (the
analogue of ``bootstrap_session`` for the Playwright path). It launches the same
dedicated real-Chrome profile via :class:`SubstackSession`, confirms the saved
login is still valid, then reads ``context.cookies()`` and writes the
Substack-domain cookies + the browser User-Agent to ``api_session.json``
(gitignored). The native client (:mod:`planning.substack.api_client`) loads that
file for every subsequent HTTP call — no browser launch needed until the cookie
expires (~89 days).

The User-Agent is captured because ``cf_clearance`` (Cloudflare) is bound to the
UA that solved its challenge; the HTTP session must present the same UA.

Usage (from the repo root):
    & .\.venv\Scripts\python.exe -m planning.substack.extract_session [--debug]

NO cookie *values* are printed — only names + expiry — so nothing secret leaks
into logs. The written ``api_session.json`` is gitignored.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from planning.substack.api_client import SESSION_FILE
from planning.substack.substack_session import (
    SubstackSession,
    configure_logger,
    load_substack_config,
)

HARVEST_URL = "https://substack.com/home"
KEY_COOKIES = {"substack.sid", "substack.lli", "cf_clearance"}


def _fmt_expiry(expires: float) -> str:
    if not expires or expires < 0:
        return "session (no expiry)"
    dt = datetime.fromtimestamp(expires, tz=timezone.utc)
    days = (dt - datetime.now(tz=timezone.utc)).days
    return f"{dt.date().isoformat()} (~{days}d)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Harvest Substack cookies for the native API path.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = configure_logger("substack_extract_session", debug=args.debug)
    cfg = load_substack_config()

    with SubstackSession(cfg) as session:
        # Raises LoginRequiredError (→ "re-run bootstrap_session") if expired.
        session.goto_with_login_check(HARVEST_URL)
        all_cookies = session.context.cookies()
        user_agent = session.page.evaluate("() => navigator.userAgent")

    substack_cookies = {
        c["name"]: c["value"]
        for c in all_cookies
        if "substack" in (c.get("domain") or "").lower()
    }

    logger.info("🍪 Harvested %d Substack-domain cookies:", len(substack_cookies))
    for c in sorted(all_cookies, key=lambda x: x.get("name", "")):
        if "substack" not in (c.get("domain") or "").lower():
            continue
        star = " ⭐" if c.get("name") in KEY_COOKIES else ""
        logger.info("   %-22s %s%s", c.get("name"), _fmt_expiry(c.get("expires", -1)), star)

    if "substack.sid" not in substack_cookies:
        logger.error("❌ substack.sid not found — login may have expired. Re-run bootstrap_session.")
        return 1

    SESSION_FILE.write_text(
        json.dumps({"cookies": substack_cookies, "user_agent": user_agent}),
        encoding="utf-8",
    )
    logger.info("✅ Wrote native API session → %s", SESSION_FILE)
    logger.info("   (gitignored — holds live auth cookies, never commit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
