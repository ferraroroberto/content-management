"""The owner's per-clip review: approved, dropped, or changes
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
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Callable, Optional

from common.atomic_write import atomic_write_text
from podcast.episode import Episode

REVIEW_FILE = "review.json"


class ClipStatus(StrEnum):
    """Where one clip stands in the owner's review. The set is closed; the values are what ``review.json`` holds."""

    PENDING = "pending"  # not decided yet; also where a clip returns after a revision or a reopen
    APPROVED = "approved"  # the owner accepted this version
    CHANGES = "changes"  # feedback is saved and a round is open for the ``revise`` stage
    DROPPED = "dropped"  # left out of the package


DECIDED = (ClipStatus.APPROVED, ClipStatus.DROPPED)


def derive_clip_status(stored: object) -> ClipStatus:
    """The state a clip entry's stored ``status`` names; a missing one is ``PENDING``.

    A value that names none of the four raises: the file is written only by this module, so an
    unreadable status is corruption to surface, never a clip to count as pending.
    """
    if stored is None:
        return ClipStatus.PENDING
    try:
        return ClipStatus(stored)
    except ValueError:
        raise ValueError(f"unknown review status {stored!r}") from None


def _path(ep: Episode) -> Path:
    return ep.folder / REVIEW_FILE


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_review(ep: Episode) -> dict:
    path = _path(ep)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"clips": {}}


def save_review(ep: Episode, state: dict) -> None:
    """Write via a temp file and an atomic replace, so a reader never sees half a file."""
    atomic_write_text(_path(ep), json.dumps(state, indent=1, ensure_ascii=False))


def clip_state(state: dict, number: int) -> dict:
    return state.setdefault("clips", {}).setdefault(str(number), {"status": ClipStatus.PENDING.value, "version": 1,
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
    """Approve, drop or reopen a clip; ``changes`` is reached only through ``request_changes``, which saves the feedback."""
    target = derive_clip_status(status)
    if target is ClipStatus.CHANGES:
        raise ValueError("changes need feedback: use request_changes")
    return update(ep, number, lambda c: c.update(status=target.value))


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
        c["status"] = ClipStatus.CHANGES.value
    return update(ep, number, change)


def open_round(c: dict) -> Optional[dict]:
    rounds = c.get("rounds") or []
    return rounds[-1] if rounds and "applied_at" not in rounds[-1] else None


def to_revise(clips: list[dict], state: dict) -> list[int]:
    """Clips the ``revise`` stage works on: changes requested and a round still open."""
    entries = state.get("clips", {})
    return [c["number"] for c in clips
            if (e := entries.get(str(c["number"]))) and derive_clip_status(e.get("status")) is ClipStatus.CHANGES
            and open_round(e)]


def counts(clips: list[dict], state: dict) -> dict[ClipStatus, int]:
    out = dict.fromkeys(ClipStatus, 0)
    for c in clips:
        out[derive_clip_status(state.get("clips", {}).get(str(c["number"]), {}).get("status"))] += 1
    return out


def review_done(clips: list[dict], state: dict) -> bool:
    """Every clip approved or dropped (and at least one approved)."""
    n = counts(clips, state)
    approved, dropped = n[ClipStatus.APPROVED], n[ClipStatus.DROPPED]
    return bool(clips) and approved > 0 and approved + dropped == len(clips)


def summary(clips: list[dict], state: dict) -> str:
    n = counts(clips, state)
    return (f"{n[ClipStatus.APPROVED]}/{len(clips)} approved, {n[ClipStatus.DROPPED]} dropped, "
            f"{n[ClipStatus.CHANGES]} with changes requested, {n[ClipStatus.PENDING]} pending")


def kept_clips(clips: list[dict], state: dict) -> list[dict]:
    """The clips that go into the package: everything not dropped."""
    entries = state.get("clips", {})
    return [c for c in clips
            if derive_clip_status(entries.get(str(c["number"]), {}).get("status")) is not ClipStatus.DROPPED]
