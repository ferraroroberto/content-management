"""ALT text is best-effort when LinkedIn's photo editor crashes (issue #270).

Since 2026-09 the ALT panel's 'Add' throws ``x.create is not a function``
inside LinkedIn's own bundle and the whole app unmounts to a full-page
"Something went wrong / Try again" screen. The scheduler used to plough on to
'Next', time out against the crash screen, and fail the row — every ILL/POST
row carrying ALT text went unscheduled while ALT-less rows sailed through.

These tests pin the three links of the fix: the crash is told apart from a
healthy (or merely slow) editor, ``_set_alt_text`` turns it into a typed error,
and the row driver re-runs once without ALT text instead of failing.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from planning.linkedin import schedule_linkedin_posts as sl


class _FakePage:
    """Virtual clock + a crash screen that appears at ``crash_at`` ms."""

    def __init__(self, crash_at: float | None) -> None:
        self.now = 0.0
        self._crash = _FakeLocator(self, crash_at)

    def evaluate(self, _expr: str) -> float:
        return self.now

    def wait_for_timeout(self, ms: float) -> None:
        self.now += ms

    def get_by_role(self, role: str, *, name) -> "_FakeLocator":
        assert role == "button" and name is sl.TRY_AGAIN_RE
        return self._crash


class _FakeLocator:
    """Visible from ``visible_at`` ms on the page's clock; ``None`` = never."""

    def __init__(self, page: _FakePage, visible_at: float | None) -> None:
        self._page = page
        self._visible_at = visible_at

    def _on(self) -> bool:
        return self._visible_at is not None and self._visible_at <= self._page.now

    def count(self) -> int:
        return 1 if self._on() else 0

    def nth(self, _i: int) -> "_FakeLocator":
        return self

    def is_visible(self) -> bool:
        return self._on()


class AltCommitCrashDetectionTests(unittest.TestCase):

    def _crashed(self, *, next_at, crash_at) -> tuple[bool, float]:
        page = _FakePage(crash_at)
        editor_next = _FakeLocator(page, next_at)
        with mock.patch.object(sl, "_dialog_next_button", return_value=editor_next):
            return sl._alt_commit_crashed(page), page.now

    def test_editor_back_is_not_a_crash(self):
        crashed, elapsed = self._crashed(next_at=0, crash_at=None)
        self.assertFalse(crashed)
        self.assertEqual(elapsed, 0, "a healthy editor must not cost the settle window")

    def test_crash_screen_is_a_crash(self):
        self.assertTrue(self._crashed(next_at=None, crash_at=0)[0])

    def test_late_crash_is_still_caught(self):
        """The live crash landed ~2 s after the click — inside the window."""
        crashed, elapsed = self._crashed(next_at=None, crash_at=2000)
        self.assertTrue(crashed)
        self.assertLess(elapsed, sl.ALT_COMMIT_SETTLE_MS)

    def test_editor_wins_over_a_stray_try_again(self):
        self.assertFalse(self._crashed(next_at=0, crash_at=0)[0])

    def test_neither_is_not_misreported_as_a_crash(self):
        """A slow/odd editor falls through to 'Next', which reports its own error."""
        crashed, elapsed = self._crashed(next_at=None, crash_at=None)
        self.assertFalse(crashed)
        self.assertGreaterEqual(elapsed, sl.ALT_COMMIT_SETTLE_MS)


class SetAltTextRaisesOnCrashTests(unittest.TestCase):

    def _run(self, crashed: bool) -> None:
        with mock.patch.object(sl, "_dialog_button"), \
             mock.patch.object(sl, "_dialog"), \
             mock.patch.object(sl, "_alt_commit_crashed", return_value=crashed):
            sl._set_alt_text(object(), "a chart of you outgrowing your job title")

    def test_crash_raises_typed_error(self):
        with self.assertRaises(sl.AltTextCrashError):
            self._run(crashed=True)

    def test_healthy_commit_returns(self):
        self._run(crashed=False)

    def test_typed_error_is_still_a_row_failure_if_it_escapes(self):
        self.assertTrue(issubclass(sl.AltTextCrashError, RuntimeError))


class RowFallbackTests(unittest.TestCase):

    ILLUST = sl.IllustrationData(
        image_filename="chart.png", alt_text="alt words", caption_text="caption",
    )

    def _schedule(self, drive) -> tuple[str, mock.Mock]:
        row = mock.Mock(day_title="20260919")
        with mock.patch.object(sl, "_drive_photo_row", side_effect=drive) as spy:
            status = sl.schedule_one_illustration_row(
                mock.Mock(), {}, row, self.ILLUST, Path("chart.png"), dry_run=False,
            )
        return status, spy

    def test_crash_redrives_once_without_alt(self):
        def drive(_s, _c, row, illust, _p, **_kw):
            if illust.alt_text:
                raise sl.AltTextCrashError("editor crashed")
            return f"{row.day_title}: LIVE scheduled"

        status, spy = self._schedule(drive)
        self.assertEqual(spy.call_count, 2)
        self.assertEqual(spy.call_args_list[0].args[3].alt_text, "alt words")
        self.assertEqual(spy.call_args_list[1].args[3].alt_text, "")
        self.assertIn("ALT text dropped", status)
        # The live loop unticks WIP-LI in Notion on this exact substring.
        self.assertIn("LIVE scheduled", status)

    def test_no_crash_keeps_alt_and_single_pass(self):
        status, spy = self._schedule(lambda *_a, **_k: "20260919: LIVE scheduled")
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args.args[3].alt_text, "alt words")
        self.assertEqual(status, "20260919: LIVE scheduled")

    def test_other_failures_are_not_retried(self):
        with self.assertRaises(RuntimeError):
            self._schedule(mock.Mock(side_effect=RuntimeError("Could not open the Schedule dialog")))


if __name__ == "__main__":
    unittest.main()
