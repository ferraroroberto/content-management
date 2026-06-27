"""Locale-aware label registry for the LinkedIn Playwright drivers.

LinkedIn honors a per-account UI language setting that the browser locale flag
cannot override (see issue #27). When the connected LinkedIn account renders in
Spanish, every accessible-name regex written in English silently misses, and
the photo / video / carousel / schedule flows all fall over.

This module centralizes every user-facing button label and date-time string
the LI drivers depend on, expressing each one as an EN | ES regex union so the
same call site works for either rendered language. New languages can be added
by extending the alternation in one place rather than chasing the same change
across both ``schedule_linkedin_posts`` and ``videos_linkedin``.

When LinkedIn rolls out additional accessible-name variants (it sometimes
ships two within the same locale), add them here as another alternation
branch — do NOT inline a new ``re.compile`` at the call site.
"""

from __future__ import annotations

import re
from datetime import date


# ---------- Feed share box ----------

# These three affordances are matched by their VISIBLE TEXT, not by an
# accessible-name `get_by_role("button", ...)`, because LinkedIn's redesigned
# share box mounts them as a role-less ``<a tabindex=0 onclick>`` (the visible
# label lives in a child ``<p>Photo</p>``). The element has no ``role="button"``
# and no aria-label once the feed hydrates, so the old accessible-name anchor
# resolved nothing and the click timed out (issue #140 — confirmed by live DOM
# probe: ``get_by_role("button", name=…)`` on the trailing-noun anchor → 0
# matches post-hydration, while ``get_by_text(PHOTO_TEXT_RE)`` → exactly 1). The
# pre-hydration placeholder briefly *is* a ``<div role="button">``, which is why
# the failure used to read "element was detached from the DOM, retrying":
# Playwright caught the placeholder, LinkedIn swapped it for the role-less
# ``<a>``, and the accessible name never matched again. Matching the visible
# ``<p>`` text and letting the click bubble up to the ``<a onclick>`` ancestor
# works for both the placeholder and the hydrated form, and across locales.
#
# Whole-string anchors (``^…$``) so a feed post that merely *mentions* "photo"
# can't win the match. ``get_by_text`` normalizes whitespace; the ``\s*`` guards
# are belt-and-braces.
PHOTO_TEXT_RE = re.compile(r"^\s*(?:photo|foto)\s*$", re.I)

# LinkedIn Spain uses "Vídeo" (with accent); some LATAM builds use "Video".
VIDEO_TEXT_RE = re.compile(r"^\s*(?:v[ií]deo)\s*$", re.I)

# Two known EN names + two known ES names for the share box's main affordance.
START_POST_TEXT_RE = re.compile(
    r"^\s*(?:start a post|create a post|empieza una publicación|crea una publicación)\s*$",
    re.I,
)


# ---------- Photo editor ----------

ALT_TEXT_BTN_RE = re.compile(r"alternative text|texto alternativo", re.I)

# 'Add' button used INSIDE the ALT dialog and as the final close on small
# sub-dialogs. Spanish LinkedIn uses "Añadir".
ADD_BTN_RE = re.compile(r"^(?:add|añadir)$", re.I)


# ---------- Composer footer (carousel route) ----------

MORE_BTN_RE = re.compile(r"^(?:more|más)$", re.I)

ADD_DOCUMENT_BTN_RE = re.compile(
    r"add a document|añadir un documento|agregar un documento",
    re.I,
)

CHOOSE_FILE_BTN_RE = re.compile(
    r"choose file|elegir archivo|elegir un archivo|seleccionar archivo",
    re.I,
)

# 'Done' closes a sub-dialog (document title, etc.). ES variants vary across
# LinkedIn builds — both 'Listo' and 'Hecho' have been observed.
DONE_BTN_RE = re.compile(r"^(?:done|listo|hecho)$", re.I)


# ---------- Schedule dialog ----------

# aria-label on the clock icon in the composer.
SCHEDULE_POST_BTN_RE = re.compile(
    r"^(?:schedule post|programar publicación)$",
    re.I,
)

# Calendar 'Next month' chevron.
NEXT_MONTH_BTN_RE = re.compile(r"next month|mes siguiente", re.I)

