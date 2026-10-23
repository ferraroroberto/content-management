"""No function or method is defined and then never mentioned anywhere else.

An uncalled helper reads as live API to the next maintainer, and the codebase
audit keeps re-finding them. A name that appears exactly once across every
tracked text file (docs, ``.bat`` and config included) is only its own ``def``,
so nothing can call it by name, import it, or list it in a config. The check
skips what a whole-word search cannot judge: dunders, ``test*`` methods, any
decorator beyond property/staticmethod/classmethod (registration), and methods
of a class with a base (they override a hook the base class calls).

When it fails, delete the function -- or, if something reaches it dynamically,
make that reference visible by name.

The scan reads tracked files only: no network, no database, no config.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config.no_window import NO_WINDOW  # noqa: E402

_PLAIN_DECORATORS = {"property", "staticmethod", "classmethod"}
_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".mp4", ".mp3", ".wav", ".pdf",
    ".woff", ".woff2", ".ttf", ".otf",
}


def _is_candidate(node: ast.AST, parent_has_base: bool) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    if parent_has_base or node.name.startswith("test"):
        return False
    if node.name.startswith("__") and node.name.endswith("__"):
        return False
    return all(isinstance(d, ast.Name) and d.id in _PLAIN_DECORATORS for d in node.decorator_list)


def _candidates(tree: ast.AST) -> list[ast.AST]:
    found: list[ast.AST] = []
    stack: list[tuple[ast.AST, bool]] = [(tree, False)]
    while stack:
        node, in_based_class = stack.pop()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                stack.append((child, bool(child.bases)))
                continue
            if _is_candidate(child, in_based_class):
                found.append(child)
            stack.append((child, in_based_class))
    return found


def find_uncalled(sources: dict[str, str]) -> list[str]:
    """`path:line name` for every function whose name occurs once in `sources`."""
    words: Counter[str] = Counter()
    for text in sources.values():
        words.update(re.findall(r"\w+", text))
    found: list[str] = []
    for path, text in sources.items():
        if not path.endswith(".py"):
            continue
        for node in _candidates(ast.parse(text)):
            if words[node.name] <= 1:
                found.append(f"{path}:{node.lineno} {node.name}")
    return sorted(found)


def _tracked_sources() -> dict[str, str]:
    listing = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        check=True, creationflags=NO_WINDOW,
    ).stdout.splitlines()
    sources: dict[str, str] = {}
    for name in listing:
        if Path(name).suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            sources[name] = (REPO_ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
    return sources


class FindUncalledTests(unittest.TestCase):
    def test_flags_a_function_nothing_mentions(self) -> None:
        sources = {"a.py": "def used():\n    pass\n\ndef orphan():\n    pass\n", "b.py": "used()\n"}
        self.assertEqual(find_uncalled(sources), ["a.py:4 orphan"])

    def test_a_mention_in_a_non_python_file_counts(self) -> None:
        sources = {"a.py": "def job():\n    pass\n", "run.bat": "python -c job\n"}
        self.assertEqual(find_uncalled(sources), [])

    def test_flags_an_uncalled_method_and_property(self) -> None:
        sources = {"a.py": (
            "class Box:\n"
            "    @property\n"
            "    def wide(self):\n"
            "        return 1\n"
            "    def open(self):\n"
            "        return 2\n"
        )}
        self.assertEqual(find_uncalled(sources), ["a.py:3 wide", "a.py:5 open"])

    def test_skips_overrides_dunders_tests_and_registered_functions(self) -> None:
        sources = {"a.py": (
            "class Handler(Base):\n"
            "    def do_GET(self):\n"
            "        pass\n"
            "class Plain:\n"
            "    def __repr__(self):\n"
            "        return ''\n"
            "    def test_thing(self):\n"
            "        pass\n"
            "@app.route('/x')\n"
            "def view():\n"
            "    pass\n"
        )}
        self.assertEqual(find_uncalled(sources), [])


class RepoTests(unittest.TestCase):
    def test_no_tracked_function_is_uncalled(self) -> None:
        self.assertEqual(find_uncalled(_tracked_sources()), [])


if __name__ == "__main__":
    unittest.main()
