r"""Characterization tests for the Instagram composer's post-upload settle wait.

``_upload_files`` waits ``_upload_settle_ms(n_files)`` after every successful
attach (all four legs, plus Leg 0's race-guard exit) so Meta can render the
preview thumbnails. The numbers (``3000 + 1500 * n``) predate the helper and are
pinned here so a refactor cannot shift the timing of the unattended scheduler.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from planning.instagram.schedule_instagram_posts import _upload_settle_ms

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "planning" / "instagram" / "schedule_instagram_posts.py"
)


def _settle_waits() -> list[str]:
    """Source of every ``wait_for_timeout`` argument in ``_upload_files`` that scales with the file count."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_upload_files"
    )
    return [
        ast.unparse(call.args[0])
        for call in ast.walk(fn)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "wait_for_timeout"
        and "paths" in ast.unparse(call.args[0])
    ]


class UploadSettleTests(unittest.TestCase):
    def test_settle_wait_values(self) -> None:
        self.assertEqual([_upload_settle_ms(n) for n in (1, 2, 10)], [4500, 6000, 18000])

    def test_all_five_upload_exits_go_through_the_helper(self) -> None:
        self.assertEqual(_settle_waits(), ["_upload_settle_ms(len(paths))"] * 5)


if __name__ == "__main__":
    unittest.main()
