"""Tiny local watermark file — ``results/newsletter/triage/state.json`` (gitignored).

``reviewed_until`` = the end (exclusive) of the last window the owner applied a review for;
``runs`` = CLI run log. Shared by the engine (``run.py``) and the review logic (``review.py``)
so the control panel does not have to import the whole engine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIAGE_DIR = REPO_ROOT / "results" / "newsletter" / "triage"
STATE_PATH = TRIAGE_DIR / "state.json"


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
