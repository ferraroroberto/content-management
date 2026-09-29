"""Regression test for issue #321 finding 2: ``planning/_probe.py`` crashed with
``AttributeError`` because ``page.accessibility`` no longer exists in the
installed Playwright (1.61.0+; ``hasattr(Page, "accessibility")`` is ``False``).
The fix switches to ``page.locator("body").aria_snapshot()``, which returns an
indented YAML-ish text dump instead of a nested dict, and parses that text
into the same ``(role, name)`` candidate pairs the old dict-walk produced.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import sys
import types
import unittest
from contextlib import contextmanager

import planning._probe as probe_mod


class ParseAriaSnapshotTests(unittest.TestCase):
    def test_extracts_role_name_pairs_in_order(self):
        snapshot = (
            '- heading "Title" [level=1]\n'
            '- button "Submit"\n'
            '- link "Home Link":\n'
            '  - /url: "#"\n'
            '- dialog "My Dialog": hi\n'
        )
        self.assertEqual(
            probe_mod._parse_aria_snapshot(snapshot),
            [
                ("heading", "Title"),
                ("button", "Submit"),
                ("link", "Home Link"),
                ("dialog", "My Dialog"),
            ],
        )

    def test_skips_property_lines_and_unnamed_nodes(self):
        snapshot = '- generic:\n  - /url: "https://example.com"\n- text: "loose text, no role line"\n'
        self.assertEqual(probe_mod._parse_aria_snapshot(snapshot), [])

    def test_empty_snapshot_yields_no_pairs(self):
        self.assertEqual(probe_mod._parse_aria_snapshot(""), [])


class _FakePage:
    def __init__(self, aria_text: str, title: str):
        self._aria_text = aria_text
        self._title = title

    def locator(self, _selector: str) -> "_FakePage":
        return self

    def aria_snapshot(self) -> str:
        return self._aria_text

    def title(self) -> str:
        return self._title


class _FakeSession:
    """Stands in for a real ``PlatformSession`` subclass — no Playwright, no
    network. Only the surface ``probe()`` touches: context-manager protocol,
    ``.page``, ``goto_with_login_check``, ``screenshot_failure``."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.page = _FakePage('- button "New post"\n- link "Home"\n', "Fake Feed")
        self.goto_calls: list[str] = []

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def goto_with_login_check(self, url: str) -> None:
        self.goto_calls.append(url)

    def screenshot_failure(self, label: str) -> None:
        pass


class ProbeEndToEndTests(unittest.TestCase):
    """Drives ``probe()`` through a fake platform registered under a fake
    module — proves the ``page.accessibility`` crash (issue #321) is gone
    without needing a real browser."""

    FAKE_MODULE_NAME = "tests._fake_probe_platform"

    def setUp(self):
        fake_module = types.ModuleType(self.FAKE_MODULE_NAME)
        fake_module.FakeSession = _FakeSession
        fake_module.load_fake_config = lambda: {"feed_url": "https://fake.example/feed"}
        sys.modules[self.FAKE_MODULE_NAME] = fake_module

        self._orig_registry = probe_mod._REGISTRY
        probe_mod._REGISTRY = dict(probe_mod._REGISTRY)
        probe_mod._REGISTRY["fakeplatform"] = (
            self.FAKE_MODULE_NAME, "FakeSession", "load_fake_config",
        )

    def tearDown(self):
        probe_mod._REGISTRY = self._orig_registry
        sys.modules.pop(self.FAKE_MODULE_NAME, None)

    def test_probe_does_not_crash_and_extracts_candidates(self):
        output = probe_mod.probe("fakeplatform", save_screenshot=False)
        self.assertIn("Fake Feed", output)
        self.assertIn("name='New post'", output)
        self.assertIn("name='Home'", output)


if __name__ == "__main__":
    unittest.main()
