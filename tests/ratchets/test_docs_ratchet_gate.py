"""The pre-ship gate's docs-ratchet stage maps the vendored script's exit codes.

``scripts/docs_ratchet.py`` exits 3 when a doc could not be read (not UTF-8, an
unclosed fence or comment, git unusable): nothing was checked. These tests pin
that 3 fails as NOT CONFIRMED -- never a pass, never a skip -- that the README's
gate stage calls the wrapper that guarantees it rather than the script, and that
a doc which gains an issue reference turns the real script red by file name.
They run a throwaway git repo in a temp directory: no network
and never the real database or config.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from config.no_window import NO_WINDOW  # noqa: E402
from scripts import docs_ratchet_gate as gate  # noqa: E402


class ClassifyExitTests(unittest.TestCase):
    def test_exit_three_is_not_confirmed_and_only_zero_passes(self) -> None:
        self.assertEqual([gate.classify_exit(c)[0] for c in (0, 1, 2, 3, 4, -1)], [True] + [False] * 5)
        label = gate.classify_exit(3)[1]
        self.assertIn("NOT CONFIRMED", label)
        self.assertNotIn("skip", label.lower())


class RunTests(unittest.TestCase):
    """The shared runner is covered by test_static_ratchet_gate; this pins that the
    docs stage feeds it this stage's mapping, so a hung or unreadable doc is not a pass."""

    def test_an_unstartable_script_is_not_confirmed(self) -> None:
        buffer = io.StringIO()
        with mock.patch.object(subprocess, "run", side_effect=OSError("no interpreter")):
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(gate.run(), 1)
        self.assertIn("FAIL: docs ratchet", buffer.getvalue())
        self.assertIn("could not start", buffer.getvalue())

    def test_a_hung_script_is_not_confirmed(self) -> None:
        buffer = io.StringIO()
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(gate.run(), 1)
        self.assertIn("did not finish", buffer.getvalue())


class WiringTests(unittest.TestCase):
    def test_the_readme_gate_calls_the_wrapper_not_the_script(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        commands = [line for line in readme.splitlines() if "docs_ratchet" in line and "-m " in line]
        self.assertTrue(any("-m scripts.docs_ratchet_gate" in line for line in commands))
        self.assertFalse(any("scripts/docs_ratchet.py" in line for line in commands))

    def test_the_ratchet_is_declared_and_its_script_is_vendored(self) -> None:
        fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
        self.assertIs(fleet["docs_ratchet"]["enabled"], True)
        self.assertTrue((REPO_ROOT / fleet["docs_ratchet"]["baseline"]).is_file())
        entry = fleet["vendored"]["docs_ratchet"]
        self.assertEqual(entry["dest"], "scripts/docs_ratchet.py")
        self.assertTrue((REPO_ROOT / entry["dest"]).is_file())

    def test_the_committed_baseline_is_current(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.run(), 0)


class AddedReferenceTurnsTheGateRedTests(unittest.TestCase):
    """The real vendored script, on a throwaway git repo shaped like this one."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / "scripts").mkdir()
        shutil.copy(gate.SCRIPT, self.repo / "scripts" / "docs_ratchet.py")
        (self.repo / "docs").mkdir()
        (self.repo / "README.md").write_text("# Demo\n\nStates what is true.\n", encoding="utf-8")
        (self.repo / "docs" / "guide.md").write_text("# Guide\n\nStates why.\n", encoding="utf-8")
        (self.repo / ".fleet.toml").write_text("[docs_ratchet]\nenabled = true\n", encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "-A")
        self._ratchet("--init")

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=self.repo,
            check=True, capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
        )

    def _ratchet(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(self.repo / "scripts" / "docs_ratchet.py"), *args], cwd=self.repo,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, check=False,
        )

    def test_a_clean_tree_passes(self) -> None:
        self.assertEqual(self._ratchet().returncode, 0)

    def test_an_added_issue_reference_fails_by_file_name(self) -> None:
        (self.repo / "docs" / "guide.md").write_text("# Guide\n\nAdded in #123.\n", encoding="utf-8")
        proc = self._ratchet()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("docs/guide.md", proc.stdout + proc.stderr)

    def test_an_unclosed_fence_is_not_confirmed_not_a_pass(self) -> None:
        (self.repo / "docs" / "guide.md").write_text("# Guide\n\n```\nnever closed #123\n", encoding="utf-8")
        proc = self._ratchet()
        self.assertEqual(proc.returncode, 3)
        self.assertIn("docs/guide.md", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
