"""Behavioural tests for the post-Schedule confirmation wait (issue #178).

The first real ``--live`` run reported 2 of 7 rows as failures that had in fact
been scheduled: the composer-count signal never resolved, the 20 s budget ran
out, and the caller then skipped the Work-in-Progress untick — queueing both
rows for a duplicate on the next run. The replacement in
``linkedin_composer.wait_for_schedule_confirmation`` adds LinkedIn's own "Post
scheduled" toast as a positive signal and refuses to infer success from a
baseline it never established.

None of that is reachable from the dry run, which discards instead of
scheduling and so never enters this code path at all. These tests drive the
waiter directly against a fake page whose clock only advances when the waiter
polls, so the timeout branch is exercised without waiting on a real one.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest

from planning.linkedin.linkedin_composer import (
    SCHEDULE_CONFIRM_TIMEOUT_MS,
    schedule_pre_state,
    wait_for_schedule_confirmation,
)


class _FakeLocator:
    """Locator whose ``count()`` follows a caller-supplied schedule.

    ``counts`` is a list consumed one entry per call, holding the final value
    once exhausted — so a test can say "0 for the first two polls, then 1".
    """

    def __init__(self, counts: list[int]):
        self._counts = list(counts)

    def count(self) -> int:
        if len(self._counts) > 1:
            return self._counts.pop(0)
        return self._counts[0]


class _FakePage:
    """Minimal Playwright ``Page`` stand-in with a virtual clock.

    The clock advances only inside ``wait_for_timeout``, which is exactly how
    the waiter's poll loop drives it — so a 45 s timeout costs no real time.
    """

    def __init__(self, toast: _FakeLocator):
        self._now = 0
        self._toast = toast

    def evaluate(self, _expr: str) -> int:
        return self._now

    def wait_for_timeout(self, ms: int) -> None:
        self._now += ms

    def get_by_text(self, _pattern) -> _FakeLocator:
        return self._toast


class ScheduleConfirmationTests(unittest.TestCase):

    def test_toast_confirms_the_row(self):
        """LinkedIn's own toast ends the wait, even with the composer stuck open."""
        toast = _FakeLocator([0, 0, 1])
        page = _FakePage(toast)
        composer = _FakeLocator([1])  # never closes
        signal = wait_for_schedule_confirmation(
            page, composer, (1, False), label="20260728",
        )
        self.assertEqual(signal, "toast")

    def test_composer_closing_confirms_the_row(self):
        """The original signal still works when the count actually drops."""
        toast = _FakeLocator([0])
        page = _FakePage(toast)
        composer = _FakeLocator([1, 1, 0])
        signal = wait_for_schedule_confirmation(
            page, composer, (1, False), label="20260728",
        )
        self.assertEqual(signal, "composer-closed")

    def test_zero_baseline_never_infers_success(self):
        """A composer that was never matched cannot confirm itself.

        This is the regression that matters most: with a zero baseline both
        "count dropped" and "count is zero" are vacuously unusable, and an
        earlier shape of this fix would have returned success immediately —
        silently unticking a row whose post may never have been created.
        """
        toast = _FakeLocator([0])
        page = _FakePage(toast)
        composer = _FakeLocator([0])
        with self.assertRaises(RuntimeError):
            wait_for_schedule_confirmation(
                page, composer, (0, False), label="20260728",
            )

    def test_zero_baseline_still_accepts_the_toast(self):
        """With no usable composer baseline, the toast alone may confirm."""
        toast = _FakeLocator([0, 1])
        page = _FakePage(toast)
        composer = _FakeLocator([0])
        signal = wait_for_schedule_confirmation(
            page, composer, (0, False), label="20260728",
        )
        self.assertEqual(signal, "toast")

    def test_stale_toast_from_previous_row_is_ignored(self):
        """A toast already on screen before the click confirms nothing.

        Rows share one browser session, so the previous row's toast can still
        be visible. Trusting it would confirm a row that never got clicked.
        """
        toast = _FakeLocator([1])  # visible the whole time
        page = _FakePage(toast)
        composer = _FakeLocator([1])  # never closes
        with self.assertRaises(RuntimeError):
            wait_for_schedule_confirmation(
                page, composer, (1, True), label="20260728",
            )

    def test_stale_toast_does_not_block_the_composer_signal(self):
        """Ignoring a stale toast must not disable the other signal."""
        toast = _FakeLocator([1])
        page = _FakePage(toast)
        composer = _FakeLocator([1, 0])
        signal = wait_for_schedule_confirmation(
            page, composer, (1, True), label="20260728",
        )
        self.assertEqual(signal, "composer-closed")

    def test_timeout_message_does_not_claim_the_post_failed(self):
        """The old wording asserted a failure it could not observe.

        It read "post likely NOT scheduled" on rows that *were* scheduled. The
        operator has to check LinkedIn, and needs to know the row stays queued.
        """
        toast = _FakeLocator([0])
        page = _FakePage(toast)
        composer = _FakeLocator([1])
        with self.assertRaises(RuntimeError) as ctx:
            wait_for_schedule_confirmation(
                page, composer, (1, False), label="20260728",
            )
        message = str(ctx.exception)
        self.assertIn("MAY still have been scheduled", message)
        self.assertIn("Work-in-Progress", message)
        self.assertNotIn("NOT scheduled", message)

    def test_timeout_honours_the_configured_budget(self):
        """The wait runs to the documented budget, not the old hardcoded 20s."""
        toast = _FakeLocator([0])
        page = _FakePage(toast)
        composer = _FakeLocator([1])
        with self.assertRaises(RuntimeError):
            wait_for_schedule_confirmation(
                page, composer, (1, False), label="20260728",
            )
        self.assertGreaterEqual(page._now, SCHEDULE_CONFIRM_TIMEOUT_MS)
        self.assertGreaterEqual(SCHEDULE_CONFIRM_TIMEOUT_MS, 45000)


class SchedulePreStateTests(unittest.TestCase):

    def test_snapshots_both_signals(self):
        toast = _FakeLocator([1])
        page = _FakePage(toast)
        composer = _FakeLocator([2])
        self.assertEqual(schedule_pre_state(page, composer), (2, True))

    def test_absent_toast_is_reported_absent(self):
        toast = _FakeLocator([0])
        page = _FakePage(toast)
        composer = _FakeLocator([1])
        self.assertEqual(schedule_pre_state(page, composer), (1, False))

    def test_survives_a_locator_that_raises(self):
        """A snapshot must never be the thing that fails the row.

        It runs between the Confirm click and the Schedule click; raising here
        would abort a post that is one click from being scheduled.
        """
        class _Exploding:
            def count(self):
                raise RuntimeError("locator detached")

        page = _FakePage(_Exploding())
        self.assertEqual(schedule_pre_state(page, _Exploding()), (0, False))


if __name__ == "__main__":
    unittest.main()
