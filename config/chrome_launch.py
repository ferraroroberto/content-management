"""Shared real-Chrome launch options for every Playwright-driven platform.

Every social-automation module (substack, linkedin, twitter, threads,
instagram, …) **must** drive Chrome via these helpers. The goal is a session
that is indistinguishable from the user driving Chrome by hand — no
automation infobar at the top of the window, no detectable
``navigator.webdriver``, and no Playwright-default ``--enable-automation``
switch.

Past incidents on other domains (captchas appearing the moment a bot
fingerprint was detected) make this a hard requirement for LinkedIn,
Twitter, Threads, Instagram and Substack alike — these platforms run
aggressive anti-bot heuristics and getting flagged risks account warnings
or lockouts.

Usage:

    from config.chrome_launch import stealth_launch_kwargs, STEALTH_INIT_SCRIPT

    context = pw.chromium.launch_persistent_context(
        **stealth_launch_kwargs(str(user_data_dir), headless=False)
    )
    context.add_init_script(STEALTH_INIT_SCRIPT)

NEVER re-inline these arguments in a new module — that's how stealth gets
out of sync across platforms. Edit this file once; everyone inherits.
"""

from __future__ import annotations

from typing import Any


# Init script injected into every page before any site script runs.
# Removes the most commonly fingerprinted automation tell.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
"""


def stealth_launch_kwargs(user_data_dir: str, *, headless: bool = False) -> dict[str, Any]:
    """Build the kwargs dict for ``pw.chromium.launch_persistent_context(**kwargs)``.

    Notes on the individual flags:

    * ``channel="chrome"`` — drive the user's real Chrome install, NOT the
      bundled Chromium (Chromium is trivially fingerprinted).
    * ``ignore_default_args=["--enable-automation", ...]`` — strip the
      Playwright default that adds the "Chrome is being controlled by
      automated test software" infobar at the top of the window. Also
      strips the IdleDetection feature flag that exposes another tell.
    * ``args=[...]`` — disable the AutomationControlled blink feature
      (handles ``navigator.webdriver`` at a lower level), turn off the
      Translate prompt, and skip the default-browser and first-run wizards
      that would otherwise pop up on a fresh profile.
    * ``viewport`` — pinned to 1280×900 so screenshots and selector
      assumptions stay stable across runs.
    * ``locale="en-US"`` + ``--lang=en-US`` — force the browser locale so
      every site renders in English. Without this, sites localize from the
      OS / account locale (Spanish on this machine), which breaks every
      English ``get_by_role`` and accessible-name selector in the planning
      drivers (see issue #27). LinkedIn additionally honors a per-account
      UI language setting; if a profile was bootstrapped while logged in to
      a Spanish-language account, the account setting wins and must be
      flipped to English manually once (Settings → Account preferences →
      Language).
    """
    return {
        "user_data_dir": user_data_dir,
        "channel": "chrome",
        "headless": headless,
        "locale": "en-US",
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=Translate",
            "--no-default-browser-check",
            "--no-first-run",
            "--lang=en-US",
        ],
        "ignore_default_args": [
            "--enable-automation",
            "--enable-blink-features=IdleDetection",
            # Stops the yellow "You are using an unsupported command-line flag:
            # --no-sandbox. Stability and security will suffer." infobar. The
            # sandbox is still effectively disabled by Playwright's process
            # model — this just hides Chrome's protest about that.
            "--no-sandbox",
        ],
        "viewport": {"width": 1280, "height": 900},
    }


# ── doc-capture variant ─────────────────────────────────────────────
#
# Used by ``config/doc_capture`` (issue #110) to screenshot the Streamlit
# control panel for the README. Deliberately NOT the stealth profile above:
# doc capture drives our own localhost app, so there is nothing to hide
# from — what matters is determinism (identical pixels for identical
# inputs) and isolation (never touch the logged-in scraping profiles).


# Injected into the page before every capture: kill animations, transitions
# and the text caret so two captures of the same state are byte-comparable.
DOC_CAPTURE_SETTLE_CSS = """
*, *::before, *::after {
    transition: none !important;
    animation: none !important;
    caret-color: transparent !important;
}
html { scroll-behavior: auto !important; }
"""


def doc_capture_launch_kwargs(*, headless: bool = True) -> dict[str, Any]:
    """Build the kwargs dict for ``pw.chromium.launch(**kwargs)``.

    Clean, **non-persistent** launch (no ``user_data_dir``): pair with
    ``browser.new_context(**doc_capture_context_kwargs())``. Real Chrome
    (``channel="chrome"``) so the rendering matches what the user sees;
    ``--force-color-profile=srgb`` + ``--hide-scrollbars`` remove the two
    remaining sources of pixel drift between machines/runs.
    """
    return {
        "channel": "chrome",
        "headless": headless,
        "args": [
            "--force-color-profile=srgb",
            "--hide-scrollbars",
            "--disable-features=Translate",
            "--no-default-browser-check",
            "--no-first-run",
            "--lang=en-US",
        ],
    }


def doc_capture_context_kwargs() -> dict[str, Any]:
    """Context options for the doc-capture browser: one fixed wide desktop
    viewport (Streamlit isn't meaningfully responsive), fixed scale factor,
    forced light scheme + reduced motion, pinned locale/timezone."""
    return {
        "viewport": {"width": 1600, "height": 1000},
        "device_scale_factor": 1,
        "reduced_motion": "reduce",
        "color_scheme": "light",
        "locale": "en-US",
        "timezone_id": "UTC",
    }


__all__ = [
    "stealth_launch_kwargs",
    "STEALTH_INIT_SCRIPT",
    "doc_capture_launch_kwargs",
    "doc_capture_context_kwargs",
    "DOC_CAPTURE_SETTLE_CSS",
]
