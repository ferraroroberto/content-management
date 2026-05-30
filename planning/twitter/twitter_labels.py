"""Accessible-name label registry for the Twitter (X) Playwright driver.

X's native composer is driven mostly via stable ``data-testid`` hooks, but a
handful of affordances — the onboarding/upsell dialog dismissals, the Schedule
toolbar button, the modal Confirm, the final Schedule action, and the discard
prompts — are only reachable by their visible / accessible name. Those names
are exactly what shifts when X relabels a button or A/Bs the composer, so they
are centralized here as compiled regexes rather than scattered inline through
``schedule_twitter_posts``.

This mirrors ``planning/linkedin/linkedin_labels.py``: the self-healing
scheduler (issue #64) can scan and patch one small module instead of hunting an
accessible-name string buried in scheduling logic. When X ships a new label
variant, add it to the alternation here — do NOT re-inline a ``re.compile`` at
the call site.

Structural selectors (``data-testid``, ``role="dialog"``, ``input[type=file]``,
``[aria-label="…"]`` CSS, ``text=/…/`` engine selectors) deliberately stay
inline in the driver: they are DOM-structure hooks, not user-facing labels.
"""

from __future__ import annotations

import re

# Onboarding / "try premium" dialogs that X intermittently pops over the feed.
# Tried in order against the dialog's buttons; first match dismisses it.
DISMISS_DIALOG_BTN_RES = (
    re.compile(r"^not now$", re.I),
    re.compile(r"^skip for now$", re.I),
    re.compile(r"^maybe later$", re.I),
    re.compile(r"^dismiss$", re.I),
    re.compile(r"^close$", re.I),
)

# Composer's calendar-clock toolbar button that opens the Schedule modal. The
# accessible name is "Schedule" or "Schedule post" depending on the build.
SCHEDULE_TOOLBAR_BTN_RE = re.compile(r"^schedule(\spost)?$", re.I)

# Bottom-right primary action inside the Schedule modal.
CONFIRM_BTN_RE = re.compile(r"^confirm$", re.I)

# The composer's primary action once a schedule is attached reads "Schedule"
# (instead of "Post"). Distinct from SCHEDULE_TOOLBAR_BTN_RE, which we do NOT
# want to match here.
FINAL_SCHEDULE_BTN_RE = re.compile(r"^schedule$", re.I)

# Discard prompt shown when closing a composer with unsaved content.
DISCARD_BTN_RES = (
    re.compile(r"^discard$", re.I),
    re.compile(r"^discard changes$", re.I),
)

# Cancel / Close affordances used to back out of the Schedule modal + composer
# on a dry-run.
CANCEL_CLOSE_BTN_RES = (
    re.compile(r"^cancel$", re.I),
    re.compile(r"^close$", re.I),
)


__all__ = [
    "DISMISS_DIALOG_BTN_RES",
    "SCHEDULE_TOOLBAR_BTN_RE",
    "CONFIRM_BTN_RE",
    "FINAL_SCHEDULE_BTN_RE",
    "DISCARD_BTN_RES",
    "CANCEL_CLOSE_BTN_RES",
]
