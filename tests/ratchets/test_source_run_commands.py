"""Every run command written into a source file under ``tools/`` names a target that exists.

When the tool packages moved under ``tools/``, the usage lines left in docstrings,
comments and SQL kept naming the old module and script paths, so a copy-pasted
command failed. The doc-references ratchet reads only Markdown (README, CLAUDE.md,
docs/), so this covers ``.py`` and ``.sql`` files: ``python -m <dotted.module>``,
``streamlit run <path>`` and ``python <path>.py``.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.ratchets.test_source_run_commands -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_SUFFIXES = (".py", ".sql")
# `python -m` resolves a module from the repo root, or from `src/` when a command puts it on PYTHONPATH.
MODULE_ROOTS = (REPO_ROOT, REPO_ROOT / "src")

# A dotted module is checked against the repo; a bare `-m pip` / `-m streamlit` has no dot.
_MODULE_CMD = re.compile(r"python(?:\.exe)?\s+-m\s+([A-Za-z_]\w*(?:\.\w+)+)")
_STREAMLIT_CMD = re.compile(r"streamlit\s+run\s+([\w./\\-]+\.py)")
_SCRIPT_CMD = re.compile(r"python(?:\.exe)?\s+((?:[\w.-]+[/\\])*[\w.-]+\.py)\b")


def _sources() -> list[Path]:
    return [p for suffix in SCANNED_SUFFIXES for p in (REPO_ROOT / "tools").rglob(f"*{suffix}")]


def _commands() -> list[tuple[str, str, str]]:
    """``(source file, kind, target)`` for every run command found."""
    found = []
    for path in _sources():
        rel = path.relative_to(REPO_ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        found.extend((rel, "module", m.group(1)) for m in _MODULE_CMD.finditer(text))
        found.extend((rel, "path", m.group(1).replace("\\", "/")) for m in _STREAMLIT_CMD.finditer(text))
        found.extend((rel, "path", m.group(1).replace("\\", "/")) for m in _SCRIPT_CMD.finditer(text))
    return found


def _resolves(kind: str, target: str) -> bool:
    if kind == "path":
        return (REPO_ROOT / target).is_file()
    for root in MODULE_ROOTS:
        base = root.joinpath(*target.split("."))
        if base.with_suffix(".py").is_file() or (base / "__init__.py").is_file():
            return True
    return False


class SourceRunCommandsTests(unittest.TestCase):
    def test_commands_were_found(self) -> None:
        self.assertTrue(_commands(), "the scan matched no run command")

    def test_every_run_command_target_exists(self) -> None:
        stale = [f"{rel}: {target}" for rel, kind, target in _commands() if not _resolves(kind, target)]
        self.assertEqual([], stale, "run commands in source files name targets that do not exist")


if __name__ == "__main__":
    unittest.main()
