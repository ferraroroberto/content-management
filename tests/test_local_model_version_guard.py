"""Regression tests for the classifier artefact/runtime version contract (issue #176).

``engagement/classify/local_model.joblib`` is a pickled sklearn pipeline, and a
pickle is bound to the scikit-learn that wrote it. Loading one across a minor
boundary emits ``InconsistentVersionWarning`` and then keeps going, so the
pipeline scores every comment from a degraded model. Nothing raises — the rows
just land outside both thresholds and read as ``ai=0 human=0 unknown=N``, which
is indistinguishable from a genuinely ambiguous batch.

These tests pin the guard that turns that into a loud failure: the sidecar
records the writing version, the loader compares it against the runtime, and a
minor-version skew raises ``ModelVersionMismatch`` naming both versions.

Every test builds a real (tiny) pipeline and dumps it to a temp dir — no
database, no network.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import json
import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import joblib
import pandas as pd
import sklearn
from sklearn.exceptions import InconsistentVersionWarning

from engagement.classify import local_model
from engagement.classify.local_model import ModelVersionMismatch

RUNTIME_VERSION = sklearn.__version__


def _fit_tiny_pipeline():
    """Smallest pipeline the real featurizer shape supports, fit on toy rows."""
    rows = [
        {"text": "great post thanks for sharing", "label": 1},
        {"text": "great post thanks so much", "label": 1},
        {"text": "the handoff between teams was our real bottleneck", "label": 0},
        {"text": "our real bottleneck was the handoff not the tooling", "label": 0},
    ]
    frame = pd.DataFrame(
        [
            {
                "text": r["text"],
                "text_len": len(r["text"]),
                "is_emoji_only": 0,
                "generic_praise_hits": 2 if r["label"] else 0,
                "has_personal_token": 0 if r["label"] else 1,
                "sub_2_min": r["label"],
                "exact_text_duplicate": 0,
            }
            for r in rows
        ]
    )
    pipeline = local_model._build_pipeline()
    pipeline.fit(frame, [r["label"] for r in rows])
    return pipeline, frame


class VersionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.model_path = tmp / "local_model.joblib"
        self.meta_path = tmp / "local_model.json"

        original_model, original_meta = local_model.MODEL_PATH, local_model.META_PATH
        local_model.MODEL_PATH = self.model_path
        local_model.META_PATH = self.meta_path

        def restore() -> None:
            local_model.MODEL_PATH = original_model
            local_model.META_PATH = original_meta
            local_model._load_model_cached.cache_clear()

        self.addCleanup(restore)
        local_model._load_model_cached.cache_clear()

        self.pipeline, self.frame = _fit_tiny_pipeline()

    def _write_artefact(self, sklearn_version: str | None) -> None:
        joblib.dump(self.pipeline, self.model_path)
        if sklearn_version is not None:
            self.meta_path.write_text(
                json.dumps({"platform": "linkedin", "sklearn_version": sklearn_version}),
                encoding="utf-8",
            )

    # ---- the lossless "no model yet" path must stay soft ----

    def test_missing_artefact_returns_none(self) -> None:
        self.assertIsNone(local_model._load_model_cached())
        self.assertFalse(local_model.model_is_available())

    # ---- matching version loads and predicts ----

    def test_matching_version_loads(self) -> None:
        self._write_artefact(RUNTIME_VERSION)
        model = local_model._load_model_cached()
        self.assertIsNotNone(model)
        prob = float(model.predict_proba(self.frame.head(1))[0, 1])
        self.assertTrue(0.0 <= prob <= 1.0)

    def test_patch_level_skew_is_tolerated(self) -> None:
        """sklearn keeps pickles portable across patch releases — only minor
        boundaries are a real break, so a patch bump must not block loading."""
        major, minor = local_model._major_minor(RUNTIME_VERSION)
        self._write_artefact(f"{major}.{minor}.999")
        self.assertIsNotNone(local_model._load_model_cached())

    def test_sidecar_without_recorded_version_still_loads(self) -> None:
        """Artefacts trained before #176 have no `sklearn_version`; they fall
        through to sklearn's own unpickle warning rather than being rejected."""
        joblib.dump(self.pipeline, self.model_path)
        self.meta_path.write_text(json.dumps({"platform": "linkedin"}), encoding="utf-8")
        self.assertIsNotNone(local_model._load_model_cached())

    # ---- the actual bug: a minor-version skew must fail loudly ----

    def test_recorded_minor_skew_raises_naming_both_versions(self) -> None:
        major, minor = local_model._major_minor(RUNTIME_VERSION)
        stale = f"{major}.{minor - 1}.0"
        self._write_artefact(stale)

        with self.assertRaises(ModelVersionMismatch) as ctx:
            local_model._load_model_cached()

        message = str(ctx.exception)
        self.assertIn(stale, message)
        self.assertIn(RUNTIME_VERSION, message)
        self.assertIn("local_model train", message)

    def test_unpickle_warning_skew_raises_without_a_sidecar(self) -> None:
        """The pre-#176 artefact case: no recorded version, but sklearn warns
        on unpickle. That warning must become an error, not a log line."""
        joblib.dump(self.pipeline, self.model_path)
        major, minor = local_model._major_minor(RUNTIME_VERSION)
        stale = f"{major}.{minor - 1}.0"

        real_load = joblib.load

        def load_with_skew_warning(path, *args, **kwargs):
            warnings.warn(
                InconsistentVersionWarning(
                    estimator_name="LogisticRegression",
                    current_sklearn_version=RUNTIME_VERSION,
                    original_sklearn_version=stale,
                )
            )
            return real_load(path, *args, **kwargs)

        with patch.object(joblib, "load", load_with_skew_warning):
            with self.assertRaises(ModelVersionMismatch) as ctx:
                local_model._load_model_cached()
        self.assertIn(stale, str(ctx.exception))

    def test_predict_one_propagates_the_mismatch(self) -> None:
        """`predict_one` swallows prediction errors into None by design — the
        version mismatch must not be swallowed with them, or the caller sees a
        silent `unknown` again."""
        major, minor = local_model._major_minor(RUNTIME_VERSION)
        self._write_artefact(f"{major}.{minor - 1}.0")

        with self.assertRaises(ModelVersionMismatch):
            local_model.predict_one({"text": "great post"}, set())


class VersionComparisonTests(unittest.TestCase):
    def test_major_minor_parsing(self) -> None:
        self.assertEqual(local_model._major_minor("1.9.0"), (1, 9))
        self.assertEqual(local_model._major_minor("1.10.2.post1"), (1, 10))
        self.assertIsNone(local_model._major_minor("not-a-version"))

    def test_compatibility_rules(self) -> None:
        self.assertTrue(local_model._versions_compatible("1.9.0", "1.9.4"))
        self.assertFalse(local_model._versions_compatible("1.8.0", "1.9.0"))
        self.assertFalse(local_model._versions_compatible("1.9.0", "2.0.0"))
        # Unparseable on either side falls back to an exact string match.
        self.assertTrue(local_model._versions_compatible("nightly", "nightly"))
        self.assertFalse(local_model._versions_compatible("nightly", "1.9.0"))


if __name__ == "__main__":
    unittest.main()
