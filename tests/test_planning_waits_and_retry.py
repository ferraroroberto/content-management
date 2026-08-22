"""Behavioural tests for the bounded readiness waits + row retry (issue #235).

The 2026-08-22 planning run lost 3 rows to the same mechanism: a readiness test
built on ``Locator.count()``, which returns *immediately* and never waits. An X
row failed in ~10 s (its six siblings took ~14 s) because the app shell had not
hydrated yet, and a Threads row failed because the composer modal's shell had
mounted while its ``contenteditable`` had not.

The fakes below model exactly that: an element that becomes ready at a chosen
point on a virtual clock. ``test_waits_for_a_late_element`` is the regression
proof — its element is absent at t=0, so the old ``if loc.count():`` probe would
have seen 0 and raised, while the replacement finds it on a later round.

The clock only advances inside the fakes' own waits, so a full 20 s timeout
costs no real time — the same trick ``test_schedule_confirmation.py`` uses.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest

from planning._failure import PostMayBeLiveError, attempt_row, classify
from planning._waits import (
    READY_TIMEOUT_MS,
    click_until_effect,
    wait_for_first_ready,
)


class _FakeTimeout(Exception):
    """Stand-in for Playwright's TimeoutError."""


class _FakePage:
    """Minimal Playwright ``Page`` with a virtual clock in milliseconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def evaluate(self, _expr: str) -> float:
        return self.now

    def wait_for_timeout(self, ms: float) -> None:
        self.now += ms

    def advance(self, ms: float) -> None:
        self.now += ms


class _FakeLocator:
    """Element that becomes ready at ``ready_at`` on the page's virtual clock.

    ``ready_at=None`` means "never appears" — the UI-drift case.
    """

    def __init__(self, page: _FakePage, ready_at: float | None = 0.0) -> None:
        self._page = page
        self._ready_at = ready_at
        self.clicks = 0

    # Playwright locators are lazy; ``.first`` re-resolves rather than snapshots.
    @property
    def first(self) -> "_FakeLocator":
        return self

    def _ready(self) -> bool:
        return self._ready_at is not None and self._ready_at <= self._page.now

    def count(self) -> int:
        return 1 if self._ready() else 0

    def wait_for(self, *, state: str = "visible", timeout: float = 0) -> None:
        if self._ready():
            return
        if self._ready_at is not None and self._ready_at <= self._page.now + timeout:
            self._page.advance(self._ready_at - self._page.now)
            return
        self._page.advance(timeout)
        raise _FakeTimeout(f"not {state} within {timeout}ms")

    def click(self, timeout: float = 0) -> None:
        if not self._ready():
            raise _FakeTimeout("not clickable")
        self.clicks += 1
        self._page.advance(50)


class _FakeButton(_FakeLocator):
    """A button whose click arms an effect locator after ``delay`` ms.

    ``delay=None`` models the inert click: the click lands and reports success,
    but nothing ever happens — the failure mode a bare ``.click()`` cannot see.
    """

    def __init__(self, page, effect: _FakeLocator, *, delay: float | None = 0.0,
                 ready_at: float = 0.0) -> None:
        super().__init__(page, ready_at)
        self._effect = effect
        self._delay = delay

    def click(self, timeout: float = 0) -> None:
        super().click(timeout)
        if self._delay is not None:
            self._effect._ready_at = self._page.now + self._delay


class WaitForFirstReadyTests(unittest.TestCase):

    def test_waits_for_a_late_element(self):
        """Regression for #235: absent at t=0, present shortly after.

        A bare ``count()`` probe — the pre-fix readiness test — returns 0 here
        and the driver raises. The bounded wait must find it instead.
        """
        page = _FakePage()
        late = _FakeLocator(page, ready_at=5000)
        self.assertEqual(late.count(), 0, "precondition: absent at t=0")

        got = wait_for_first_ready(
            page, [("late", late)], label="composer",
        )
        self.assertIs(got, late)
        self.assertLessEqual(page.now, READY_TIMEOUT_MS)

    def test_honours_candidate_order(self):
        page = _FakePage()
        preferred = _FakeLocator(page, ready_at=0)
        fallback = _FakeLocator(page, ready_at=0)
        got = wait_for_first_ready(
            page,
            [("preferred", preferred), ("fallback", fallback)],
            label="composer",
        )
        self.assertIs(got, preferred)

    def test_falls_through_to_a_later_candidate(self):
        page = _FakePage()
        never = _FakeLocator(page, ready_at=None)
        works = _FakeLocator(page, ready_at=0)
        got = wait_for_first_ready(
            page, [("never", never), ("works", works)], label="composer",
        )
        self.assertIs(got, works)

    def test_timeout_message_reports_what_it_saw(self):
        """The failure must distinguish drift from a race without a screenshot."""
        page = _FakePage()
        missing = _FakeLocator(page, ready_at=None)
        with self.assertRaises(RuntimeError) as ctx:
            wait_for_first_ready(
                page, [("side-rail", missing)], label="X compose area",
                timeout_ms=3000,
            )
        msg = str(ctx.exception)
        self.assertIn("X compose area", msg)
        self.assertIn("side-rail", msg)
        self.assertIn("count=0", msg)

    def test_timeout_is_bounded(self):
        page = _FakePage()
        missing = _FakeLocator(page, ready_at=None)
        with self.assertRaises(RuntimeError):
            wait_for_first_ready(
                page, [("missing", missing)], label="x", timeout_ms=3000,
            )
        # Generous ceiling: the loop may overshoot by one attempt + settle.
        self.assertLess(page.now, 3000 * 3)


class ClickUntilEffectTests(unittest.TestCase):

    def test_clicks_once_when_the_effect_follows(self):
        page = _FakePage()
        effect = _FakeLocator(page, ready_at=None)
        button = _FakeButton(page, effect, delay=200)
        click_until_effect(
            page, [("side-rail", button)], effect=effect, label="X compose modal",
        )
        self.assertEqual(button.clicks, 1)

    def test_does_not_click_when_the_effect_is_already_present(self):
        """Guards against a second, redundant click into an open composer."""
        page = _FakePage()
        effect = _FakeLocator(page, ready_at=0)
        button = _FakeButton(page, effect, delay=0)
        click_until_effect(
            page, [("side-rail", button)], effect=effect, label="X compose modal",
        )
        self.assertEqual(button.clicks, 0)

    def test_retries_an_inert_click(self):
        """Click lands and reports success, but nothing happens — must retry."""
        page = _FakePage()
        effect = _FakeLocator(page, ready_at=None)
        inert = _FakeButton(page, effect, delay=None)
        with self.assertRaises(RuntimeError) as ctx:
            click_until_effect(
                page, [("inert", inert)], effect=effect,
                label="X compose modal", timeout_ms=6000,
            )
        self.assertGreater(inert.clicks, 1, "an inert click must be retried")
        self.assertIn("effect never became visible", str(ctx.exception))

    def test_waits_for_a_button_that_mounts_late(self):
        """The X-splash-screen case: nothing is mounted on the first round."""
        page = _FakePage()
        effect = _FakeLocator(page, ready_at=None)
        button = _FakeButton(page, effect, delay=200, ready_at=4000)
        click_until_effect(
            page, [("side-rail", button)], effect=effect, label="X compose modal",
        )
        self.assertEqual(button.clicks, 1)


class AttemptRowTests(unittest.TestCase):

    def test_retries_a_transient_failure(self):
        calls = []

        def run():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("Could not open the X compose area.")
            return "post:LIVE"

        self.assertEqual(attempt_row(run, label="20260830"), "post:LIVE")
        self.assertEqual(len(calls), 2)

    def test_never_retries_a_committed_post(self):
        """The double-post guard — the one regression that must never happen."""
        calls = []

        def run():
            calls.append(1)
            raise PostMayBeLiveError("Composer did not clear")

        with self.assertRaises(PostMayBeLiveError):
            attempt_row(run, label="20260830")
        self.assertEqual(calls, [1], "a committed post must never be re-attempted")

    def test_reraises_after_the_final_attempt(self):
        calls = []

        def run():
            calls.append(1)
            raise RuntimeError("still broken")

        with self.assertRaises(RuntimeError) as ctx:
            attempt_row(run, label="20260830", attempts=3)
        self.assertEqual(len(calls), 3)
        self.assertIn("still broken", str(ctx.exception))

    def test_resets_between_attempts(self):
        """A half-filled composer must be cleared or the retry inherits it."""
        order = []

        def run():
            order.append("run")
            if order.count("run") == 1:
                raise RuntimeError("transient")
            return "ok"

        attempt_row(run, label="20260830", reset=lambda: order.append("reset"))
        self.assertEqual(order, ["run", "reset", "run"])

    def test_reset_failure_does_not_mask_the_retry(self):
        calls = []

        def run():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("transient")
            return "ok"

        def boom():
            raise RuntimeError("cancel failed")

        self.assertEqual(attempt_row(run, label="x", reset=boom), "ok")

    def test_committed_failure_still_classifies(self):
        """The heal loop reads failure_kind — a committed row must not become
        auto-heal-eligible just because it now carries a different type."""
        self.assertEqual(
            classify("FAIL", "Composer did not clear — see shot.png"), "other"
        )


if __name__ == "__main__":
    unittest.main()
