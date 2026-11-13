"""The review status of one stored comment, declared once.

A row of the ``comments`` table starts ``pending`` and the owner (or a cascade) moves it to
``approved``, ``rejected`` or ``ignored``; ``sent`` marks a reply that went out. This module is
the only place that names those states and maps a stored string to one, so the writers, the
cascades, the review app and the UI check never spell or compare the raw string.
``docs/engagement-comment-status.md`` has the state table.

The string values are the ones ``schema.sql`` documents for the ``status`` column, so they never
change.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Union


class CommentStatus(StrEnum):
    """Where one comment stands in the owner's review. The set is closed."""

    PENDING = "pending"  # waiting for a decision; also where a surfaced or reopened comment returns
    APPROVED = "approved"  # the owner (or the blacklist cascade) accepted the suggested action
    SENT = "sent"  # the reply went out
    REJECTED = "rejected"  # the owner declined the suggestion
    IGNORED = "ignored"  # not worth a decision, e.g. already replied to
    UNKNOWN = "unknown"  # the stored value names none of the above; never written


#: The five states a row can hold, in the order the review UI lists them.
STORED_STATUSES = (CommentStatus.PENDING, CommentStatus.APPROVED, CommentStatus.SENT,
                   CommentStatus.REJECTED, CommentStatus.IGNORED)

StatusLike = Union[CommentStatus, str, None]


def derive_comment_status(stored: StatusLike) -> CommentStatus:
    """The state a stored ``status`` value names; ``UNKNOWN`` when it names none (a missing one included).

    ``UNKNOWN`` is its own answer: a reader must not treat it as ``PENDING``, which would put an
    unreadable row into the decision queue.
    """
    if isinstance(stored, CommentStatus):
        return stored
    if not isinstance(stored, str):
        return CommentStatus.UNKNOWN
    try:
        return CommentStatus(stored.strip().lower())
    except ValueError:
        return CommentStatus.UNKNOWN


def writable_status(target: StatusLike) -> CommentStatus:
    """The state a write may store; ``UNKNOWN`` and any value outside the set raise ``ValueError``."""
    state = derive_comment_status(target)
    if state is CommentStatus.UNKNOWN:
        raise ValueError(f"unknown comment status {target!r}")
    return state
