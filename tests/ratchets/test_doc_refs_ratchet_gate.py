"""Tests for the doc-refs-ratchet gate stage and the vendored script behind it.

The script exits 3 when a doc or git could not be read (a non-UTF-8 file, a fence
or HTML comment left open), meaning nothing was checked. Here: 3 fails as NOT
CONFIRMED and is never a skip; the README runs the wrapper, not the script; the
real tree passes; and a README naming a missing path turns the real script red
until the line is removed. The red/green cases use a temporary git repo, so the
suite needs no network, database or real config.

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

from scripts import doc_refs_ratchet_gate as gate  # noqa: E402
from tests.ratchets import test_structure_ratchet_gate as sibling  # noqa: E402


class ExitMappingTests(unittest.TestCase):
    def test_exit_three_fails_as_not_confirmed_and_is_never_a_skip(self) -> None:
        ok, label = gate.classify_exit(gate.EXIT_NOT_CONFIRMED)
        self.assertFalse(ok)
        self.assertIn("NOT CONFIRMED", label)
        self.assertNotIn("skip", label.lower())

    def test_only_exit_zero_passes(self) -> None:
        self.assertTrue(gate.classify_exit(gate.EXIT_PASS)[0])
        for code in (gate.EXIT_DANGLING, gate.EXIT_CONFIG, 4, -1):
            self.assertFalse(gate.classify_exit(code)[0], code)

    def test_a_script_that_cannot_finish_is_not_confirmed(self) -> None:
        for exc, phrase in ((OSError("no interpreter"), "could not start"), (subprocess.TimeoutExpired("x", 1), "did not finish")):
            with self.subTest(exc=type(exc).__name__), patch.object(subprocess, "run", side_effect=exc):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(gate.run(), 1)
                self.assertIn("FAIL: doc refs ratchet", out.getvalue())
                self.assertIn(phrase, out.getvalue())


class WiringTests(unittest.TestCase):
    def test_the_readme_gate_calls_the_wrapper_not_the_script(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        commands = [line for line in readme.splitlines() if "doc_refs_ratchet" in line and "-m " in line]
        self.assertTrue(any("-m scripts.doc_refs_ratchet_gate" in line for line in commands))
        self.assertFalse(any("scripts/doc_refs_ratchet.py" in line for line in commands))

    def test_the_ratchet_is_declared_and_the_script_is_vendored(self) -> None:
        fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
        self.assertIs(fleet["doc_refs_ratchet"]["enabled"], True)
        self.assertTrue((REPO_ROOT / fleet["doc_refs_ratchet"]["baseline"]).is_file())
        entry = fleet["vendored"]["doc_refs_ratchet"]
        self.assertEqual(entry["dest"], "scripts/doc_refs_ratchet.py")
        self.assertTrue((REPO_ROOT / entry["dest"]).is_file())

    def test_the_committed_baseline_is_current(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(gate.run(), 0)


class MissingPathTurnsTheGateRedTests(unittest.TestCase):
    """The real vendored script, on a throwaway git repo whose README names a real file."""

    CLEAN = "# Tool\n\nRun `scripts/doc_refs_ratchet.py` to check.\n"
    DANGLING = CLEAN + "\nSee `scripts/no_such_tool.py` too.\n"

    # the sibling stage's throwaway-repo helpers, shared rather than copied
    _sh = sibling.GrowthTurnsTheGateRedTests._sh
    _git = sibling.GrowthTurnsTheGateRedTests._git

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts" / "doc_refs_ratchet.py").write_bytes(gate.SCRIPT.read_bytes())
        self.readme = self.repo / "README.md"
        self.readme.write_text(self.CLEAN, encoding="utf-8")
        (self.repo / ".fleet.toml").write_text("[doc_refs_ratchet]\nenabled = true\n", encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "-A")
        self._ratchet("--init")

    def _ratchet(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._sh(sys.executable, str(self.repo / "scripts" / "doc_refs_ratchet.py"), *args)

    def test_a_doc_naming_existing_paths_passes(self) -> None:
        self.assertEqual(self._ratchet().returncode, 0)

    def test_a_missing_path_fails_by_name_and_removing_it_passes(self) -> None:
        self.readme.write_text(self.DANGLING, encoding="utf-8")
        proc = self._ratchet()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("scripts/no_such_tool.py", proc.stdout + proc.stderr)
        self.readme.write_text(self.CLEAN, encoding="utf-8")
        self.assertEqual(self._ratchet().returncode, 0)

    def test_an_unclosed_fence_is_not_confirmed_not_a_pass(self) -> None:
        self.readme.write_text(self.CLEAN + "```\nnever closed\n", encoding="utf-8")
        self.assertEqual(self._ratchet().returncode, 3)


if __name__ == "__main__":
    unittest.main()
