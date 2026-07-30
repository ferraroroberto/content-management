"""Leaf module for picking a canned "thanks" reply from ``phrases.json``.

Deliberately has no dependency on ``engagement.db.client`` or
``engagement.classify.rules`` — safe for both to import without a cycle.
Single-source for the reply-picking logic that had drifted into two
independent implementations: ``engagement/db/client.py``'s
``_pick_thanks_reply`` existed only to dodge the very cycle this module
avoids (``rules.py`` already imports from ``client.py``), so a
``phrases.json`` schema change could update one path and silently miss the
other.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHRASES_PATH = REPO_ROOT / "engagement" / "classify" / "phrases.json"


def first_name(display_name: Optional[str]) -> str:
    if not display_name:
        return "there"
    return display_name.strip().split()[0]


def pick_reply_from_templates(display_name: Optional[str], phrases: dict) -> str:
    """Pick a random ``like_and_thanks`` reply template from an already-loaded ``phrases`` dict.

    Pure — no file I/O. Falls back to a fixed default if no templates are
    configured. Used by callers (e.g. ``rules.py``) that load ``phrases.json``
    once per run and pick many replies against the same dict.
    """
    first = first_name(display_name)
    templates = phrases.get("reply_templates", {}).get("like_and_thanks", [])
    if not templates:
        return f"Thanks {first}! 🙏"
    return random.choice(templates).format(first_name=first)


def pick_thanks_reply(display_name: Optional[str]) -> str:
    """Pick a random canned thanks-reply, reading ``phrases.json`` itself.

    Convenience wrapper around :func:`pick_reply_from_templates` for callers
    (e.g. ``engagement/db/client.py``) that don't already have a loaded
    ``phrases`` dict on hand. Falls back to a fixed default on any error
    (missing/corrupt file, empty template list).
    """
    try:
        with open(PHRASES_PATH, "r", encoding="utf-8") as fp:
            phrases = json.load(fp)
        return pick_reply_from_templates(display_name, phrases)
    except Exception:
        return f"Thanks {first_name(display_name)}! 🙏"
