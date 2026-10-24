"""The pre-ship gate's conventions-ratchet stage and the vendored script behind it.

Pinned here: the wrapper fails exit 3 (a file git or the parser could not read)
as NOT CONFIRMED rather than skipping it; the README runs the wrapper, not the
script; the real tree is green; and a seeded violation of each enabled rule turns
the real script red, by rule name, until it is reverted. The seeding runs inside
a throwaway git repo, so nothing here touches the network, a database or config.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
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

from scripts import conventions_ratchet_gate as gate  # noqa: E402
from tests.ratchets import test_structure_ratchet_gate as sibling  # noqa: E402


class ExitMappingTests(unittest.TestCase):
    def test_exit_three_fails_as_not_confirmed_and_is_never_a_skip(self) -> None:
        ok, label = gate.classify_exit(gate.EXIT_NOT_CONFIRMED)
        self.assertFalse(ok)
        self.assertIn("NOT CONFIRMED", label)
        self.assertNotIn("skip", label.lower())

    def test_only_exit_zero_passes(self) -> None:
        self.assertTrue(gate.classify_exit(gate.EXIT_PASS)[0])
        for code in (gate.EXIT_RISEN, gate.EXIT_CONFIG, 4, -1):
            self.assertFalse(gate.classify_exit(code)[0], code)

    def test_a_script_that_cannot_finish_is_not_confirmed(self) -> None:
        for exc, phrase in ((OSError("no interpreter"), "could not start"), (subprocess.TimeoutExpired("x", 1), "did not finish")):
            with self.subTest(exc=type(exc).__name__), patch.object(subprocess, "run", side_effect=exc):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(gate.run(), 1)
                self.assertIn("FAIL: conventions ratchet", out.getvalue())
                self.assertIn(phrase, out.getvalue())


class WiringTests(unittest.TestCase):
    def test_the_readme_gate_calls_the_wrapper_not_the_script(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        commands = [line for line in readme.splitlines() if "conventions_ratchet" in line and "-m " in line]
        self.assertTrue(any("-m scripts.conventions_ratchet_gate" in line for line in commands))
        self.assertFalse(any("scripts/conventions_ratchet.py" in line for line in commands))

    def test_the_ratchet_is_declared_and_the_script_is_vendored(self) -> None:
        fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
        table = fleet["conventions_ratchet"]
        self.assertIs(table["enabled"], True)
        self.assertEqual(table["rules"], ["no-window", "logging-not-print", "no-drive-path"])
        self.assertTrue((REPO_ROOT / table["baseline"]).is_file())
        entry = fleet["vendored"]["conventions_ratchet"]
        self.assertEqual(entry["dest"], "scripts/conventions_ratchet.py")
        self.assertTrue((REPO_ROOT / entry["dest"]).is_file())

    def test_the_committed_baseline_is_current(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(gate.run(), 0)


class SeededViolationTurnsTheGateRedTests(unittest.TestCase):
    """The real vendored script, on a throwaway git repo that starts clean."""

    CLEAN = "import logging\n\nlog = logging.getLogger(__name__)\n\n\ndef work() -> None:\n    log.info('ok')\n"
    VIOLATIONS = {
        "no-window": CLEAN + "\n\ndef spawn() -> None:\n    import subprocess\n\n    subprocess.run(['echo', 'x'], check=False)\n",
        "logging-not-print": CLEAN + "\n\ndef shout() -> None:\n    print('diagnostic')\n",
        "no-drive-path": CLEAN + "\nDATA = 'E:/data/out'\n",
    }

    # the sibling stage's throwaway-repo helpers, shared rather than copied
    _sh = sibling.GrowthTurnsTheGateRedTests._sh
    _git = sibling.GrowthTurnsTheGateRedTests._git

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts" / "conventions_ratchet.py").write_bytes(gate.SCRIPT.read_bytes())
        (self.repo / "lib").mkdir()
        self.module = self.repo / "lib" / "tool.py"
        self.module.write_text(self.CLEAN, encoding="utf-8")
        (self.repo / ".fleet.toml").write_text(
            '[conventions_ratchet]\nenabled = true\nrules = ["no-window", "logging-not-print", "no-drive-path"]\n'
            'print_paths = ["lib/"]\n',
            encoding="utf-8",
        )
        self._git("init", "-q")
        self._git("add", "-A")
        self._ratchet("--init")

    def _ratchet(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._sh(sys.executable, str(self.repo / "scripts" / "conventions_ratchet.py"), *args)

    def test_a_clean_tree_passes(self) -> None:
        self.assertEqual(self._ratchet().returncode, 0)

    def test_each_enabled_rule_fails_by_name_and_reverting_passes(self) -> None:
        for rule, source in self.VIOLATIONS.items():
            with self.subTest(rule=rule):
                self.module.write_text(source, encoding="utf-8")
                proc = self._ratchet()
                self.assertEqual(proc.returncode, 1)
                self.assertIn(f"[{rule}]", proc.stdout + proc.stderr)
                self.module.write_text(self.CLEAN, encoding="utf-8")
                self.assertEqual(self._ratchet().returncode, 0)

    def test_a_file_that_does_not_parse_is_not_confirmed_not_a_pass(self) -> None:
        self.module.write_text(self.CLEAN + "def broken(:\n", encoding="utf-8")
        self.assertEqual(self._ratchet().returncode, 3)


if __name__ == "__main__":
    unittest.main()
