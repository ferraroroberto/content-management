"""Shared failure classifier for the planning pipeline + the self-heal skill.

A scheduler row records a coarse ``status`` (``LIVE`` / ``DRY`` / ``FAIL`` /
``LOGIN-REQUIRED`` / ``OTHER``) plus a free-text ``detail``. That is enough for
a human reading the markdown summary, but the autonomous self-heal loop
(``/schedule-autoheal``) needs to know *what kind* of failure a row is so it can
decide whether it is safe to touch:

- **ui-drift** — a selector / locator broke because the platform's DOM changed.
  This is the ONLY kind the heal loop may auto-fix (selector-only edits).
- **login-required** — the session is logged out; a human must re-auth.
- **data-error** — the content/payload could not be resolved (empty caption,
  missing illustration, Notion lookup miss). A human decision, never auto-fixed.
- **other** — anything unclassified; treated as human-only.
- **none** — not a failure (``LIVE`` / ``DRY``).

The classification lives here so the pipeline (which writes ``failure_kind`` into
the machine-readable result JSON) and the skill (which reads it) can never drift
apart. Pure function, no side effects — trivially unit-testable.
"""

from __future__ import annotations

import re
from typing import Literal

FailureKind = Literal["ui-drift", "login-required", "data-error", "other", "none"]

# Playwright selector/locator breakage phrasing. These are the words Playwright
# itself emits when a selector stops matching after a DOM change — the exact
# signature of UI drift (issue #60 surfaced three of these in one run).
_UI_DRIFT_RE = re.compile(
    r"timeout|timed out|waiting for|locator|selector|strict mode violation|"
    r"no element|element is not|not visible|not attached|intercepts pointer|"
    r"element handle|frame was detached|exceeded.*wait",
    re.I,
)

# Content/data resolution failures — a human decision, never a selector fix.
_DATA_ERROR_RE = re.compile(
    r"payload|resolution|could not resolve|not found in notion|missing|"
    r"no rows|empty|caption|illustration|keyerror|nonetype|no such (?:row|page)",
    re.I,
)


def classify(status: str, detail: str) -> FailureKind:
    """Map a scheduler row's ``(status, detail)`` to a heal-eligibility class.

    Order matters: login is checked first (most specific), then UI-drift
    (the heal-eligible class), then data errors, else ``other``. Non-failure
    statuses short-circuit to ``none``.
    """
    status_up = (status or "").upper()
    if status_up in ("LIVE", "DRY"):
        return "none"
    if status_up == "LOGIN-REQUIRED":
        return "login-required"

    text = detail or ""
    if _UI_DRIFT_RE.search(text):
        return "ui-drift"
    if _DATA_ERROR_RE.search(text):
        return "data-error"
    return "other"


_SCREENSHOT_RE = re.compile(r"\(screenshot\s+([^)]+)\)")


def extract_screenshot(detail: str) -> str:
    """Pull the screenshot path the schedulers embed as ``(screenshot <path>)``
    in a row's ``detail`` string. Returns an empty string when absent."""
    match = _SCREENSHOT_RE.search(detail or "")
    return match.group(1).strip() if match else ""
