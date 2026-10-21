"""The pre-ship gate's static-ratchet stage maps the vendored script's exit codes.

``scripts/static_ratchet.py`` exits 3 when a detector could not run (no ``npx``,
no ruff, a timeout): nothing was checked. These tests pin that 3 fails as NOT
CONFIRMED -- never a pass, never a skip -- and that the README's gate stage
calls the wrapper that guarantees it rather than the script. They run fake
scripts in a temp directory: no network, no ``npx``, and never the real
database or config.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import static_ratchet_gate as gate  # noqa: E402


def _fake_script(directory: Path, code: int, message: str = "") -> Path:
    script = directory / "fake_ratchet.py"
    script.write_text(f"import sys\nprint({message!r})\nsys.exit({code})\n", encoding="utf-8")
    return script


def _run(script: Path) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = gate.run(script)
    return code, buffer.getvalue()


class ClassifyExitTests(unittest.TestCase):
    def test_only_exit_zero_passes(self) -> None:
        self.assertTrue(gate.classify_exit(0)[0])
        for code in (1, 2, 3, 4, -1):
            self.assertFalse(gate.classify_exit(code)[0], code)

    def test_exit_three_is_not_confirmed_and_never_a_skip(self) -> None:
        ok, label = gate.classify_exit(3)
        self.assertFalse(ok)
        self.assertIn("NOT CONFIRMED", label)
        self.assertNotIn("skip", label.lower())


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_run_exits_nonzero_for_every_script_failure(self) -> None:
        for code, expected in [(0, 0), (1, 1), (2, 1), (3, 1)]:
            with self.subTest(code=code):
                got, out = _run(_fake_script(self.tmp, code, "from the script"))
                self.assertEqual(got, expected)
                self.assertIn("from the script", out)  # the script's own finding is echoed
                self.assertTrue(out.rstrip().splitlines()[-1].startswith("OK" if code == 0 else "FAIL"))

    def test_run_reports_a_detector_that_could_not_run(self) -> None:
        got, out = _run(_fake_script(self.tmp, 3, "npx was not found"))
        self.assertEqual(got, 1)
        self.assertIn("npx was not found", out)
        self.assertIn("NOT CONFIRMED", out)

    def test_a_timeout_and_an_unstartable_script_are_not_confirmed(self) -> None:
        script = _fake_script(self.tmp, 0)
        for exc in (subprocess.TimeoutExpired(cmd="x", timeout=1), OSError("no interpreter")):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(gate.subprocess, "run", side_effect=exc):
                    got, out = _run(script)
                self.assertEqual(got, 1)
                self.assertIn("NOT CONFIRMED", out)

    def test_the_wrapper_runs_under_the_venv_interpreter(self) -> None:
        real = subprocess.run
        with mock.patch.object(gate.subprocess, "run", side_effect=real) as spy:
            _run(_fake_script(self.tmp, 0))
        # the python that has ruff, not whatever PATH resolves
        self.assertEqual(spy.call_args.args[0][0], sys.executable)


class WiringTests(unittest.TestCase):
    def test_the_readme_gate_calls_the_wrapper_not_the_script(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        commands = [line for line in readme.splitlines() if "static_ratchet" in line and "-m " in line]
        self.assertTrue(any("-m scripts.static_ratchet_gate" in line for line in commands))
        self.assertFalse(any("scripts/static_ratchet.py" in line for line in commands))

    def test_the_ratchet_is_declared_and_its_script_is_vendored(self) -> None:
        fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
        self.assertIs(fleet["static_ratchet"]["enabled"], True)
        self.assertTrue((REPO_ROOT / fleet["static_ratchet"]["baseline"]).is_file())
        entry = fleet["vendored"]["static_ratchet"]
        self.assertEqual(entry["dest"], "scripts/static_ratchet.py")
        self.assertTrue((REPO_ROOT / entry["dest"]).is_file())


if __name__ == "__main__":
    unittest.main()
