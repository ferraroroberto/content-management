"""Every documented ``unittest tests.<module>`` command names a test module that exists.

A test file moved into a domain subfolder keeps working under discovery, but the
``Run:`` line in its docstring (and a skill's "done" check) goes on naming the old
path and fails with ModuleNotFoundError. The doc-references ratchet reads only
README, CLAUDE.md and docs/, so this covers the test docstrings and the skills.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.ratchets.test_documented_test_commands -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Matches the dotted module list that follows `-m unittest`.
_COMMAND = re.compile(r"-m unittest((?:\s+tests(?:\.\w+)+)+)")
_MODULE = re.compile(r"tests(?:\.\w+)+")


def _documented_modules() -> list[tuple[Path, str]]:
    sources = [*(REPO_ROOT / "tests").rglob("*.py"), *(REPO_ROOT / ".claude" / "skills").rglob("SKILL.md")]
    found = []
    for path in sources:
        for command in _COMMAND.finditer(path.read_text(encoding="utf-8")):
            found.extend((path, module) for module in _MODULE.findall(command.group(1)))
    return found


def _resolves(module: str) -> bool:
    target = REPO_ROOT.joinpath(*module.split("."))
    return target.with_suffix(".py").is_file() or (target / "__init__.py").is_file()


class DocumentedTestCommandsTests(unittest.TestCase):
    def test_commands_were_found(self) -> None:
        self.assertTrue(_documented_modules(), "the scan matched no documented command")

    def test_every_documented_module_exists(self) -> None:
        stale = [
            f"{path.relative_to(REPO_ROOT).as_posix()}: {module}"
            for path, module in _documented_modules()
            if not _resolves(module)
        ]
        self.assertEqual([], stale, "documented test commands name modules that do not exist")


if __name__ == "__main__":
    unittest.main()
