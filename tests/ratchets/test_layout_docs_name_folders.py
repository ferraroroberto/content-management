"""The layout docs name every tracked root folder and every shared module.

The doc-references ratchet checks that a named path exists, not that an existing
folder is named, so a new root folder or a module added to ``src/`` or ``common/``
left ``README.md`` and ``docs/layout.md`` stale without any gate noticing.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.ratchets.test_layout_docs_name_folders -v
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tracked(*pathspec: str) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *pathspec],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return [name for name in result.stdout.split("\0") if name]


def _root_folders() -> list[str]:
    return sorted({name.split("/")[0] for name in _tracked() if "/" in name and not name.startswith(".")})


def _shared_modules() -> list[str]:
    return [name for name in _tracked("src", "common") if name.count("/") == 1 and name.endswith(".py")]


class LayoutDocsNameFoldersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layout = (REPO_ROOT / "docs" / "layout.md").read_text(encoding="utf-8")
        self.readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_scan_found_folders_and_modules(self) -> None:
        self.assertTrue(_root_folders(), "no tracked root folder was found")
        self.assertTrue(_shared_modules(), "no src/ or common/ module was found")

    def test_layout_names_every_root_folder(self) -> None:
        missing = [name for name in _root_folders() if f"`{name}/`" not in self.layout]
        self.assertEqual([], missing, "docs/layout.md does not name these root folders")

    def test_readme_tree_names_every_root_folder(self) -> None:
        missing = [name for name in _root_folders() if f"── {name}/ " not in self.readme]
        self.assertEqual([], missing, "the README project tree does not list these root folders")

    def test_layout_and_readme_name_every_shared_module(self) -> None:
        modules = [Path(name).name for name in _shared_modules()]
        missing = [
            f"{doc}: {module}"
            for module in modules
            for doc, text in (("docs/layout.md", self.layout), ("README.md", self.readme))
            if module not in text
        ]
        self.assertEqual([], missing, "shared src/ and common/ modules missing from the layout docs")


if __name__ == "__main__":
    unittest.main()
