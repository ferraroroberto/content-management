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
apart.

This module also owns the **retry-safety contract** (issue #235):
``PostMayBeLiveError`` marks a failure that must never be retried, and
``attempt_row`` is the single enforcement of that rule. Both live here, next to
each other and to the classifier, precisely because a driver that re-implemented
either one slightly differently would double-post to a live social account.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Literal, Optional, TypeVar

logger = logging.getLogger("planning_failure")

T = TypeVar("T")

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


class PostMayBeLiveError(RuntimeError):
    """A row failed at or after the point of no return — never retry it.

    The per-row retry added in issue #235 exists to absorb races *before* a
    post is committed (composer not open yet, caption box not rendered). Once
    the driver has clicked the platform's final Schedule action, a failure no
    longer means "nothing happened": the post may well be scheduled and the
    driver simply lost sight of it. Retrying there would double-post, which is
    strictly worse than the transient failure the retry was added to fix.

    So drivers raise this — instead of a plain ``RuntimeError`` — for anything
    from the final-action click onward, and the row loop treats it as terminal.
    The boundary is deliberately drawn *before* that click rather than after:
    a click that never landed is safe to retry, but proving it never landed is
    harder than accepting one lost row.
    """


ROW_ATTEMPTS = 2


def attempt_row(
    run: Callable[[], T],
    *,
    label: str,
    attempts: int = ROW_ATTEMPTS,
    reset: Optional[Callable[[], None]] = None,
) -> T:
    """Run one scheduler row, retrying only failures that are safe to retry.

    ``run`` is re-invoked at most ``attempts`` times. Between attempts ``reset``
    (when given) returns the browser to a clean state — the composer from the
    failed attempt has to be dismissed or the retry types into a half-filled
    one.

    Two failures are never retried:

    * ``PostMayBeLiveError`` — raised at or past the platform's final Schedule
      click, where a retry risks a duplicate post (see the class docstring).
    * anything on the final attempt, which is simply re-raised.

    Everything else — a composer that never opened, a caption box that had not
    rendered — is exactly the transient class issue #235 was filed for, and is
    retried against a settled DOM.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    last_err: Exception
    for attempt in range(1, attempts + 1):
        try:
            return run()
        except PostMayBeLiveError:
            # Terminal by construction: the post may already be live.
            raise
        except Exception as err:
            last_err = err
            if attempt >= attempts:
                break
            logger.warning(
                "↻ %s: attempt %d/%d failed (%s) — retrying.",
                label, attempt, attempts, err,
            )
            if reset is not None:
                try:
                    reset()
                except Exception as reset_err:
                    logger.warning(
                        "⚠️ %s: could not reset between attempts: %s", label, reset_err
                    )
    raise last_err


_SCREENSHOT_RE = re.compile(r"\(screenshot\s+([^)]+)\)")


def extract_screenshot(detail: str) -> str:
    """Pull the screenshot path the schedulers embed as ``(screenshot <path>)``
    in a row's ``detail`` string. Returns an empty string when absent."""
    match = _SCREENSHOT_RE.search(detail or "")
    return match.group(1).strip() if match else ""
