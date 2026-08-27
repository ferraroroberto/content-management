"""Newsletter Chrome bootstrap must wait out a held profile, never kill it (issue #244).

`ensure_chrome` used to `taskkill /F /T` any non-debug Chrome holding the
newsletter profile immediately, with no wait and no retry — violating the
fleet rule "never kill a live holder; wait with exponential backoff,
re-attempting each cycle, and only raise after the full schedule" (the
compliant pattern already lives in `config.chrome_profile_lock`). These tests
pin the fixed behaviour: wait, re-check, and only report (never kill) a
holder still present after the full backoff schedule.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from newsletter import bootstrap_chrome as bc  # noqa: E402


class WaitForProfileFreeTests(unittest.TestCase):
    def test_no_holder_returns_immediately_with_no_wait(self):
        with mock.patch.object(bc, "pids_holding_profile", return_value=[]) as m_pids, \
             mock.patch.object(bc.time, "sleep") as m_sleep:
            result = bc._wait_for_profile_free(backoff_seconds=(60, 120))
        self.assertEqual(result, [])
        m_sleep.assert_not_called()
        m_pids.assert_called_once()

    def test_holder_that_frees_during_the_schedule_is_never_killed(self):
        # Held on the initial check and after the first wait step, free after the second.
        with mock.patch.object(bc, "pids_holding_profile",
                                side_effect=[[111], [111], []]) as m_pids, \
             mock.patch.object(bc.time, "sleep") as m_sleep, \
             mock.patch("subprocess.run") as m_run:
            result = bc._wait_for_profile_free(backoff_seconds=(60, 120))
        self.assertEqual(result, [])
        self.assertEqual(m_sleep.call_args_list, [mock.call(60), mock.call(120)])
        self.assertEqual(m_pids.call_count, 3)
        m_run.assert_not_called()  # never taskkill'd

    def test_holder_still_present_after_full_schedule_is_reported_not_killed(self):
        with mock.patch.object(bc, "pids_holding_profile", return_value=[111]), \
             mock.patch.object(bc.time, "sleep") as m_sleep, \
             mock.patch("subprocess.run") as m_run:
            result = bc._wait_for_profile_free(backoff_seconds=(60, 120))
        self.assertEqual(result, [111])
        self.assertEqual(m_sleep.call_count, 2)
        m_run.assert_not_called()


class EnsureChromeReturnCodesTests(unittest.TestCase):
    def test_debug_port_already_up_is_an_immediate_success(self):
        with mock.patch.object(bc, "debug_port_up", return_value=True), \
             mock.patch.object(bc, "_wait_for_profile_free") as m_wait:
            rc = bc.ensure_chrome()
        self.assertEqual(rc, 0)
        m_wait.assert_not_called()

    def test_wedged_holder_after_full_schedule_returns_4_without_launching(self):
        with mock.patch.object(bc, "debug_port_up", return_value=False), \
             mock.patch.object(bc, "_wait_for_profile_free", return_value=[111]), \
             mock.patch.object(bc, "_find_chrome_exe") as m_find:
            rc = bc.ensure_chrome()
        self.assertEqual(rc, 4)
        m_find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
