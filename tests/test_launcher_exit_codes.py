"""Regression test for launcher exit-code propagation (issue #174).

Every ``launch_*.bat`` wrapper is invoked by the scheduler, which derives a
run's success/failure purely from the process exit code. A wrapper that ends
without propagating its Python child's code returns the exit status of its
last ``echo``/``pause`` — always 0 — so a hard crash is recorded as a green
run. That is exactly how two days of reporting-pipeline crashes were logged as
successes while ``reporting_pipeline.py`` was faithfully calling ``sys.exit(1)``.

Three shapes are checked, statically, across every tracked ``launch_*.bat``:

1. **Exit-code propagation.** The script must end with an ``exit /b %RC%``.
2. **The ``endlocal`` trap.** ``endlocal`` restores the pre-``setlocal``
   environment, discarding ``RC``. Split across two lines, ``exit /b %RC%``
   degrades to a bare ``exit /b`` and the run reports 0. Verified empirically:

       endlocal            + exit /b %RC%  (two lines) -> child rc 7 became 0
       endlocal & exit /b %RC%             (one line)  -> child rc 7 stayed 7

   So a script using ``setlocal`` must join them on one parsed line.
3. **No system-Python fallback.** A missing venv must be a hard stop. Falling
   back to a bare ``python`` on PATH runs the pipeline against an interpreter
   without the project's pinned dependencies — it cannot succeed, and if it
   ever did it would write to the real datastore with unpinned versions.
4. **CRLF line endings.** ``cmd`` tracks byte offsets as it interprets a batch
   file; with LF-only endings it resumes at the wrong offset after a ``goto``
   and executes fragments of lines. Observed while validating this very fix —
   an LF-saved launcher emitted ``'M' is not recognized``, ``'/d' is not
   recognized``, read ``%VENV_DIR%`` as empty, and exited 0 on a broken venv.
   The failure is silent and looks like unrelated corruption.

This scans the tracked files rather than re-checking today's four, so a
launcher added later that forgets the convention is caught too.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# `exit /b %RC%` / `exit /b !RC!`, optionally preceded by `endlocal &` on the
# same line. Trailing `exit /b 1` guard clauses elsewhere in the file don't
# count — this must be the script's final propagation.
_PROPAGATES_RE = re.compile(r"^\s*(endlocal\s*&\s*)?exit\s*/b\s*[%!]\w+[%!]\s*$", re.IGNORECASE)
_ENDLOCAL_JOINED_RE = re.compile(r"^\s*endlocal\s*&\s*exit\s*/b\s*[%!]\w+[%!]", re.IGNORECASE)
_BARE_ENDLOCAL_RE = re.compile(r"^\s*endlocal\s*$", re.IGNORECASE)
_SETLOCAL_RE = re.compile(r"^\s*setlocal\b", re.IGNORECASE)
# A bare `python foo.py` invocation — i.e. not `"%VENV_DIR%\Scripts\python.exe"`.
# Anchored at line start so mentions inside REM/echo help text don't trip it.
_SYSTEM_PYTHON_RE = re.compile(r"^\s*(python|py)(\.exe)?\s+\S+\.py\b", re.IGNORECASE)


def _tracked_launcher_scripts() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "launch_*.bat"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        creationflags=_NO_WINDOW,
    )
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line.strip()]


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """Numbered lines with REM comments and blank lines stripped."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines: list[tuple[int, str]] = []
    for n, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or re.match(r"^rem\b", stripped, re.IGNORECASE) or stripped.startswith("::"):
            continue
        lines.append((n, raw))
    return lines


class LauncherExitCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scripts = _tracked_launcher_scripts()
        self.assertTrue(self.scripts, "no tracked launch_*.bat files found")

    def test_every_launcher_propagates_its_exit_code(self):
        offenders: list[str] = []
        for path in self.scripts:
            lines = _code_lines(path)
            if not any(_PROPAGATES_RE.match(text) for _, text in lines):
                offenders.append(
                    f"{path.name}: no final 'exit /b %RC%' — the scheduler will "
                    f"record every run, including crashes, as success"
                )
        self.assertEqual(
            offenders, [],
            "launcher(s) discarding their child's exit code (issue #174):\n  "
            + "\n  ".join(offenders),
        )

    def test_setlocal_launchers_join_endlocal_and_exit(self):
        offenders: list[str] = []
        for path in self.scripts:
            lines = _code_lines(path)
            if not any(_SETLOCAL_RE.match(text) for _, text in lines):
                continue
            if any(_ENDLOCAL_JOINED_RE.match(text) for _, text in lines):
                continue
            bare = [n for n, text in lines if _BARE_ENDLOCAL_RE.match(text)]
            if bare:
                offenders.append(
                    f"{path.name}:{bare[-1]} bare 'endlocal' with setlocal in effect — "
                    f"RC is discarded before 'exit /b %RC%' reads it, so a failed "
                    f"run exits 0. Use 'endlocal & exit /b %RC%' on one line."
                )
        self.assertEqual(
            offenders, [],
            "launcher(s) losing RC across endlocal (issue #174):\n  " + "\n  ".join(offenders),
        )

    def test_every_launcher_uses_crlf_line_endings(self):
        offenders: list[str] = []
        for path in self.scripts:
            data = path.read_bytes()
            bare_lf = sum(
                1 for i, byte in enumerate(data)
                if byte == 0x0A and (i == 0 or data[i - 1] != 0x0D)
            )
            if bare_lf:
                offenders.append(
                    f"{path.name}: {bare_lf} bare-LF line ending(s) — cmd will "
                    f"mis-execute this file after a goto and can exit 0 on failure"
                )
        self.assertEqual(
            offenders, [],
            "launcher(s) with LF line endings (issue #174):\n  " + "\n  ".join(offenders),
        )

    def test_no_launcher_falls_back_to_system_python(self):
        offenders: list[str] = []
        for path in self.scripts:
            for n, text in _code_lines(path):
                if _SYSTEM_PYTHON_RE.match(text):
                    offenders.append(f"{path.name}:{n} {text.strip()}")
        self.assertEqual(
            offenders, [],
            "launcher(s) invoking a system Python instead of the project venv "
            "(issue #174) — a missing venv must be a hard stop, not a silent "
            "fallback to an interpreter without the pinned dependencies:\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
