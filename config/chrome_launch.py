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
    """
    return {
        "user_data_dir": user_data_dir,
        "channel": "chrome",
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=Translate",
            "--no-default-browser-check",
            "--no-first-run",
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


__all__ = ["stealth_launch_kwargs", "STEALTH_INIT_SCRIPT"]
