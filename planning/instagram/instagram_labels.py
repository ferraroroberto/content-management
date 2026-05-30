"""Accessible-name + locale label registry for the Instagram/Facebook driver.

The Meta Business planner mixes a few stable structural hooks (data-surface,
``input[aria-label="hours"]``, etc.) with many user-facing labels the driver
must match by name: the "Get Meta Verified" upsell dismissals, the week/month
calendar chevrons, the "Set date and time" toggle, the discard prompt, and the
"Add photo/video" → "Upload from computer" media path (which Meta ships in both
English and Spanish, see issues #28 / #60). Those labels are exactly what shifts
when Meta relabels a control or flips an account's locale, so they are
centralized here rather than scattered inline through ``schedule_instagram_posts``.

This mirrors ``planning/linkedin/linkedin_labels.py``: the self-healing
scheduler (issue #64) can scan and patch one small module instead of hunting an
accessible-name string buried in scheduling logic. When Meta ships a new label
or locale variant, extend the alternation here — do NOT re-inline a
``re.compile`` or ``:has-text`` selector at the call site.

Out of scope (stays inline in the driver): dynamic, argument-built name regexes
(``re.compile(rf"^{re.escape(action)}$", ...)`` for "Schedule post"/"Schedule
story", action buttons, time slots), structural CSS (``data-surface``,
``input[placeholder=…]``, ``input[type=checkbox]``), and JS DOM probes — none of
those are fixed user-facing labels.
"""

from __future__ import annotations

import re
from datetime import date


# ---------- Dialog dismissals ----------

# 'Not now' dismisses the first-load 'Get Meta Verified' upsell modal.
NOT_NOW_BTN_RE = re.compile(r"^not now$", re.I)

# Dialog-scoped Close (×) fallback for blocking modals.
CLOSE_BTN_RE = re.compile(r"^close$", re.I)

# 'Discard' / 'Leave' on the composer's unsaved-changes prompt.
DISCARD_LEAVE_BTN_RE = re.compile(r"^(discard|leave)$", re.I)


# ---------- Composer toggles / calendar nav ----------

# The post composer's 'Set date and time' toggle label (the visible text next
# to the checkbox input).
SET_DATE_TIME_TEXT_RE = re.compile(r"^set date and time$", re.I)

# Final 'Schedule' action text — also used as a scroll anchor in the composer.
SCHEDULE_TEXT_RE = re.compile(r"^schedule$", re.I)

# Calendar 'Next month' chevron (not anchored: Meta wraps the text with a glyph).
NEXT_MONTH_BTN_RE = re.compile(r"next month", re.I)

# Week-view chevrons. Meta gives these the chevron-glyph text "Right"/"Left"
# (aria-label is empty), so we match either the semantic or the glyph name.
NEXT_WEEK_BTN_RE = re.compile(r"^(next week|right)$", re.I)
PREV_WEEK_BTN_RE = re.compile(r"^(previous week|left)$", re.I)


# ---------- Media-attach path (locale EN | ES) ----------

# 'Add photo/video' affordance. Meta localizes this (issue #28 / #60), so the
# selector unions every observed EN + ES wording plus the upload-button surface.
ADD_MEDIA_BTN_SELECTOR = (
    'div[role="button"]:has-text("Add photo/video"), '
    'button:has-text("Add photo/video"), '
    'div[role="button"]:has-text("Añadir foto/vídeo"), '
    'button:has-text("Añadir foto/vídeo"), '
    'div[role="button"]:has-text("Añadir foto"), '
    'button:has-text("Añadir foto"), '
    '[data-surface*="upload_button"]'
)

# Some Meta builds open an intermediate dialog whose 'Upload from computer'
# button is what actually triggers the file chooser. EN + ES wordings (issue #28).
UPLOAD_FROM_COMPUTER_SELECTOR = (
    'div[role="dialog"] div[role="button"]:has-text("Upload from computer"), '
    'div[role="dialog"] button:has-text("Upload from computer"), '
    'div[role="dialog"] div[role="button"]:has-text("Upload"), '
    'div[role="dialog"] button:has-text("Upload"), '
    'div[role="dialog"] div[role="button"]:has-text("Subir desde el ordenador"), '
    'div[role="dialog"] button:has-text("Subir desde el ordenador"), '
    'div[role="dialog"] div[role="button"]:has-text("Subir"), '
    'div[role="dialog"] button:has-text("Subir")'
)


# ---------- Localized date / time rendering ----------

# The day-cell header in the Meta planner is e.g. "Mon 18". Windows strftime
# uses "%#d" rather than "%-d" for an unpadded day, so build the label with
# explicit indexing to stay cross-platform-safe.
_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def day_cell_label(d: date) -> str:
    """Return the planner column header text for a day, e.g. ``"Mon 18"``."""
    return f"{_WEEKDAY_ABBR[d.weekday()]} {d.day}"


def fmt_time_12h(hour: int, minute: int) -> str:
    """Render a 12-hour time slot string as Meta's time picker shows it,
    e.g. ``"6:30 AM"`` / ``"3:00 PM"``."""
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{minute:02d} {suffix}"


__all__ = [
    "NOT_NOW_BTN_RE",
    "CLOSE_BTN_RE",
    "DISCARD_LEAVE_BTN_RE",
    "SET_DATE_TIME_TEXT_RE",
    "SCHEDULE_TEXT_RE",
    "NEXT_MONTH_BTN_RE",
    "NEXT_WEEK_BTN_RE",
    "PREV_WEEK_BTN_RE",
    "ADD_MEDIA_BTN_SELECTOR",
    "UPLOAD_FROM_COMPUTER_SELECTOR",
    "day_cell_label",
    "fmt_time_12h",
]
