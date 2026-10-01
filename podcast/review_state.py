"""The owner's per-clip review (issue #339): approved, dropped, or changes
requested with free-text feedback, one round per request.

Kept in ``<episode folder>/review.json`` beside ``episode.json``, so it
survives app restarts and pipeline re-runs. Keyed by clip number: a title
change renames a clip's files, never its number. Each round records the
feedback, then (once the ``revise`` stage ran) the edits proposed and applied,
what could not be mapped, and the version it produced. A round without
``applied_at`` is still open.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from podcast.episode import Episode

REVIEW_FILE = "review.json"
STATUSES = ("pending", "approved", "changes", "dropped")
DECIDED = ("approved", "dropped")


def _path(ep: Episode) -> Path:
    return ep.folder / REVIEW_FILE


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_review(ep: Episode) -> dict:
    path = _path(ep)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"clips": {}}


def save_review(ep: Episode, state: dict) -> None:
    """Write via a temp file and an atomic replace, so a reader never sees half a file."""
    path = _path(ep)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def clip_state(state: dict, number: int) -> dict:
    return state.setdefault("clips", {}).setdefault(str(number), {"status": "pending", "version": 1,
                                                                   "rounds": []})


def update(ep: Episode, number: int, change: Callable[[dict], None]) -> dict:
    """Load, change one clip's entry, save: a short read-modify-write, so the
    app and a running ``revise`` stage only overwrite each other's work if
    they touch the same moment."""
    state = load_review(ep)
    change(clip_state(state, number))
    save_review(ep, state)
    return state


def set_status(ep: Episode, number: int, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown review status {status!r}")
    return update(ep, number, lambda c: c.update(status=status))


def request_changes(ep: Episode, number: int, feedback: str) -> dict:
    """Open a round with the owner's feedback; an open round gets the new text appended."""
    text = feedback.strip()
    if not text:
        raise ValueError("feedback is empty")

    def change(c: dict) -> None:
        last = open_round(c)
        if last:
            last["feedback"] = f"{last['feedback']}\n{text}"
        else:
            c["rounds"].append({"at": now(), "feedback": text, "version": c["version"]})
        c["status"] = "changes"
    return update(ep, number, change)


def open_round(c: dict) -> Optional[dict]:
    rounds = c.get("rounds") or []
    return rounds[-1] if rounds and "applied_at" not in rounds[-1] else None


def to_revise(clips: list[dict], state: dict) -> list[int]:
    """Clips the ``revise`` stage works on: changes requested and a round still open."""
    entries = state.get("clips", {})
    return [c["number"] for c in clips
            if (e := entries.get(str(c["number"]))) and e.get("status") == "changes" and open_round(e)]


def counts(clips: list[dict], state: dict) -> dict[str, int]:
    out = dict.fromkeys(STATUSES, 0)
    for c in clips:
        out[state.get("clips", {}).get(str(c["number"]), {}).get("status", "pending")] += 1
    return out


def review_done(clips: list[dict], state: dict) -> bool:
    """Every clip approved or dropped (and at least one approved)."""
    n = counts(clips, state)
    return bool(clips) and n["approved"] > 0 and n["approved"] + n["dropped"] == len(clips)


def summary(clips: list[dict], state: dict) -> str:
    n = counts(clips, state)
    return (f"{n['approved']}/{len(clips)} approved, {n['dropped']} dropped, "
            f"{n['changes']} with changes requested, {n['pending']} pending")


def kept_clips(clips: list[dict], state: dict) -> list[dict]:
    """The clips that go into the package: everything not dropped."""
    entries = state.get("clips", {})
    return [c for c in clips if entries.get(str(c["number"]), {}).get("status") != "dropped"]
