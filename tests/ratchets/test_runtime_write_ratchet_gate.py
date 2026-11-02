"""The pre-ship gate's runtime-write-ratchet stage and the vendored script behind it.

Pinned here: the wrapper fails exit 3 (a write target it could not resolve) as NOT
CONFIRMED rather than skipping it; the README runs the wrapper, not the script; both
vendored files are declared; and the real tree is green against the committed
baseline. The script's own detection is tested in project-scaffolding, where it is
authored.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import io
import subprocess
import sys
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import runtime_write_ratchet_gate as gate  # noqa: E402


class ExitMappingTests(unittest.TestCase):
    def test_only_exit_zero_passes_and_three_is_its_own_not_confirmed_state(self) -> None:
        verdicts = {code: gate.classify_exit(code) for code in (0, 1, 2, 3, 4, -1)}
        self.assertEqual([code for code, (ok, _) in verdicts.items() if ok], [gate.EXIT_PASS])
        not_confirmed = verdicts[gate.EXIT_NOT_CONFIRMED][1]
        self.assertIn("NOT CONFIRMED", not_confirmed)
        self.assertNotIn("skip", not_confirmed.lower())

    def test_a_script_that_cannot_finish_fails(self) -> None:
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(gate.run(), 1)
            self.assertIn("FAIL: runtime write ratchet", out.getvalue())


class WiringTests(unittest.TestCase):
    def test_the_readme_gate_calls_the_wrapper_not_the_script(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        commands = [line for line in readme.splitlines() if "runtime_write_ratchet" in line and "-m " in line]
        self.assertTrue(any("-m scripts.runtime_write_ratchet_gate" in line for line in commands))

    def test_the_ratchet_is_declared_and_both_scripts_are_vendored(self) -> None:
        fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
        table = fleet["runtime_write_ratchet"]
        self.assertIs(table["enabled"], True)
        self.assertTrue((REPO_ROOT / table["baseline"]).is_file())
        for name in ("runtime_write_ratchet", "runtime_write_resolve"):
            entry = fleet["vendored"][name]
            self.assertEqual(entry["dest"], f"scripts/{name}.py")
            self.assertTrue((REPO_ROOT / entry["dest"]).is_file())

    def test_the_committed_baseline_is_current(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(gate.run(), 0)


if __name__ == "__main__":
    unittest.main()
