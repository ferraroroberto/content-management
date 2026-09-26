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

# Threads renders the placeholder with a typographic apostrophe ("What’s new?",
# U+2019) since 2026-09; older builds used the ASCII one. Accept both — a
# straight-quote-only selector matched nothing and failed every row (#315).
_APOS = "['’]"

# The profile feed's inline composer row that opens the New thread dialog.
# Its accessible name is an aria-label, not the placeholder it displays;
# "Create new thread" is the pre-2026-09 variant.
COMPOSE_ENTRY_BTN_RE = re.compile(
    r"^(empty text field\. type to compose a new post\.?|create new thread)$",
    re.I,
)

# Visible placeholder text on that composer row (and again inside the dialog).
WHATS_NEW_PLACEHOLDER_RE = re.compile(rf"what{_APOS}s new\?", re.I)

# Caption contenteditable inside the composer dialog. Its accessible name is
# the same aria-label as the profile row; older builds named it after the
# placeholder.
WHATS_NEW_TEXTBOX_RE = re.compile(
    rf"what{_APOS}s new|type to compose a new post", re.I
)

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
    "COMPOSE_ENTRY_BTN_RE",
    "WHATS_NEW_PLACEHOLDER_RE",
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
