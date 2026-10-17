"""Characterization tests for the planning pipeline's clone step (issue #406).

Clone's ``main()`` returns a bare exit code where the schedulers return
``(rc, rows)``; these pin the ``PlatformResult`` the orchestrator records for
each outcome so routing clone through the shared ``_run_platform`` runner
keeps the summary and result JSON unchanged.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import argparse
import logging
import sys
import unittest
from unittest import mock

import planning_pipeline


def _args(**overrides) -> argparse.Namespace:
    base = dict(skip_clone=False, live=False, dry_run=True, debug=False, force=False)
    base.update(overrides)
    return argparse.Namespace(**base)


class CloneStepTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(
            planning_pipeline, "logger", logging.getLogger("test_clone_step"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, clone_fn, **overrides) -> planning_pipeline.PlatformResult:
        with mock.patch.object(planning_pipeline, "run_clone", clone_fn):
            return planning_pipeline._run_clone(_args(**overrides))

    def test_skip_flag_records_skipped_without_running(self):
        clone = mock.Mock(return_value=0)
        r = self._run(clone, skip_clone=True)
        clone.assert_not_called()
        self.assertEqual((r.name, r.skipped, r.exit_code, r.error), ("Clone", True, 0, None))

    def test_success_records_clean_result(self):
        r = self._run(mock.Mock(return_value=0))
        self.assertEqual((r.name, r.skipped, r.exit_code, r.rows, r.error),
                         ("Clone", False, 0, [], None))
        self.assertFalse(planning_pipeline._platform_failed(r))

    def test_nonzero_exit_records_exit_code_as_error(self):
        r = self._run(mock.Mock(return_value=11))
        self.assertEqual((r.exit_code, r.rows, r.error), (11, [], "exit code 11"))
        self.assertTrue(planning_pipeline._platform_failed(r))

    def test_system_exit_is_swallowed(self):
        r = self._run(mock.Mock(side_effect=SystemExit(4)))
        self.assertEqual((r.exit_code, r.error), (4, "SystemExit(4)"))

    def test_non_int_system_exit_maps_to_99(self):
        r = self._run(mock.Mock(side_effect=SystemExit("boom")))
        self.assertEqual((r.exit_code, r.error), (99, "SystemExit(99)"))

    def test_exception_is_swallowed(self):
        r = self._run(mock.Mock(side_effect=RuntimeError("notion down")))
        self.assertEqual((r.exit_code, r.rows, r.error), (99, [], "notion down"))

    def test_clone_sees_its_own_argv_and_argv_is_restored(self):
        seen: list[list[str]] = []

        def clone() -> int:
            seen.append(sys.argv[1:])
            return 0

        before = sys.argv.copy()
        self._run(clone, live=True, dry_run=False, force=True)
        self.assertEqual(seen, [["--live", "--force"]])
        self.assertEqual(sys.argv, before)

    def test_summary_shows_clone_exit_code_as_platform_error(self):
        r = self._run(mock.Mock(return_value=2))
        now = planning_pipeline.datetime.now()
        md = planning_pipeline._build_summary_md(_args(), [r], now, now)
        self.assertIn("> Platform-level error: `exit code 2`", md)
        self.assertNotIn("idempotent no-op", md)


if __name__ == "__main__":
    unittest.main()
