"""Local sklearn classifier — Phase 2a.

Logistic regression on TF-IDF (word 1-2 + char_wb 3-5) plus six per-comment
scalar features. Trained on accumulated commenter-level whitelist/blacklist
labels (every comment by a blacklisted commenter is AI, every comment by a
whitelisted commenter is human). Layered after `engagement.classify.rules`:
called only on rows the rules layer left as `unknown`.

If no model has been trained yet (no `.joblib` on disk), `predict_one` returns
None and the rules pipeline falls back to the same "unknown / surface_to_me"
behavior it had pre-Phase-2a. Training is gated by
`rules.local_model_min_train_per_class` in `phrases.json` so a too-small label
set never produces a useless model.

The featurizer (`featurize_one`) is the single source of truth, used at both
train and inference time. It re-uses the per-comment helpers from `rules.py`
so the two layers can never drift.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger("engagement.classify.local_model")

MODEL_DIR = Path(__file__).resolve().parent
MODEL_PATH = MODEL_DIR / "local_model.joblib"
META_PATH = MODEL_DIR / "local_model.json"

SCALAR_COLS = [
    "text_len",
    "is_emoji_only",
    "generic_praise_hits",
    "has_personal_token",
    "sub_2_min",
    "exact_text_duplicate",
]


# ---------- Features ----------

def featurize_one(comment: dict, duplicate_texts: set, phrases: dict) -> dict:
    """Build one feature row. Uses the rules.py helpers so training and
    inference use byte-identical derived signals."""
    from engagement.classify.rules import (
        _generic_praise_hits,
        _has_personal_token,
        _is_emoji_only,
        _seconds_after,
    )

    text = (comment.get("text") or "").strip()
    text_norm = text.lower()
    secs = _seconds_after(comment.get("post_posted_at"), comment.get("posted_at"))
    return {
        "text": text,
        "text_len": len(text),
        "is_emoji_only": int(_is_emoji_only(text)),
        "generic_praise_hits": _generic_praise_hits(text_norm, phrases["generic_praise_substrings"]),
        "has_personal_token": int(_has_personal_token(text_norm, phrases["personal_tokens"])),
        "sub_2_min": int(secs is not None and secs <= 120),
        "exact_text_duplicate": int(bool(text_norm) and text_norm in duplicate_texts),
    }


def _build_pipeline():
    from sklearn.compose import ColumnTransformer
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    pre = ColumnTransformer(
        transformers=[
            ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, lowercase=True), "text"),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, lowercase=True), "text"),
            ("scalars", StandardScaler(with_mean=False), SCALAR_COLS),
        ],
    )
    return Pipeline(
        [
            ("feats", pre),
            ("clf", LogisticRegression(max_iter=1000, class_weight="balanced", solver="liblinear")),
        ]
    )


# ---------- Train / Eval ----------

def _prepare_dataset(platform: str):
    """Fetch labeled rows + return (DataFrame, y, phrases, n_ai, n_human)."""
    import numpy as np
    import pandas as pd

    from engagement.classify.rules import _build_duplicate_set, load_phrases
    from engagement.db.client import fetch_labeled_training_set

    phrases = load_phrases()
    rows = fetch_labeled_training_set(platform)
    if not rows:
        return None, None, phrases, 0, 0

    duplicates = _build_duplicate_set(rows)
    feats = [featurize_one(r, duplicates, phrases) for r in rows]
    df = pd.DataFrame(feats)
    y = np.array([r["_label"] for r in rows])
    n_ai = int((y == 1).sum())
    n_human = int((y == 0).sum())
    return df, y, phrases, n_ai, n_human


def train(platform: str = "linkedin") -> dict:
    df, y, phrases, n_ai, n_human = _prepare_dataset(platform)
    min_per_class = phrases["rules"]["local_model_min_train_per_class"]

    if df is None:
        logger.error(
            "❌ no labeled rows for %s — mark some commenters whitelist/blacklist first",
            platform,
        )
        return {"status": "no_labels", "n_ai": 0, "n_human": 0}

    if n_ai < min_per_class or n_human < min_per_class:
        logger.error(
            "❌ insufficient labels (need ≥%d per class); got ai=%d human=%d",
            min_per_class, n_ai, n_human,
        )
        return {
            "status": "insufficient",
            "n_ai": n_ai,
            "n_human": n_human,
            "min_per_class": min_per_class,
        }

    from sklearn.model_selection import StratifiedKFold, cross_val_score

    pipeline = _build_pipeline()
    n_splits = min(5, n_ai, n_human)
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    auc_scores = cross_val_score(pipeline, df, y, cv=cv, scoring="roc_auc")
    mean_auc = float(auc_scores.mean())

    pipeline.fit(df, y)

    import joblib
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, MODEL_PATH)

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "platform": platform,
        "n_ai": n_ai,
        "n_human": n_human,
        "cv_auc_mean": mean_auc,
        "cv_auc_per_fold": [float(s) for s in auc_scores],
        "n_splits": n_splits,
        "threshold": phrases["rules"]["local_model_ai_threshold"],
        "min_per_class": min_per_class,
        "scalar_cols": SCALAR_COLS,
    }
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _load_model_cached.cache_clear()
    logger.info(
        "✅ trained local model — n_ai=%d n_human=%d cv_auc=%.3f → %s",
        n_ai, n_human, mean_auc, MODEL_PATH,
    )
    return {"status": "trained", **meta}


def evaluate(platform: str = "linkedin") -> dict:
    df, y, phrases, n_ai, n_human = _prepare_dataset(platform)
    if df is None:
        return {"status": "no_labels", "n_ai": 0, "n_human": 0}
    if n_ai < 2 or n_human < 2:
        return {"status": "insufficient", "n_ai": n_ai, "n_human": n_human}

    from sklearn.metrics import (
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )
    from sklearn.model_selection import StratifiedKFold, cross_val_predict

    threshold = phrases["rules"]["local_model_ai_threshold"]
    n_splits = min(5, n_ai, n_human)
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
    pipeline = _build_pipeline()
    probs = cross_val_predict(pipeline, df, y, cv=cv, method="predict_proba")[:, 1]
    pred = (probs >= threshold).astype(int)

    out = {
        "status": "ok",
        "n_ai": n_ai,
        "n_human": n_human,
        "n_splits": n_splits,
        "threshold": threshold,
        "cv_auc": float(roc_auc_score(y, probs)),
        "precision_at_threshold": float(precision_score(y, pred, zero_division=0)),
        "recall_at_threshold": float(recall_score(y, pred, zero_division=0)),
        "f1_at_threshold": float(f1_score(y, pred, zero_division=0)),
        "confusion_matrix_at_threshold": confusion_matrix(y, pred).tolist(),
    }
    logger.info(
        "📊 eval — auc=%.3f p=%.3f r=%.3f f1=%.3f (threshold=%.2f, n=%d/%d)",
        out["cv_auc"], out["precision_at_threshold"], out["recall_at_threshold"],
        out["f1_at_threshold"], threshold, n_ai, n_human,
    )
    return out


# ---------- Inference ----------

@lru_cache(maxsize=1)
def _load_model_cached():
    """Return the persisted pipeline, or None if no model has been trained.
    Cached for the lifetime of the process — `train()` clears the cache after
    overwriting the file."""
    if not MODEL_PATH.exists():
        return None
    try:
        import joblib
        return joblib.load(MODEL_PATH)
    except Exception as err:
        logger.warning("⚠️ local_model load failed: %s", err)
        return None


def predict_one(comment: dict, duplicate_texts: set, phrases: Optional[dict] = None) -> Optional[float]:
    """Return P(AI) for one comment, or None if no model is loaded."""
    model = _load_model_cached()
    if model is None:
        return None
    if phrases is None:
        from engagement.classify.rules import load_phrases
        phrases = load_phrases()
    try:
        import pandas as pd
        feat = featurize_one(comment, duplicate_texts, phrases)
        return float(model.predict_proba(pd.DataFrame([feat]))[0, 1])
    except Exception as err:
        logger.warning("⚠️ local_model predict failed: %s", err)
        return None


def model_is_available() -> bool:
    return _load_model_cached() is not None


# ---------- CLI ----------

def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Train or evaluate the engagement local classifier.")
    parser.add_argument("action", choices=["train", "eval"], nargs="?", default="train")
    parser.add_argument("--platform", default="linkedin")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )

    result = train(args.platform) if args.action == "train" else evaluate(args.platform)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