# 'Next' button used inside the Schedule sub-dialog and the photo editor.
NEXT_BTN_RE = re.compile(r"^(?:next|siguiente)$", re.I)

# Final primary action in the composer once a schedule is attached — has
# accessible name exactly 'Schedule' (or 'Programar'). Different from the
# clock-icon aria-label above, which we deliberately do NOT match here.
FINAL_SCHEDULE_BTN_RE = re.compile(r"^(?:schedule|programar)$", re.I)

# 'Discard' on the save-as-draft prompt.
DISCARD_BTN_RE = re.compile(r"^(?:discard|descartar)$", re.I)


# ---------- Localized date / time strings ----------

# LinkedIn renders calendar day aria-labels and the month/year header in the
# account's UI language. Python's ``strftime`` follows the process locale, not
# LinkedIn's locale, so we hand-roll the Spanish names rather than mutating
# the process locale globally (which would ripple into logging and timestamps).

_ES_WEEKDAYS = (
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
)
_ES_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def calendar_day_aria_candidates(target: date) -> tuple[str, ...]:
    """Return every aria-label LinkedIn might use for the calendar day cell.

    English: ``"Monday, May 25, 2026."``
    Spanish: ``"lunes, 25 de mayo de 2026."`` (also seen without the trailing
    period and without the comma after the weekday on older builds).
    """
    en = f"{target.strftime('%A')}, {target.strftime('%B')} {target.day}, {target.year}."
    es_weekday = _ES_WEEKDAYS[target.weekday()]
    es_month = _ES_MONTHS[target.month - 1]
    es = f"{es_weekday}, {target.day} de {es_month} de {target.year}."
    es_no_dot = es[:-1]
    es_no_comma = es.replace(",", "", 1)
    return (en, es, es_no_dot, es_no_comma)


def calendar_day_aria_re(target: date) -> re.Pattern[str]:
    """Compile the day-aria candidates into a single case-insensitive regex.

    Used by ``get_by_role('button', name=...)`` against LinkedIn's calendar.
    Each candidate is escaped because day aria-labels contain commas and
    periods that would otherwise be regex metacharacters.
    """
    alternation = "|".join(re.escape(c) for c in calendar_day_aria_candidates(target))
    return re.compile(alternation, re.I)


def calendar_header_candidates(target: date) -> tuple[str, ...]:
    """Return every month/year header string LinkedIn might render.

    Used to detect whether the calendar is already on the target month, so we
    don't click 'Next month' once more than needed.
    """
    en = f"{target.strftime('%B')} {target.year}"
    es_month = _ES_MONTHS[target.month - 1]
    es = f"{es_month} de {target.year}"
    es_short = f"{es_month} {target.year}"
    return (en, es, es_short)


def time_picker_candidates(hour: int, minute: int) -> tuple[str, ...]:
    """Return every time-picker entry text LinkedIn might render.

    English uses 12-hour AM/PM (``"6:30 AM"``). Spanish builds use 24-hour
    (``"06:30"``) or 12-hour with a Spanish AM/PM marker (``"6:30 a. m."``);
    we return all observed forms so the caller can ``:has-text`` against any.
    """
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    en = f"{h12}:{minute:02d} {suffix}"
    es_24 = f"{hour:02d}:{minute:02d}"
    es_24_no_pad = f"{hour}:{minute:02d}"
    es_meridiem = "a. m." if hour < 12 else "p. m."
    es_12 = f"{h12}:{minute:02d} {es_meridiem}"
    return (en, es_24, es_24_no_pad, es_12)


__all__ = [
    "PHOTO_TEXT_RE",
    "VIDEO_TEXT_RE",
    "START_POST_TEXT_RE",
    "ALT_TEXT_BTN_RE",
    "ADD_BTN_RE",
    "MORE_BTN_RE",
    "ADD_DOCUMENT_BTN_RE",
    "CHOOSE_FILE_BTN_RE",
    "DONE_BTN_RE",
    "SCHEDULE_POST_BTN_RE",
    "NEXT_MONTH_BTN_RE",
    "NEXT_BTN_RE",
    "FINAL_SCHEDULE_BTN_RE",
    "DISCARD_BTN_RE",
    "calendar_day_aria_candidates",
    "calendar_day_aria_re",
    "calendar_header_candidates",
    "time_picker_candidates",
]
