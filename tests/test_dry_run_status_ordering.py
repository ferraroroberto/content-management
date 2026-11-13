"""Regression tests for issue #321 findings 3 and 4: a failed platform leg
must never read as a clean ``DRY``/pipeline-success status during a dry run.

**Instagram** (``schedule_instagram_posts.py``): the row status used to be an
inline if/elif chain that checked ``dry_run`` before ``any_fail``, so a dry run
with a failed leg was tagged ``DRY``. It now comes from the same
``derive_row_status`` as Videos, so the proof is behavioural: a dry run with a
failed story or post leg must not read as ``DRY``, and the driver must hand its
legs to the derive rather than test free-text leg strings.

**Videos** (``schedule_videos_posts.py``): ``_aggregate_row_status`` is a
standalone pure function, so it gets a direct behavioural test — a dry run
with one ``DRY`` leg and one ``FAIL`` leg (e.g. LI:DRY, IG:FAIL) must report
a failure (``PARTIAL``), not ``DRY``.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import ast
import unittest
from datetime import date
from pathlib import Path

from tools.planning.row_outcome import RowStatus, derive_row_status
from tools.planning.videos.schedule_videos_posts import _aggregate_row_status, _RowState

INSTAGRAM_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tools" / "planning" / "instagram" / "schedule_instagram_posts.py"
)


class InstagramRowStatusTests(unittest.TestCase):
    def test_failed_leg_in_a_dry_run_is_not_a_clean_dry(self):
        for legs in ([RowStatus.DRY, RowStatus.FAIL], [RowStatus.FAIL, RowStatus.DRY],
                     [RowStatus.FAIL, RowStatus.FAIL]):
            self.assertNotEqual(derive_row_status(legs, dry_run=True), RowStatus.DRY)

    def test_driver_derives_the_row_status_from_its_legs(self):
        tree = ast.parse(INSTAGRAM_MODULE_PATH.read_text(encoding="utf-8"))
        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "derive_row_status"
        ]
        self.assertEqual(len(calls), 1)


class VideosAggregateRowStatusDryRunTests(unittest.TestCase):
    def _row_state(self, **driver_status: str) -> _RowState:
        state = _RowState(
            page_id="page-1", day=date(2026, 9, 29),
            payload=None, link_status={}, in_scope={},
        )
        state.driver_status = dict(driver_status)
        return state

    def test_mixed_dry_and_fail_is_partial_not_dry(self):
        state = self._row_state(li="DRY", ig="FAIL", tw="SKIP", th="SKIP")
        status, _detail = _aggregate_row_status(state, dry_run=True)
        self.assertEqual(status, "PARTIAL")

    def test_fail_with_no_dry_leg_falls_back_to_fail(self):
        # No leg went dry-clean, so there's nothing to call PARTIAL against.
        state = self._row_state(li="FAIL", ig="LIVE", tw="SKIP", th="SKIP")
        status, _detail = _aggregate_row_status(state, dry_run=True)
        self.assertEqual(status, "FAIL")

    def test_clean_dry_run_is_still_dry(self):
        state = self._row_state(li="DRY", ig="DRY", tw="DRY", th="DRY")
        status, _detail = _aggregate_row_status(state, dry_run=True)
        self.assertEqual(status, "DRY")


if __name__ == "__main__":
    unittest.main()
