"""The outcome of one scheduled Notion row, declared once.

Every platform scheduler ends each row in one of six outcomes, and the
orchestrator, the failure classifier and the drivers' own exit codes all ask the
same two questions of it: "did this row fail?" and "did something get
scheduled?". This module is the only place that names the outcomes, derives a
row's outcome from its legs and answers those two questions, so a reader never
re-derives either from the raw string. ``docs/planning-row-outcome.md`` has the
state table.

The string values are the ones the result JSON and the markdown summary carry
(consumed by the planning tab and ``/schedule-autoheal``), so they never change.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Iterable, Optional, Union


class RowStatus(StrEnum):
    """How one row (or one platform leg of a row) ended. The set is closed."""

    LIVE = "LIVE"  # scheduled for real
    DRY = "DRY"  # a dry run walked the flow to the final action without submitting
    SKIP = "SKIP"  # nothing to do: out of scope, already scheduled or switched off
    FAIL = "FAIL"  # no leg completed and at least one broke
    PARTIAL = "PARTIAL"  # at least one leg completed and at least one broke
    LOGIN_REQUIRED = "LOGIN-REQUIRED"  # the platform session is logged out


StatusLike = Union[RowStatus, str, None]


def parse(raw: StatusLike) -> Optional[RowStatus]:
    """The outcome a status string names, or ``None`` when it names none of the six.

    ``None`` is the explicit "unrecognised" answer: a reader that gets it must not
    treat the row as clean.
    """
    if isinstance(raw, RowStatus):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return RowStatus(raw.strip().upper())
    except ValueError:
        return None


def is_failure(status: StatusLike) -> bool:
    """Whether a row outcome counts as a failure.

    An unrecognised status counts as one: a row the pipeline cannot read is not
    reported clean.
    """
    parsed = parse(status)
    return parsed is None or parsed in (
        RowStatus.FAIL, RowStatus.PARTIAL, RowStatus.LOGIN_REQUIRED,
    )


def has_scheduled(status: StatusLike) -> bool:
    """Whether at least one post of the row went live (``LIVE`` or ``PARTIAL``)."""
    return parse(status) in (RowStatus.LIVE, RowStatus.PARTIAL)


def derive_row_status(legs: Iterable[StatusLike], dry_run: bool) -> RowStatus:
    """The row outcome from its per-leg outcomes.

    A leg is one platform (Videos) or one surface of a platform (Instagram story
    and post). A row with no legs reads ``SKIP``. A failed leg never reads as a
    clean ``DRY`` or ``LIVE``: it makes the row ``PARTIAL`` when another leg
    completed and ``FAIL`` when none did.
    """
    statuses = [parse(leg) for leg in legs]
    if any(s is RowStatus.LOGIN_REQUIRED for s in statuses):
        return RowStatus.LOGIN_REQUIRED
    if all(s is RowStatus.SKIP for s in statuses):
        return RowStatus.SKIP
    has_fail = any(s is RowStatus.FAIL for s in statuses)
    if all(s in (RowStatus.SKIP, RowStatus.FAIL) for s in statuses) and has_fail:
        return RowStatus.FAIL
    completed = RowStatus.DRY if dry_run else RowStatus.LIVE
    if dry_run and not has_fail:
        return RowStatus.DRY
    has_completed = any(s is completed for s in statuses)
    if has_completed and not has_fail:
        return RowStatus.LIVE
    if has_completed:
        return RowStatus.PARTIAL
    return RowStatus.FAIL
