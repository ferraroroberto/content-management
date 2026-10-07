"""``run_module`` records a step that returns a non-zero int exit code (issue #370).

``planning.substack.daily_pipeline.main`` returns an int (99 when a Note branch
raised, non-zero on a failed post); only ``False`` used to count as a failure.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import reporting_pipeline as rp


class RunModuleExitCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        rp.logger = MagicMock()

    def _run(self, result) -> rp.PipelineFailures:
        failures = rp.PipelineFailures()
        rp.run_module(lambda: result, "Step", failures=failures)
        return failures

    def test_non_zero_int_is_a_step_failure(self) -> None:
        failures = self._run(99)
        self.assertEqual(failures.step_failures, [("Step", "step reported failure (exit code 99)")])

    def test_false_is_still_a_step_failure(self) -> None:
        self.assertEqual(len(self._run(False).step_failures), 1)

    def test_zero_none_and_true_are_success(self) -> None:
        for result in (0, None, True):
            with self.subTest(result=result):
                self.assertEqual(self._run(result).step_failures, [])


if __name__ == "__main__":
    unittest.main()
