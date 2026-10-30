"""The pre-ship gate's structure-ratchet stage maps the vendored script's exit codes.

``scripts/structure_ratchet.py`` exits 3 when something could not be measured
(git unusable, an unparsable file, an unknown ``shape``): nothing was checked.
These tests pin that 3 fails as NOT CONFIRMED -- never a pass, never a skip --
that the README's gate stage calls the wrapper that guarantees it rather than
the script, that the real tree passes, and that a line added to a baselined file
turns the real script red until it is reverted. They run a throwaway git repo in
a temp directory: no network and never the real database or config.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import io
import shutil
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

from config.no_window import NO_WINDOW  # noqa: E402
from scripts import structure_ratchet_gate as gate  # noqa: E402


class ExitMappingTests(unittest.TestCase):
    def test_only_exit_zero_passes_and_exit_three_is_never_a_skip(self) -> None:
        verdicts = {code: gate.classify_exit(code) for code in (0, 1, 2, 3, 4, -1)}
        self.assertEqual([ok for ok, _ in verdicts.values()], [True, False, False, False, False, False])
        self.assertIn("NOT CONFIRMED", verdicts[3][1])
        self.assertNotIn("skip", verdicts[3][1].lower())

    def test_a_script_that_cannot_finish_is_not_confirmed(self) -> None:
        # the shared runner is covered by test_static_ratchet_gate; this stage must hand it its own mapping
        for exc, phrase in ((OSError("no interpreter"), "could not start"), (subprocess.TimeoutExpired("x", 1), "did not finish")):
            with self.subTest(exc=type(exc).__name__), patch.object(subprocess, "run", side_effect=exc):
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    self.assertEqual(gate.run(), 1)
                self.assertIn("FAIL: structure ratchet", buffer.getvalue())
                self.assertIn(phrase, buffer.getvalue())


class WiringTests(unittest.TestCase):
    def test_the_readme_gate_calls_the_wrapper_not_the_script(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        commands = [line for line in readme.splitlines() if "structure_ratchet" in line and "-m " in line]
        self.assertTrue(any("-m scripts.structure_ratchet_gate" in line for line in commands))
        self.assertFalse(any("scripts/structure_ratchet.py" in line for line in commands))

    def test_the_ratchet_and_the_shape_are_declared_and_the_script_is_vendored(self) -> None:
        fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
        self.assertIs(fleet["structure_ratchet"]["enabled"], True)
        self.assertTrue((REPO_ROOT / fleet["structure_ratchet"]["baseline"]).is_file())
        self.assertEqual(fleet["shape"], "pipeline-cli")
        entry = fleet["vendored"]["structure_ratchet"]
        self.assertEqual(entry["dest"], "scripts/structure_ratchet.py")
        self.assertTrue((REPO_ROOT / entry["dest"]).is_file())

    def test_every_declared_root_is_documented_in_the_layout_doc(self) -> None:
        fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
        layout = (REPO_ROOT / "docs" / "layout.md").read_text(encoding="utf-8")
        for root in fleet["extra_roots"]:
            self.assertIn(f"`{root}/`", layout, root)

    def test_the_committed_baseline_is_current(self) -> None:
        with redirect_stdout(io.StringIO()):
            self.assertEqual(gate.run(), 0)


class GrowthTurnsTheGateRedTests(unittest.TestCase):
    """The real vendored script, on a throwaway git repo with one baselined giant."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        (self.repo / "scripts").mkdir()
        shutil.copy(gate.SCRIPT, self.repo / "scripts" / "structure_ratchet.py")
        shutil.copy(gate.SCRIPT.with_name("structure_layout.py"), self.repo / "scripts" / "structure_layout.py")
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_x.py").write_text("def test_x() -> None:\n    pass\n", encoding="utf-8")
        self.giant = self.repo / "tool.py"
        self.original = "".join(f"x{i} = {i}\n" for i in range(1001))
        self.giant.write_text(self.original, encoding="utf-8")
        self._write_fleet('shape = "pipeline-cli"\n')
        self._git("init", "-q")
        self._git("add", "-A")
        self._ratchet("--init")

    def _write_fleet(self, head: str) -> None:
        (self.repo / ".fleet.toml").write_text(head + "[structure_ratchet]\nenabled = true\n", encoding="utf-8")

    def _sh(self, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(argv), cwd=self.repo, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, check=False,
        )

    def _git(self, *args: str) -> None:
        done = self._sh("git", "-c", "user.name=t", "-c", "user.email=t@t", *args)
        self.assertEqual(done.returncode, 0, done.stderr)

    def _ratchet(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._sh(sys.executable, str(self.repo / "scripts" / "structure_ratchet.py"), *args)

    def test_a_baselined_file_passes_at_its_size(self) -> None:
        self.assertEqual(self._ratchet().returncode, 0)

    def test_a_line_added_to_a_baselined_file_fails_and_reverting_passes(self) -> None:
        self.giant.write_text(self.original + "y = 1\n", encoding="utf-8")
        proc = self._ratchet()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("tool.py", proc.stdout + proc.stderr)
        self.giant.write_text(self.original, encoding="utf-8")
        self.assertEqual(self._ratchet().returncode, 0)

    def test_an_unknown_shape_is_not_confirmed_not_a_pass(self) -> None:
        self._write_fleet('shape = "no-such-shape"\n')
        self.assertEqual(self._ratchet().returncode, 3)


if __name__ == "__main__":
    unittest.main()
