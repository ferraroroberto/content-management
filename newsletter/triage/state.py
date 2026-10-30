"""Tiny local watermark file — ``results/newsletter/triage/state.json`` (gitignored).

``reviewed_until`` = the end (exclusive) of the last window the owner applied a review for;
``runs`` = CLI run log. Shared by the engine (``run.py``) and the review logic (``review.py``)
so the control panel does not have to import the whole engine.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
TRIAGE_DIR = REPO_ROOT / "results" / "newsletter" / "triage"
STATE_PATH = TRIAGE_DIR / "state.json"

logger = logging.getLogger("newsletter_triage.state")


class StateUnreadableError(RuntimeError):
    """The state file exists but does not parse; saving would destroy the ``reviewed_until`` watermark."""


def _read_state(path: Path) -> Dict[str, Any]:
    """Parsed state file; ``{}`` when absent. Raises ``OSError``/``ValueError`` when present but unreadable."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_state(path: Path = STATE_PATH) -> Dict[str, Any]:
    try:
        return _read_state(path)
    except (OSError, ValueError) as exc:
        logger.warning("⚠️ triage state %s is unreadable (%s) — treating it as empty; it will not be overwritten",
                       path, type(exc).__name__)
        return {}


def save_state(state: Dict[str, Any], path: Path = STATE_PATH) -> None:
    try:
        _read_state(path)
    except (OSError, ValueError) as exc:
        logger.error("❌ refusing to overwrite unreadable triage state %s (%s) — fix or delete it first",
                     path, type(exc).__name__)
        raise StateUnreadableError(f"{path} exists but cannot be parsed ({type(exc).__name__})") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
