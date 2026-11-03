"""The pre-ship gate's usage-track-ratchet stage and the vendored script behind it.

Pinned here: the wrapper fails exit 3 (a file git or the scanner could not read) as
NOT CONFIRMED rather than skipping it; the README runs the wrapper, not the script;
the real tree is green with the usage log wired; and a seeded untracked control,
sheet or built id, and a tree with no mount, each turn the real script red by rule
name until reverted. The seeding runs inside a throwaway git repo.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.ratchets.test_usage_track_ratchet_gate -v
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

from scripts import usage_track_ratchet_gate as gate  # noqa: E402
from tests.ratchets import test_structure_ratchet_gate as sibling  # noqa: E402


class ExitMappingTests(unittest.TestCase):
    def test_only_exit_zero_passes_and_exit_three_is_not_confirmed_never_a_skip(self) -> None:
        verdicts = {code: gate.classify_exit(code) for code in (0, 1, 2, 3, 4, -1)}
        self.assertEqual([code for code, (ok, _) in verdicts.items() if ok], [gate.EXIT_PASS])
        self.assertIn("NOT CONFIRMED", verdicts[gate.EXIT_NOT_CONFIRMED][1])
        self.assertTrue(all("skip" not in label.lower() for _, label in verdicts.values()))

    def test_a_script_that_cannot_finish_is_not_confirmed(self) -> None:
        cases = {"could not start": OSError("no interpreter"), "did not finish": subprocess.TimeoutExpired("x", 1)}
        for phrase, exc in cases.items():
            with self.subTest(phrase=phrase), patch.object(subprocess, "run", side_effect=exc):
                out = io.StringIO()
                with redirect_stdout(out):
                    code = gate.run()
                self.assertEqual(code, 1)
                self.assertIn("FAIL: usage track ratchet", out.getvalue())
                self.assertIn(phrase, out.getvalue())


class WiringTests(unittest.TestCase):
    def test_the_readme_gate_calls_the_wrapper_not_the_script(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        commands = [line for line in readme.splitlines() if "usage_track_ratchet" in line and "-m " in line]
        self.assertTrue(any("-m scripts.usage_track_ratchet_gate" in line for line in commands))
        self.assertFalse(any("scripts/usage_track_ratchet.py" in line for line in commands))

    def test_the_ratchet_is_declared_with_all_four_rules_and_the_pieces_are_vendored(self) -> None:
        fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
        table = fleet["usage_track_ratchet"]
        self.assertIs(table["enabled"], True)
        self.assertEqual(table["rules"], ["untracked-control", "untracked-sheet", "dynamic-id", "usage-log-wired"])
        self.assertTrue((REPO_ROOT / table["baseline"]).is_file())
        for key in ("usage-log", "usage_log", "usage_track_ratchet", "usage_track_scan"):
            with self.subTest(key=key):
                self.assertTrue((REPO_ROOT / fleet["vendored"][key]["dest"]).exists())

    def test_the_committed_baseline_is_current_and_the_log_is_wired(self) -> None:
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(gate.run(), 0)
        self.assertIn("usage-log-wired", out.getvalue())


class SeededViolationTurnsTheGateRedTests(unittest.TestCase):
    """The real vendored script, on a throwaway git repo that starts clean and wired."""

    PAGE = (
        '<!doctype html><button data-track="go">Go</button>\n'
        '<script type="module">import { initUsageLog } from "./usage-log.js"; initUsageLog();</script>\n'
    )
    VIOLATIONS = {
        "untracked-control": PAGE + "<button>Stop</button>\n",
        "untracked-sheet": PAGE + '<dialog id="d"></dialog>\n',
        "dynamic-id": PAGE + '<script>box.dataset.track = "id-" + n;</script>\n',
    }

    # the sibling stage's throwaway-repo helpers, shared rather than copied
    _sh = sibling.GrowthTurnsTheGateRedTests._sh
    _git = sibling.GrowthTurnsTheGateRedTests._git

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / "scripts").mkdir()
        for name in ("usage_track_ratchet.py", "usage_track_scan.py"):
            (self.repo / "scripts" / name).write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        (self.repo / "site").mkdir()
        self.page = self.repo / "site" / "index.html"
        self.page.write_text(self.PAGE, encoding="utf-8")
        self.mount = self.repo / "mount.py"
        self.mount.write_text("app.include_router(usage_router(db))\n", encoding="utf-8")
        (self.repo / ".fleet.toml").write_text(
            '[vendored]\nusage-log = { src = "x", dest = "site/_vendored/usage-log" }\n'
            'usage_log = { src = "x", dest = "src/usage_log.py" }\n'
            'usage_track_ratchet = { src = "x", dest = "scripts/usage_track_ratchet.py" }\n'
            'usage_track_scan = { src = "x", dest = "scripts/usage_track_scan.py" }\n'
            '[usage_track_ratchet]\nenabled = true\npaths = ["site/**"]\n'
            'rules = ["untracked-control", "untracked-sheet", "dynamic-id", "usage-log-wired"]\n',
            encoding="utf-8",
        )
        self._git("init", "-q")
        self._git("add", "-A")
        self._ratchet("--init")

    def _ratchet(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._sh(sys.executable, str(self.repo / "scripts" / "usage_track_ratchet.py"), *args)

    def test_a_clean_wired_tree_passes(self) -> None:
        self.assertEqual(self._ratchet().returncode, 0)

    def test_each_markup_rule_fails_by_name_and_reverting_passes(self) -> None:
        for rule, source in self.VIOLATIONS.items():
            with self.subTest(rule=rule):
                self.page.write_text(source, encoding="utf-8")
                proc = self._ratchet()
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f"[{rule}]", proc.stdout + proc.stderr)
                self.page.write_text(self.PAGE, encoding="utf-8")
                self.assertEqual(self._ratchet().returncode, 0)

    def test_an_unmounted_log_fails_as_not_wired_and_mounting_it_passes(self) -> None:
        self.mount.write_text("pass\n", encoding="utf-8")
        proc = self._ratchet()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("usage-log-wired", proc.stdout + proc.stderr)
        self.mount.write_text("app.include_router(usage_router(db))\n", encoding="utf-8")
        self.assertEqual(self._ratchet().returncode, 0)


if __name__ == "__main__":
    unittest.main()
