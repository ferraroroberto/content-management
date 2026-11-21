"""The docs' counts, launch paths and screenshot manifest match the code.

The doc-references ratchet only resolves repo paths written in forward-slash form
inside ``README.md``, ``CLAUDE.md`` and ``docs/``. It cannot see a stale count, a
backslash launch command in a per-tool README, or a control-panel tab with no
screenshot entry, so each of those is checked here.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.ratchets.test_docs_match_surfaces -v
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_NUMBER_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_STREAMLIT_RUN = re.compile(r"streamlit run\s+([^\s`|]+)")


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _intro_surface_count(readme: str) -> tuple[int, int]:
    """(count the opening line states, number of bold bullets listed under it)."""
    head, _, rest = readme.partition("\n## ")
    stated = re.search(r"^(\w+) surfaces, one repo:", head, re.MULTILINE)
    assert stated, "README.md no longer opens with '<N> surfaces, one repo:'"
    return _NUMBER_WORDS[stated.group(1).lower()], len(re.findall(r"^- \*\*", head, re.MULTILINE))


class SurfaceCountTests(unittest.TestCase):
    def test_readme_opening_count_matches_its_list(self) -> None:
        stated, listed = _intro_surface_count(_read("README.md"))
        self.assertEqual(listed, stated, "README.md opening line and its surface list disagree")


class LaunchCommandTests(unittest.TestCase):
    def test_documented_streamlit_launch_paths_exist(self) -> None:
        docs = [REPO_ROOT / "README.md", REPO_ROOT / "CLAUDE.md", *REPO_ROOT.glob("docs/**/*.md"),
                *REPO_ROOT.glob("tools/**/*.md")]
        found = []
        missing = []
        for doc in docs:
            for target in _STREAMLIT_RUN.findall(doc.read_text(encoding="utf-8")):
                found.append(target)
                if not (REPO_ROOT / target.replace("\\", "/")).is_file():
                    missing.append(f"{doc.relative_to(REPO_ROOT).as_posix()}: {target}")
        self.assertTrue(found, "no documented streamlit launch command was found")
        self.assertEqual([], missing, "documented streamlit launch paths that do not exist")


class ScreenshotManifestTests(unittest.TestCase):
    def test_every_control_panel_tab_has_a_manifest_entry(self) -> None:
        manifest = json.loads(_read("docs/screenshots/manifest.json"))
        covered = {glob for feature in manifest["features"].values() for glob in feature["source_globs"]}
        tabs = sorted(path.relative_to(REPO_ROOT).as_posix() for path in (REPO_ROOT / "tools" / "app").glob("tab_*.py"))
        self.assertTrue(tabs, "no control-panel tab module was found")
        self.assertEqual([], [tab for tab in tabs if tab not in covered],
                         "control-panel tabs with no docs/screenshots/manifest.json entry")


if __name__ == "__main__":
    unittest.main()
