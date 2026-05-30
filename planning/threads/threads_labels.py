"""Accessible-name label registry for the Threads Playwright driver.

The Threads composer exposes almost no stable ``data-testid`` hooks, so the
driver reaches most affordances by their visible / accessible name: the
caption textbox, the media-attach button, the 3-dots "Schedule…" menuitem, the
calendar's "Next month" / "Done" controls, and the final "Schedule" action.
Those names are exactly what shifts when Threads relabels a control, so they
are centralized here as compiled regexes rather than scattered inline through
``schedule_threads_posts``.

This mirrors ``planning/linkedin/linkedin_labels.py``: the self-healing
scheduler (issue #64) can scan and patch one small module instead of hunting an
accessible-name string buried in scheduling logic. When Threads ships a new
label variant, add it to the alternation here — do NOT re-inline a
``re.compile`` at the call site.

Structural selectors (``role="dialog"``, ``input[type=file]``,
``[aria-label="…"]`` CSS, ``text="…"`` engine selectors) and the positional JS
DOM probes deliberately stay inline in the driver: they are DOM-structure
hooks, not user-facing labels.
"""

from __future__ import annotations

import re

# Caption contenteditable inside the composer dialog, anchored on the
# "What's new?" placeholder.
WHATS_NEW_TEXTBOX_RE = re.compile(r"what's new", re.I)

# First media-row icon that opens the file picker.
ATTACH_MEDIA_BTN_RE = re.compile(
    r"^(attach media|add media|attach image|attach photo)$", re.I
)

# 'Schedule…' entry in the 3-dots more-options menu. Threads renders the
# U+2026 ellipsis ("Schedule…") but some builds use three dots ("Schedule...").
# The MENUITEM variant makes the ellipsis optional (the role-name computation
# occasionally drops it); the TEXT variant — used for the get_by_text
# confirmation / fallback — keeps it required so it can't match the final
# "Schedule" action button.
SCHEDULE_MENUITEM_RE = re.compile(r"^schedule(…|\.\.\.)?$", re.I)
SCHEDULE_TEXT_RE = re.compile(r"^schedule(…|\.\.\.)$", re.I)

# Calendar 'Next month' chevron.
NEXT_MONTH_BTN_RE = re.compile(r"^next month$", re.I)

# Calendar popup's bottom-right 'Done'.
DONE_BTN_RE = re.compile(r"^done$", re.I)

# Composer's primary action once a schedule is attached — reads "Schedule"
# instead of "Post".
FINAL_SCHEDULE_BTN_RE = re.compile(r"^schedule$", re.I)

# Cancel / Discard affordances used to back out of the composer. Tried in
# order; the discard-confirmation pops up after Cancel.
CANCEL_DISCARD_BTN_RES = (
    re.compile(r"^cancel$", re.I),
    re.compile(r"^discard$", re.I),
)

# Standalone 'Discard' on the post-Cancel confirmation prompt.
DISCARD_BTN_RE = re.compile(r"^discard$", re.I)


# ---------- Localized calendar strings ----------

# The calendar month/year header renders English month names. Centralized here
# alongside the labels so a future locale variant lives in one place.
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def calendar_header(target) -> str:
    """Return the ``<Month> <Year>`` header string for the target month."""
    return f"{MONTH_NAMES[target.month - 1]} {target.year}"


__all__ = [
    "WHATS_NEW_TEXTBOX_RE",
    "ATTACH_MEDIA_BTN_RE",
    "SCHEDULE_MENUITEM_RE",
    "SCHEDULE_TEXT_RE",
    "NEXT_MONTH_BTN_RE",
    "DONE_BTN_RE",
    "FINAL_SCHEDULE_BTN_RE",
    "CANCEL_DISCARD_BTN_RES",
    "DISCARD_BTN_RE",
    "MONTH_NAMES",
    "calendar_header",
]
