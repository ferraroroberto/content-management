"""The request-budget stage of the pre-ship gate, and the vendored script behind it.

Pinned here: an unreadable-source exit (3) fails as NOT CONFIRMED rather than being
skipped; the README runs the wrapper, not the script; the real tree is green against its
committed baseline; and a seeded fixed-rate poll turns the real script red by rule name
until it is reverted. The seeding runs inside a throwaway git repo.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.ratchets.test_request_budget_ratchet_gate -v
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import request_budget_ratchet_gate as gate  # noqa: E402
from tests.ratchets.test_structure_ratchet_gate import GrowthTurnsTheGateRedTests  # noqa: E402


class ExitMappingTests(unittest.TestCase):
    def test_only_a_clean_exit_passes(self) -> None:
        passing = [code for code in range(-1, 5) if gate.classify_exit(code)[0]]
        self.assertEqual(passing, [gate.EXIT_PASS])

    def test_exit_three_reads_as_not_confirmed(self) -> None:
        label = gate.classify_exit(gate.EXIT_NOT_CONFIRMED)[1]
        self.assertIn("NOT CONFIRMED", label)
        self.assertNotIn("skip", label.lower())

    def test_a_run_that_cannot_complete_fails_with_its_reason(self) -> None:
        for reason, error in (("could not start", OSError("gone")), ("did not finish", subprocess.TimeoutExpired("x", 1))):
            with self.subTest(reason=reason), patch.object(subprocess, "run", side_effect=error):
                out = io.StringIO()
                with redirect_stdout(out):
                    exit_code = gate.run()
                self.assertEqual(exit_code, 1)
                self.assertIn("FAIL: request budget ratchet", out.getvalue())
                self.assertIn(reason, out.getvalue())


class WiringTests(unittest.TestCase):
    def test_the_readme_gate_calls_the_wrapper_not_the_script(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        commands = [line for line in readme.splitlines() if "request_budget_ratchet" in line and "-m " in line]
        self.assertTrue(any("-m scripts.request_budget_ratchet_gate" in line for line in commands))
        self.assertFalse(any("scripts/request_budget_ratchet.py" in line for line in commands))

    def test_the_ratchet_is_enabled_and_the_pieces_are_vendored(self) -> None:
        fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
        table = fleet["request_budget_ratchet"]
        self.assertIs(table["enabled"], True)
        self.assertTrue((REPO_ROOT / table["baseline"]).is_file())
        for key in ("request_budget_ratchet", "request_budget_scan", "request_timing", "poll", "ratchet_baseline"):
            with self.subTest(key=key):
                self.assertTrue((REPO_ROOT / fleet["vendored"][key]["dest"]).exists())

    def test_the_committed_baseline_is_current(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(gate.run(), 0)
        self.assertIn("none new", out.getvalue())


class SeededPollTurnsTheGateRedTests(unittest.TestCase):
    """The real vendored script on a throwaway git repo that starts with no hits."""

    CLEAN = "export const x = 1;\n"
    POLL = "setInterval(() => fetch('/api/x'), 500);\n"

    # the sibling stage's throwaway-repo helpers, shared rather than copied
    _sh = GrowthTurnsTheGateRedTests._sh
    _git = GrowthTurnsTheGateRedTests._git

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / "scripts").mkdir()
        for name in ("request_budget_ratchet.py", "request_budget_scan.py", "ratchet_baseline.py"):
            (self.repo / "scripts" / name).write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        (self.repo / "site").mkdir()
        self.page = self.repo / "site" / "page.js"
        self.page.write_text(self.CLEAN, encoding="utf-8")
        (self.repo / ".fleet.toml").write_text(
            '[request_budget_ratchet]\nenabled = true\nbrowser_paths = ["site/**"]\nserver_paths = ["src/**"]\n',
            encoding="utf-8",
        )
        self._git("init", "-q")
        self._git("add", "-A")
        self._ratchet("--init")

    def _ratchet(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._sh(sys.executable, str(self.repo / "scripts" / "request_budget_ratchet.py"), *args)

    def test_a_tree_with_no_new_hit_passes(self) -> None:
        self.assertEqual(self._ratchet().returncode, 0)

    def test_a_new_fixed_rate_poll_fails_by_name_and_reverting_passes(self) -> None:
        self.page.write_text(self.POLL, encoding="utf-8")
        proc = self._ratchet()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("fixed-rate-poll", proc.stdout + proc.stderr)
        self.page.write_text(self.CLEAN, encoding="utf-8")
        self.assertEqual(self._ratchet().returncode, 0)


if __name__ == "__main__":
    unittest.main()
