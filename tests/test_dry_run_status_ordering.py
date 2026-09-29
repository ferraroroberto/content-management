"""Regression tests for issue #321 findings 3 and 4: a failed platform leg
must never read as a clean ``DRY``/pipeline-success status during a dry run.

**Instagram** (``schedule_instagram_posts.py``): the row-status if/elif chain
checked ``dry_run`` before ``any_fail``, so a dry run with a failed leg was
tagged ``DRY`` and skipped the ``failed`` filter entirely — a real failure
read as a clean run. Fixed by testing ``any_fail`` first. This module builds
the status inline in a long scheduling loop (no standalone function to call),
so the regression proof is structural: parse the AST and assert the ``if``
chain's first test is ``any_fail``, not ``dry_run``.

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

from planning.videos.schedule_videos_posts import _aggregate_row_status, _RowState

INSTAGRAM_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "planning" / "instagram" / "schedule_instagram_posts.py"
)


def _find_row_status_if_chain(tree: ast.Module) -> ast.If:
    """Find the ``if ...: row_status = ...`` chain (an ``If`` node whose body
    assigns to a name ``row_status``, followed by ``elif`` branches)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == "row_status"
            ):
                return node
    raise AssertionError("no `if ...: row_status = ...` chain found")


def _test_name(test_expr: ast.expr) -> str:
    """Best-effort name of a simple ``Name`` test expression (e.g. `any_fail`)."""
    if isinstance(test_expr, ast.Name):
        return test_expr.id
    return ast.dump(test_expr)


class InstagramRowStatusOrderingTests(unittest.TestCase):
    def test_any_fail_is_checked_before_dry_run(self):
        tree = ast.parse(INSTAGRAM_MODULE_PATH.read_text(encoding="utf-8"))
        chain = _find_row_status_if_chain(tree)
        first_test = _test_name(chain.test)
        self.assertEqual(
            first_test, "any_fail",
            "row_status if-chain must test `any_fail` first, or a failed leg "
            "in a dry run reads as a clean DRY row (issue #321)",
        )


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
