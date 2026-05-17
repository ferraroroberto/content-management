"""CDP attach helpers for the user's real Chrome.

Chrome must be launched with ``--remote-debugging-port=9222`` first — see
``bootstrap_chrome.bat``. We attach over CDP (no fresh browser spawn) so the
script operates on the actual tabs the user opened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from playwright.sync_api import Browser, Page, sync_playwright


@dataclass
class TabRef:
    page: Page
    url: str
    title: str


def connect(debug_port: int = 9222) -> Browser:
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{debug_port}")
    browser._pw_handle = pw  # type: ignore[attr-defined]
    return browser


def list_tabs(browser: Browser) -> List[TabRef]:
    tabs: List[TabRef] = []
    for context in browser.contexts:
        for page in context.pages:
            try:
                url = page.url
                title = page.title() or ""
            except Exception:
                continue
            tabs.append(TabRef(page=page, url=url, title=title))
    return tabs


def should_skip(url: str, skip_substrings: List[str]) -> bool:
    u = (url or "").lower()
    if not u or not u.startswith(("http://", "https://")):
        return True
    return any(s in u for s in skip_substrings)


def close_browser(browser: Browser) -> None:
    """Disconnect the CDP session. Does NOT close the underlying Chrome."""
    pw = getattr(browser, "_pw_handle", None)
    try:
        browser.close()
    finally:
        if pw is not None:
            pw.stop()
