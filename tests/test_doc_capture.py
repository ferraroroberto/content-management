"""Unit tests for the doc-capture engine's browserless logic (issue #110).

Covers the three contract guarantees:
* fail-safe masking — an entry without mask selectors is refused, never captured;
* input-hash idempotency — unchanged sources skip, changed sources recapture;
* README regeneration — deterministic block between markers, no-op on rerun.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from config.doc_capture import engine


def _manifest(features: dict) -> dict:
    return {"app": {"base_url": "http://localhost:8501"}, "features": features}


def _entry(**overrides) -> dict:
    entry = {
        "title": "📊 Reporting",
        "description": "Daily numbers pipeline.",
        "source_globs": ["app/tab_reporting.py"],
        "reach": {"label": "📊 reporting"},
        "wait": {"text": "daily numbers"},
        "mask": ['[data-testid="stCode"]'],
        "input_hash": None,
        "captured_at": None,
        "files": [],
    }
    entry.update(overrides)
    return entry


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "app").mkdir()
        (self.root / "app" / "tab_reporting.py").write_text("print('v1')\n", encoding="utf-8")

    def _plan(self, manifest, **kwargs):
        return engine.plan_features(manifest, repo_root=self.root, **kwargs)

    def test_missing_mask_is_refused(self):
        entry = _entry()
        del entry["mask"]
        with self.assertLogs(engine.logger, level="WARNING"):
            items = self._plan(_manifest({"reporting": entry}))
        self.assertEqual(items[0].action, engine.ACTION_SKIP_UNMASKED)

    def test_empty_mask_is_refused(self):
        with self.assertLogs(engine.logger, level="WARNING"):
            items = self._plan(_manifest({"reporting": _entry(mask=[])}))
        self.assertEqual(items[0].action, engine.ACTION_SKIP_UNMASKED)

    def test_unmasked_is_refused_even_with_force(self):
        with self.assertLogs(engine.logger, level="WARNING"):
            items = self._plan(_manifest({"reporting": _entry(mask=[])}), force=True)
        self.assertEqual(items[0].action, engine.ACTION_SKIP_UNMASKED)

    def test_never_captured_gets_captured(self):
        items = self._plan(_manifest({"reporting": _entry()}))
        self.assertEqual(items[0].action, engine.ACTION_CAPTURE)
        self.assertIsNotNone(items[0].new_hash)

    def test_unchanged_input_skips(self):
        entry = _entry()
        entry["input_hash"] = engine.compute_input_hash(entry, self.root)
        out = engine.screenshot_path("reporting", self.root)
        out.parent.mkdir(parents=True)
        out.write_bytes(b"png")
        items = self._plan(_manifest({"reporting": entry}))
        self.assertEqual(items[0].action, engine.ACTION_SKIP_UNCHANGED)

    def test_changed_source_recaptures(self):
        entry = _entry()
        entry["input_hash"] = engine.compute_input_hash(entry, self.root)
        out = engine.screenshot_path("reporting", self.root)
        out.parent.mkdir(parents=True)
        out.write_bytes(b"png")
        (self.root / "app" / "tab_reporting.py").write_text("print('v2')\n", encoding="utf-8")
        items = self._plan(_manifest({"reporting": entry}))
        self.assertEqual(items[0].action, engine.ACTION_CAPTURE)

    def test_missing_png_recaptures_despite_matching_hash(self):
        entry = _entry()
        entry["input_hash"] = engine.compute_input_hash(entry, self.root)
        items = self._plan(_manifest({"reporting": entry}))
        self.assertEqual(items[0].action, engine.ACTION_CAPTURE)

    def test_force_recaptures_unchanged(self):
        entry = _entry()
        entry["input_hash"] = engine.compute_input_hash(entry, self.root)
        out = engine.screenshot_path("reporting", self.root)
        out.parent.mkdir(parents=True)
        out.write_bytes(b"png")
        items = self._plan(_manifest({"reporting": entry}), force=True)
        self.assertEqual(items[0].action, engine.ACTION_CAPTURE)

    def test_mask_change_recaptures(self):
        entry = _entry()
        entry["input_hash"] = engine.compute_input_hash(entry, self.root)
        out = engine.screenshot_path("reporting", self.root)
        out.parent.mkdir(parents=True)
        out.write_bytes(b"png")
        entry["mask"] = ['[data-testid="stCode"]', '[data-testid="stMetric"]']
        items = self._plan(_manifest({"reporting": entry}))
        self.assertEqual(items[0].action, engine.ACTION_CAPTURE)

    def test_only_filters_features(self):
        manifest = _manifest({"reporting": _entry(), "planning": _entry(source_globs=[])})
        items = self._plan(manifest, only=["planning"])
        self.assertEqual([i.name for i in items], ["planning"])

    def test_capture_features_never_shoots_unmasked(self):
        # Belt and suspenders: even a hand-built unmasked "capture" list can't
        # reach a browser, because capture_features only acts on plan items.
        with self.assertLogs(engine.logger, level="WARNING"):
            items = self._plan(_manifest({"reporting": _entry(mask=[])}))
        captured = engine.capture_features(
            _manifest({"reporting": _entry(mask=[])}), items, repo_root=self.root
        )
        self.assertEqual(captured, 0)


class ReadmeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.readme = Path(self._tmp.name) / "README.md"
        self.readme.write_text(
            "# Title\n\nintro\n\n<!-- docs-shots:start -->\nstale\n<!-- docs-shots:end -->\n\ntail\n",
            encoding="utf-8",
        )

    def test_regenerates_between_markers_and_is_idempotent(self):
        entry = _entry(files=["docs/screenshots/reporting-desktop.png"])
        manifest = _manifest({"reporting": entry})
        self.assertTrue(engine.regenerate_readme(manifest, self.readme))
        text = self.readme.read_text(encoding="utf-8")
        self.assertIn("### 📊 Reporting", text)
        self.assertIn("Daily numbers pipeline.", text)
        self.assertIn("![📊 Reporting screenshot](docs/screenshots/reporting-desktop.png)", text)
        self.assertNotIn("stale", text)
        self.assertTrue(text.startswith("# Title\n\nintro\n\n"))
        self.assertTrue(text.endswith("<!-- docs-shots:end -->\n\ntail\n"))
        # Rerun with the same manifest → no-op.
        self.assertFalse(engine.regenerate_readme(manifest, self.readme))

    def test_uncaptured_feature_is_omitted(self):
        manifest = _manifest({"reporting": _entry(files=[])})
        with self.assertLogs(engine.logger, level="WARNING"):
            engine.regenerate_readme(manifest, self.readme)
        self.assertNotIn("### 📊 Reporting", self.readme.read_text(encoding="utf-8"))

    def test_missing_markers_fails_loud(self):
        self.readme.write_text("# no markers\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            engine.regenerate_readme(_manifest({"reporting": _entry()}), self.readme)


if __name__ == "__main__":
    unittest.main()
