"""The status of one stored triage run, declared once.

A row of ``triage_runs`` starts ``running`` and ends ``done`` or ``failed``; nothing moves
a finished run back. This module is the only place that names those states, maps a stored
string to one and says which moves are legal, so the store, the runner and the review tab
never compare the raw string. ``docs/triage-run-status.md`` has the state table.

The string values are the ones the ``status`` column holds (``schema.sql``), so they never
change.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Union


class RunStatus(StrEnum):
    """Where one stored run stands. The set is closed."""

    RUNNING = "running"  # registered, the stages are still working
    DONE = "done"  # finished, results stored: the review tab can open it
    FAILED = "failed"  # a stage raised; ``stats["error"]`` carries the cause
    UNKNOWN = "unknown"  # the stored value names none of the above; never written


StatusLike = Union[RunStatus, str, None]


class IllegalRunTransition(ValueError):
    """A run was asked to move somewhere only a running run may go."""


def derive_run_status(stored: StatusLike) -> RunStatus:
    """The state a stored ``status`` value names; ``UNKNOWN`` when it names none.

    ``UNKNOWN`` is its own answer: a reader must not treat it as ``DONE``, so a run it
    cannot read is never opened for review.
    """
    if isinstance(stored, RunStatus):
        return stored
    if not isinstance(stored, str):
        return RunStatus.UNKNOWN
    try:
        return RunStatus(stored.strip().lower())
    except ValueError:
        return RunStatus.UNKNOWN


def settled_status(target: StatusLike) -> RunStatus:
    """The state a finishing run takes, ``DONE`` or ``FAILED``; anything else is refused.

    The other half of the rule (only a ``running`` row may settle) is a compare-and-set in
    ``db.mark_run``, because the row's current value lives in the store, not here.
    """
    state = derive_run_status(target)
    if state not in (RunStatus.DONE, RunStatus.FAILED):
        raise IllegalRunTransition(f"a run settles as done or failed, not {target!r}")
    return state
