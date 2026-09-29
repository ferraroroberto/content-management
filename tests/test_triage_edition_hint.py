"""Regression test for issue #321 finding 7: the live triage report's "target
edition" hint came from ``db.next_edition_number()``, which reads
``triage_editions`` — a table filled once by ``import-history`` and never
refreshed by anything in the run/review flow. Every live report since
2026-08-07 showed the same stale "N232".

Fixed by no longer calling it from ``run.py`` at all: ``run_window`` leaves
``edition_hint`` unset, and ``_run_window_body`` already falls back to the
honest "next free edition" label rather than a fabricated number.

This is a static AST check (matching the pattern in
``test_subprocess_no_window.py`` / ``test_instagram_schedule_guard.py``)
rather than a call-through test, because exercising ``run_window`` end-to-end
needs a live/mocked Supabase store several layers deep (``register_run``,
``store_run_results``, ``mark_run``) that isn't worth faking just to prove a
function is never called — the AST scan proves that directly.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

RUN_MODULE_PATH = (
    Path(__file__).resolve().parent.parent / "newsletter" / "triage" / "run.py"
)


class EditionHintStalenessTests(unittest.TestCase):
    def test_run_module_never_calls_next_edition_number(self):
        tree = ast.parse(RUN_MODULE_PATH.read_text(encoding="utf-8"))
        offenders = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "next_edition_number"
        ]
        self.assertEqual(
            offenders, [],
            "run.py must not call db.next_edition_number() — it reads a table "
            "that's never refreshed, so every live report would show the same "
            "stale edition number again (issue #321)",
        )


if __name__ == "__main__":
    unittest.main()
