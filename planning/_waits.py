"""Bounded readiness waits shared by the planning schedulers.

``Locator.count()`` returns *immediately* — it is a query, not a wait. Using it
as a readiness test ("is the composer there yet?") therefore only ever asks
"was it there at this instant", and a driver built on it fails the moment the
page is slower than whatever fixed ``page.wait_for_timeout(...)`` happened to
precede the probe. Issue #235: an X row failed in ~10 s (its six siblings took
~14 s) because the app shell had not hydrated, and a Threads row failed because
the composer modal's shell had mounted while its ``contenteditable`` had not —
two probes racing each other inside the same driver.

The helpers here replace that pattern with a deadline + re-resolve loop, the
same shape ``linkedin_composer.click_feed_entry`` already uses: every attempt
re-resolves the locator against the live DOM, so a not-yet-mounted element on
attempt 1 is simply found on attempt 3 instead of raising. A warm page still
returns on the first attempt in well under a second — the budget is a ceiling,
not a cost.

Two rules the callers depend on:

* **Deadlines come off the page clock** (``Date.now()`` in the page context),
  not ``time.monotonic()``, so the fake-page harness in ``tests/`` can drive a
  full timeout in zero real seconds — the same trick
  ``_wait_composer_clears`` and ``_wait_for_pdf_upload`` already rely on.
* **Failures are self-describing.** Every raise ends with a live probe of each
  candidate's ``count()``, so the log alone says whether the selector matched
  nothing (drift) or matched something that never became visible (a race) —
  without needing the failure screenshot.
"""

from __future__ import annotations

import logging
from typing import Sequence, Tuple

logger = logging.getLogger("planning_waits")

# Ceiling for a readiness wait. Generous on purpose: it is only ever paid in
# full on a genuine failure, and a cold app shell (X's splash screen) can take
# several seconds to hydrate on a slow morning.
READY_TIMEOUT_MS = 20000

# Per-attempt budget for a single candidate. Short so the loop cycles through
# every candidate quickly rather than spending the whole budget on the first.
ATTEMPT_TIMEOUT_MS = 800

# Budget for a click to produce its observable effect (a modal opening).
EFFECT_TIMEOUT_MS = 4000

CLICK_TIMEOUT_MS = 4000

# Settle pause between rounds, letting a churning SPA re-render before we
# re-resolve against it.
POLL_INTERVAL_MS = 400

# (label, locator) pairs — the label is what shows up in the log/diagnostic.
Candidates = Sequence[Tuple[str, object]]


def _now(page) -> float:
    """Milliseconds from the *page's* clock (see module docstring)."""
    return page.evaluate("() => Date.now()")


def describe(candidates: Candidates) -> str:
    """Live ``count()`` probe of every candidate, for a failure message.

    Deliberately called only on the failure path: it distinguishes "matched
    nothing" (the platform renamed something — UI drift) from "matched but
    never became visible" (a race, or an element behind an overlay), which is
    the first question anyone debugging one of these asks.
    """
    parts = []
    for name, loc in candidates:
        try:
            parts.append(f"{name}: count={loc.count()}")
        except Exception as err:  # a detached frame can make even count() throw
            parts.append(f"{name}: probe-error({type(err).__name__})")
    return "; ".join(parts)


def wait_for_first_ready(
    page,
    candidates: Candidates,
    *,
    label: str,
    timeout_ms: int = READY_TIMEOUT_MS,
    state: str = "visible",
):
    """Return the first candidate that reaches ``state`` before the deadline.

    Candidates are tried in order on every round, so preference order is
    honoured while a later-appearing preferred candidate can still win a
    subsequent round. Playwright locators are lazy, so the caller may build
    them once and they still re-resolve on each attempt.

    Raises ``RuntimeError`` naming ``label`` and describing what each candidate
    could actually see at the deadline.
    """
    deadline = _now(page) + timeout_ms
    attempts = 0
    while True:
        attempts += 1
        for name, loc in candidates:
            try:
                target = loc.first
                target.wait_for(state=state, timeout=ATTEMPT_TIMEOUT_MS)
            except Exception:
                continue
            if attempts > 1:
                logger.info(
                    "✅ %s: ready via %s after %d attempt(s).", label, name, attempts
                )
            return target
        if _now(page) >= deadline:
            break
        page.wait_for_timeout(POLL_INTERVAL_MS)
    raise RuntimeError(
        f"{label}: no candidate became {state} within {timeout_ms}ms "
        f"({attempts} attempt(s)) [{describe(candidates)}]"
    )


def click_until_effect(
    page,
    candidates: Candidates,
    *,
    effect,
    label: str,
    timeout_ms: int = READY_TIMEOUT_MS,
) -> None:
    """Click the first ready candidate; accept only once ``effect`` is visible.

    Guards the failure mode a bare click cannot see: the click is delivered to
    a mounted-but-not-yet-hydrated node and silently swallowed, so ``.click()``
    reports success while nothing happens. A click is only accepted once its
    observable consequence — the modal it was supposed to open — is actually
    on screen; otherwise it is retried like any other failed attempt.

    ``effect`` is re-checked at the top of every round *before* clicking again,
    so a click whose effect merely arrived late is never followed by a second,
    redundant click into an already-open composer.
    """
    deadline = _now(page) + timeout_ms
    attempts = 0
    last_err = "no attempt made"
    while True:
        attempts += 1

        # Effect already satisfied (warm composer, or a previous round's click
        # landing late) — nothing to do.
        try:
            effect.first.wait_for(state="visible", timeout=ATTEMPT_TIMEOUT_MS)
            if attempts > 1:
                logger.info("✅ %s: effect present after %d attempt(s).", label, attempts)
            return
        except Exception:
            pass

        for name, loc in candidates:
            try:
                target = loc.first
                target.wait_for(state="visible", timeout=ATTEMPT_TIMEOUT_MS)
                target.click(timeout=CLICK_TIMEOUT_MS)
            except Exception as err:
                last_err = f"{name}: {type(err).__name__}"
                continue
            try:
                effect.first.wait_for(state="visible", timeout=EFFECT_TIMEOUT_MS)
            except Exception:
                # Click landed but had no visible effect — inert click, or the
                # platform is still hydrating. Retry rather than proceed into a
                # composer that isn't there.
                last_err = f"{name}: clicked, but the effect never became visible"
                logger.info("↻ %s: %s — retrying.", label, last_err)
                continue
            logger.debug("📝 %s: opened via %s (attempt %d).", label, name, attempts)
            return

        if _now(page) >= deadline:
            break
        page.wait_for_timeout(POLL_INTERVAL_MS)
    raise RuntimeError(
        f"{label}: could not reach the expected state within {timeout_ms}ms "
        f"({attempts} attempt(s); last: {last_err}) "
        f"[{describe(candidates)}; {describe([('effect', effect)])}]"
    )
