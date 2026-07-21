"""Regression test for the fleet-wide CREATE_NO_WINDOW convention (issue #166).

Any ``subprocess.Popen``/``.run``/``.call``/``.check_output``/``.check_call`` (or
``asyncio.create_subprocess_exec``) call that launches an external executable must
pass a ``creationflags=`` keyword on Windows, or a console-less parent (Streamlit
run under the app-launcher, a scheduled task, a detached wrapper) flashes a new
console window on screen for every spawn. See ``fleet-config``'s global
``CLAUDE.md`` ("Subprocess spawns must suppress the console window (Windows)").

This statically scans every tracked ``.py`` file (via ``git ls-files``, mirroring
``design_lint``'s convention) for the relevant call shapes and asserts each one
carries a ``creationflags`` keyword argument — catching a future call site that
forgets the flag, not just re-checking today's fixed sites.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import ast
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_SUBPROCESS_SPAWN_ATTRS = {"Popen", "run", "call", "check_output", "check_call"}
_ASYNCIO_SPAWN_ATTRS = {"create_subprocess_exec", "create_subprocess_shell"}


def _tracked_python_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
    )
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line.strip()]


def _spawn_calls_missing_creationflags(path: Path) -> list[str]:
    """Return "line:call" strings for spawn calls in ``path`` with no creationflags kwarg."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []

    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        base = node.func.value
        base_name = base.id if isinstance(base, ast.Name) else None

        is_subprocess_spawn = base_name == "subprocess" and attr in _SUBPROCESS_SPAWN_ATTRS
        is_asyncio_spawn = base_name == "asyncio" and attr in _ASYNCIO_SPAWN_ATTRS
        if not (is_subprocess_spawn or is_asyncio_spawn):
            continue

        has_creationflags = any(kw.arg == "creationflags" for kw in node.keywords)
        # A bare **kwargs forward could legitimately carry it — don't flag those.
        has_kwargs_forward = any(kw.arg is None for kw in node.keywords)
        if not has_creationflags and not has_kwargs_forward:
            missing.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {base_name}.{attr}(...)")
    return missing


class NoWindowConventionTests(unittest.TestCase):
    def test_every_subprocess_spawn_has_creationflags(self):
        offenders: list[str] = []
        for path in _tracked_python_files():
            offenders.extend(_spawn_calls_missing_creationflags(path))
        self.assertEqual(
            offenders, [],
            "subprocess/asyncio spawn call(s) missing creationflags=... "
            "(CREATE_NO_WINDOW convention, fleet-config#399):\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
