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

The module also owns the one *structural* anchor both LI drivers share —
``DIALOG_SEL``, the modal container every composer helper scopes to. Same
rationale as the labels: LinkedIn changes it, and it must change in one place.
"""

from __future__ import annotations

import re
from datetime import date


# ---------- Modal container ----------

# Every composer/editor helper scopes its search to the open modal so the feed
# behind it (carousel arrows with aria-label="Next", hidden video.js dialogs,
# a rogue caption editor) can't win the match.
#
# LinkedIn's 2026 composer rebuild swapped the ARIA-annotated
# ``<div role="dialog">`` for a **native ``<dialog>`` element carrying no
# ``role`` attribute** (issue #178). A native ``<dialog>`` has an *implicit*
# ARIA dialog role, so screen readers are unaffected — but ``[role="dialog"]``
# is an attribute selector and stopped matching, so every scoped helper
# resolved zero elements and timed out with the target plainly visible on the
# failure screenshot. Live DOM probe: the open editor is ``<dialog open>``
# whose ancestor chain carries no ``role`` at all, while the only surviving
# ``[role="dialog"]`` nodes are hidden ``vjs-modal-dialog`` leftovers in the
# feed. Both forms are matched so the drivers survive a rollback or an A/B.
#
# ``dialog[open]`` (not bare ``dialog``): LinkedIn keeps several closed
# ``<dialog>`` elements mounted, and an unqualified match would let a hidden
# one win ``.first``.
#
# IMPORTANT: this is a comma-separated selector *list*. Never interpolate it
# into a larger selector — ``f'{DIALOG_SEL} button'`` parses as
# ``[role="dialog"]`` OR ``dialog[open] button``, silently matching the hidden
# video.js modals. Chain off the locator instead: ``_dialog(page).locator(…)``.
DIALOG_SEL = '[role="dialog"], dialog[open]'


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

# The composer's secondary content types sit behind a '+' toggle. The 2026
# rebuild renamed it from 'More' / 'Más' to 'Expand content types'; the older
# names stay in the alternation so a rollback keeps working.
EXPAND_CONTENT_TYPES_RE = re.compile(
    r"expand content types|ampliar tipos de contenido|"
    r"más tipos de contenido|^(?:more|más)$",
    re.I,
)

# Document affordance revealed by the toggle above. In the rebuilt composer it
# is an ``<a aria-label="Document">`` — a *link*, not a button — so call sites
# must not anchor on ``role="button"``. Pre-rebuild it read 'Add a document'.
DOCUMENT_BTN_RE = re.compile(
    r"^(?:document|documento)$|"
    r"add a document|añadir un documento|agregar un documento",
    re.I,
)

CHOOSE_FILE_BTN_RE = re.compile(
    r"choose file|elegir archivo|elegir un archivo|seleccionar archivo",
    re.I,
)

# 'Done' closes a sub-dialog (document title, etc.). ES variants vary across
# LinkedIn builds — both 'Listo' and 'Hecho' have been observed.
#
# Matched by VISIBLE TEXT, not by accessible name. This used to be
# ``DONE_BTN_RE`` + ``get_by_role("button", name=…)``, which stopped matching
# in 2026-08 when LinkedIn re-rendered the control as a role-less ``<a>``
# whose label sits in nested ``<span>``s (issue #237 — the same pattern as the
# feed share-box in #140). An ``<a>`` carrying neither ``href`` nor ``role``
# has no implicit ARIA button role, so the role anchor could not match it at
# all: it found nothing for 177 consecutive polls while the dialog sat plainly
# ready on screen, and the caller reported the PDF as still processing.
DONE_TEXTS = ("Done", "Listo", "Hecho")

# Matches the Done control by visible text, as ``<a>`` or ``<button>`` so it
# survives either rendering, and **only when visible**.
#
# The ``:visible`` filter is load-bearing, not defensive: the one element in
# the whole document whose own text is exactly "Done" is video.js's hidden
# caption-settings button (``button.vjs-done-button`` inside
# ``div.vjs-modal-dialog.vjs-hidden[role="dialog"]``), a leftover in the feed
# that ``DIALOG_SEL``'s ``[role="dialog"]`` branch happily matches — see that
# constant's docstring. ``get_by_role`` used to exclude it for free by skipping
# hidden nodes; a text anchor does not, so the filter has to be explicit.
DONE_CTRL_SEL = ", ".join(
    f'{tag}:has-text("{word}"):visible'
    for word in DONE_TEXTS
    for tag in ("a", "button")
)


# ---------- Schedule dialog ----------

# The composer's clock affordance, which opens the Schedule dialog. It used to
# be a button with aria-label 'Schedule post'; the rebuild made it an
# ``<a aria-haspopup="dialog">`` whose accessible name is a *dynamic counter*
# ("Scheduled (2)" — it grows as posts are queued), so neither a fixed name nor
# a role anchor survives. It is matched structurally instead, by the LinkedIn
# icon id it wraps: ``svg#clock-medium`` is part of LI's icon system, so it is
# both locale-proof and counter-proof.
SCHEDULE_CLOCK_SEL = (
    'a:has(svg#clock-medium), '
    'button:has(svg#clock-medium), '
    '[role="button"]:has(svg#clock-medium)'
)

# Inside the Schedule dialog, LinkedIn ships stable ``data-testid`` hooks —
# preferred over any copy string because they survive every locale.
DATE_INPUT_SEL = 'input[data-testid="date-picker-input"]'
TIME_INPUT_SEL = 'input[data-testid="time-picker-input"]'
TIME_MENU_SEL = '[data-testid="time-picker-menu"]'

# LinkedIn's own confirmation line under the two inputs — e.g. "Posting at
# Tue, Jul 28, 6:30 AM". It renders ONLY when both fields parse, which makes it
# the authoritative oracle for "did the date/time actually take?" (the raw
# input value is not: it happily holds an unparsed string). The id is a
# generated component ref, so match on its stable prefix.
SCHEDULE_SUMMARY_SEL = '[id^="schedulePostDateTimeLabel"]'

# Primary action of the Schedule dialog — 'Next' pre-rebuild, 'Confirm' now.
CONFIRM_BTN_RE = re.compile(r"^(?:confirm|confirmar|next|siguiente)$", re.I)

# Final primary action in the composer once a schedule is attached — has
# accessible name exactly 'Schedule' (or 'Programar'). Different from the
# clock-icon aria-label above, which we deliberately do NOT match here.
FINAL_SCHEDULE_BTN_RE = re.compile(r"^(?:schedule|programar)$", re.I)

# 'Discard' on the save-as-draft prompt.
DISCARD_BTN_RE = re.compile(r"^(?:discard|descartar)$", re.I)

# LinkedIn's own toast after a successful schedule — "Post scheduled. View
# scheduled posts". This is the only *positive* confirmation the UI gives; the
# composer disappearing is merely circumstantial, and on a slow row it is not
# observable inside any reasonable budget (issue #178). Matched page-wide by
# text rather than scoped to a toast container: the container's class names are
# hashed and were never probed, whereas the copy is stable and the sibling
# ``_UPLOAD_COMPLETE_TEXT_RE`` in ``linkedin_composer`` already matches this way.
POST_SCHEDULED_TOAST_RE = re.compile(
    r"post scheduled|publicaci[óo]n programada|post programado",
    re.I,
)


# ---------- Localized date / time strings ----------

# LinkedIn renders the date input and the "Posting at …" summary in the
# account's UI language. Python's ``strftime`` follows the process locale, not
# LinkedIn's locale, so we hand-roll the Spanish names rather than mutating
# the process locale globally (which would ripple into logging and timestamps).

_ES_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def schedule_date_candidates(target: date) -> tuple[str, ...]:
    """Return every string form of ``target`` worth typing into the date input.

    The rebuilt Schedule dialog has a free-text date field whose accepted
    format follows the account's locale: en-US wants ``M/D/YYYY``, es-ES and
    en-GB want ``D/M/YYYY``. Rather than detect the locale — which LinkedIn
    exposes nowhere reliable — the caller tries these in order and keeps the
    first one LinkedIn confirms via ``SCHEDULE_SUMMARY_SEL``.

    Order matters only as an optimization; correctness comes from the caller's
    verification, which is what disambiguates a date like 8/7 that parses
    (differently) under both orderings.
    """
    return (
        f"{target.month}/{target.day}/{target.year}",
        f"{target.day}/{target.month}/{target.year}",
        f"{target.day:02d}/{target.month:02d}/{target.year}",
        target.strftime("%Y-%m-%d"),
    )


def month_token_candidates(target: date) -> tuple[str, ...]:
    """Month names LinkedIn might use in the "Posting at …" summary.

    Full and abbreviated, EN and ES — the summary abbreviates ("Jul"), and the
    caller matches any of these to prove LinkedIn parsed the month it meant.
    """
    es_full = _ES_MONTHS[target.month - 1]
    return (
        target.strftime("%B"),
        target.strftime("%b"),
        es_full,
        es_full[:3],
    )


def time_picker_candidates(hour: int, minute: int) -> tuple[str, ...]:
    """Return every time-picker entry text LinkedIn might render.

    English uses 12-hour AM/PM (``"6:30 AM"``). Spanish builds use 24-hour
    (``"06:30"``) or 12-hour with a Spanish AM/PM marker (``"6:30 a. m."``);
    we return all observed forms so the caller can ``:has-text`` against any.
    Live probe of the rebuilt picker: the menu holds 96 fifteen-minute slots
    rendered as ``6:30 AM`` on an English account.
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
    "DIALOG_SEL",
    "PHOTO_TEXT_RE",
    "VIDEO_TEXT_RE",
    "START_POST_TEXT_RE",
    "ALT_TEXT_BTN_RE",
    "ADD_BTN_RE",
    "EXPAND_CONTENT_TYPES_RE",
    "DOCUMENT_BTN_RE",
    "CHOOSE_FILE_BTN_RE",
    "DONE_CTRL_SEL",
    "DONE_TEXTS",
    "SCHEDULE_CLOCK_SEL",
    "DATE_INPUT_SEL",
    "TIME_INPUT_SEL",
    "TIME_MENU_SEL",
    "SCHEDULE_SUMMARY_SEL",
    "CONFIRM_BTN_RE",
    "FINAL_SCHEDULE_BTN_RE",
    "DISCARD_BTN_RE",
    "POST_SCHEDULED_TOAST_RE",
    "schedule_date_candidates",
    "month_token_candidates",
    "time_picker_candidates",
]
