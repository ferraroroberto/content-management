r"""Structural check: every ``config.json`` block the code reads must have a
placeholder in ``config/config_example.json``.

A fresh clone only ever starts from ``config_example.json`` (the README's
install step is ``Copy-Item config\config_example.json config\config.json``).
If a module reads a top-level block via ``load_block("<name>")`` /
``load_config_block("<name>")`` that the example never declares, the clone has
no schema to copy and the first run that touches it fails with a bare
``KeyError`` / ``RuntimeError`` far from the real cause (audit-495).

This pins the two sides together so a new ``<platform>_session.py`` (or any
other block reader) that forgets the matching example block fails the test
suite instead of a fresh-clone's first run.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import json
import re
import unittest

from planning._session_base import REPO_ROOT

CONFIG_EXAMPLE_PATH = REPO_ROOT / "config" / "config_example.json"

# Mirrors the vendored-copy exclusions the ratchets use: these are portable
# components copied from elsewhere, not this repo's own config consumers.
_EXCLUDED_DIRS = {"gmail_readonly", "google_oauth_common", "tests", ".venv"}

_BLOCK_CALL_RE = re.compile(r"load_(?:config_)?block\(\s*[\"']([a-zA-Z_][a-zA-Z0-9_]*)[\"']\s*\)")


def _required_block_names() -> set[str]:
    """Statically collect every literal block name passed to a loader call."""
    names: set[str] = set()
    for path in REPO_ROOT.rglob("*.py"):
        if _EXCLUDED_DIRS & set(path.relative_to(REPO_ROOT).parts):
            continue
        names.update(_BLOCK_CALL_RE.findall(path.read_text(encoding="utf-8")))
    return names


class ConfigExampleSchemaTests(unittest.TestCase):
    def test_every_loaded_block_has_an_example_placeholder(self) -> None:
        required = _required_block_names()
        # Sanity: the scan itself must find a representative sample, or a
        # change to the loader call pattern would silently blind this test.
        self.assertIn("substack", required)
        self.assertIn("newsletter_triage", required)

        example = json.loads(CONFIG_EXAMPLE_PATH.read_text(encoding="utf-8"))
        missing = sorted(name for name in required if name not in example)
        self.assertEqual(
            missing, [],
            f"config/config_example.json is missing a placeholder block for: "
            f"{missing} — a fresh clone has no schema to copy for these "
            f"load_block()/load_config_block() consumers.",
        )


if __name__ == "__main__":
    unittest.main()
