"""The LinkedIn Schedule dialog's date and time setters.

``set_schedule_datetime`` types the date into the dialog's free-text field,
picks the time from its 15-minute slot menu, and verifies both against
LinkedIn's own "Posting at …" summary line. Every route (photo, POST,
carousel, video) calls it between ``linkedin_composer.open_schedule_dialog``
and ``linkedin_composer.finalize_schedule``; it is split out of
``linkedin_composer`` so that module stays under the structure ratchet's
file-size limit.

Logs to the ``linkedin_composer`` logger, like the rest of the Schedule
dialog, so a videos run that enables that logger still sees these lines.
Selectors and locale candidates live in ``linkedin_labels``.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from playwright.sync_api import Page

from planning.linkedin.linkedin_labels import (
    DATE_INPUT_SEL,
    SCHEDULE_SUMMARY_SEL,
    TIME_INPUT_SEL,
    TIME_MENU_SEL,
    month_token_candidates,
    schedule_date_candidates,
    time_picker_candidates,
)

logger = logging.getLogger("linkedin_composer")


def _schedule_summary(page: Page) -> str:
    """LinkedIn's own "Posting at Tue, Jul 28, 6:30 AM" line, or ''.

    Empty means LinkedIn has not accepted the current date+time pair — it is
    the authoritative signal, since the raw input keeps whatever string was
    typed whether or not it parsed.
    """
    try:
        loc = page.locator(SCHEDULE_SUMMARY_SEL).first
        if loc.count() == 0:
            return ""
        return (loc.inner_text(timeout=2000) or "").strip()
    except Exception:
        return ""


def _summary_matches(summary: str, target: date) -> bool:
    """True when LinkedIn's summary line describes ``target``.

    Guards the one case a non-empty summary alone would not: a date like 8/7
    parses under both M/D and D/M orderings, so "it parsed" does not mean "it
    parsed as the day we meant". Requires the day number as a standalone token
    AND a month name from either supported locale.
    """
    if not summary:
        return False
    low = summary.lower()
    if not re.search(rf"\b{target.day}\b", low):
        return False
    return any(tok.lower() in low for tok in month_token_candidates(target))


def _summary_has_time(summary: str, hour: int, minute: int) -> bool:
    """True when LinkedIn's summary line shows ``hour:minute``.

    Accepts the 12-hour form with an AM/PM marker in either locale ("6:30 AM",
    "6:30 a. m.") or a 24-hour form with no marker ("06:30", "18:30"). The
    digit guards keep 6:30 from matching 16:30, and the marker guard keeps a
    24-hour 6:30 from matching "6:30 PM".
    """
    low = summary.lower()
    marker = r"a\.?\s*m\.?" if hour < 12 else r"p\.?\s*m\.?"
    h12 = hour % 12 or 12
    if re.search(rf"(?<!\d){h12}:{minute:02d}\s*{marker}", low):
        return True
    return re.search(rf"(?<!\d)0?{hour}:{minute:02d}(?!\d)(?!\s*[ap]\.?\s*m)", low) is not None


def _wait_for_summary_match(
    page: Page,
    target: date,
    *,
    time: Optional[tuple[int, int]] = None,
    timeout_ms: int = 4000,
) -> bool:
    """Poll the summary line until it describes ``target`` (and ``time``, if
    given), or give up.

    Polled rather than slept: the summary is re-rendered asynchronously after
    the field changes, and a single fixed wait is a coin flip — a 700 ms sleep
    passed for five rows in a row and then failed on a date LinkedIn was in
    fact accepting. Polling also returns as soon as it lands, so the happy path
    is faster than the sleep it replaces.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while True:
        summary = _schedule_summary(page)
        if _summary_matches(summary, target) and (
            time is None or _summary_has_time(summary, *time)
        ):
            return True
        if page.evaluate("() => Date.now()") >= deadline:
            return False
        page.wait_for_timeout(200)


def _set_schedule_date(page: Page, target: date) -> None:
    """Type the date into the rebuilt Schedule dialog's free-text date field.

    The field's accepted format is locale-dependent (en-US ``M/D/YYYY``, es-ES
    ``D/M/YYYY``) and LinkedIn exposes the active locale nowhere reliable, so
    we try each candidate rendering and keep the first that LinkedIn itself
    confirms in the summary line. A wrong-but-parseable ordering is rejected by
    ``_summary_matches``, so this converges on the right day rather than
    silently scheduling a post three weeks off.

    ``fill()``, not ``type()``: the field normalizes keystrokes as they arrive
    (typing "6:30 AM" into the sibling time field yields "6:30AM"), and
    ``fill()`` sets the value atomically and fires the input event React wants.
    """
    di = page.locator(DATE_INPUT_SEL).first
    try:
        di.wait_for(state="visible", timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Schedule dialog has no date input: {err}")

    candidates = schedule_date_candidates(target)
    for cand in candidates:
        try:
            di.fill("")
            # Let the controlled input settle before the real value: two
            # back-to-back fills can coalesce and swallow the parse.
            page.wait_for_timeout(150)
            di.fill(cand)
        except Exception:
            continue
        if _wait_for_summary_match(page, target):
            logger.debug("📅 date accepted as %s", cand)
            return
    raise RuntimeError(
        f"Could not set Date {target:%Y-%m-%d}: LinkedIn rejected every "
        f"candidate format {candidates} (summary={_schedule_summary(page)!r})"
    )


# How long to wait, per menu opening, for the wanted slot to render.
# The menu's slot list trails the date field: opened right after a date
# change it can still show the previous date's list, which for today is only
# the slots after now (an evening run sees 7:45 PM … 11:45 PM, no morning).
# The live probe saw it refresh to the full 96 slots within ~100 ms.
TIME_SLOT_WAIT_MS = 4000

# Opening attempts before giving up — the second one re-renders the menu
# against the committed date in case the open list never refreshed.
TIME_MENU_OPENS = 2

# How long to wait for the picker to report the clicked slot as its value.
TIME_SELECTED_WAIT_MS = 2000


def _open_time_menu(page: Page):
    """Click the time field and return the visible slot menu."""
    try:
        page.locator(TIME_INPUT_SEL).first.click(timeout=10000)
        menu = page.locator(TIME_MENU_SEL).first
        menu.wait_for(state="visible", timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not open the time picker: {err}")
    return menu


def _wait_for_time_slot(page: Page, menu, candidates: tuple[str, ...]):
    """Poll ``menu`` until a slot labelled with any candidate renders, or None."""
    patterns = [re.compile(rf"^\s*{re.escape(c)}\s*$", re.I) for c in candidates]
    deadline = page.evaluate("() => Date.now()") + TIME_SLOT_WAIT_MS
    while True:
        for pattern in patterns:
            entry = menu.get_by_text(pattern)
            if entry.count() > 0:
                return entry.first
        if page.evaluate("() => Date.now()") >= deadline:
            return None
        page.wait_for_timeout(200)


def _time_menu_labels(menu) -> list[str]:
    """The slot labels the open menu currently renders, top to bottom."""
    try:
        return [ln.strip() for ln in menu.inner_text(timeout=2000).splitlines() if ln.strip()]
    except Exception:
        return []


def _describe_time_labels(labels: list[str], *, edge: int = 6) -> str:
    """Compact rendering of the menu's labels for a failure message."""
    if not labels:
        return "no slots"
    if len(labels) <= 2 * edge:
        return f"{len(labels)} slot(s): {', '.join(labels)}"
    return (f"{len(labels)} slot(s): {', '.join(labels[:edge])} … "
            f"{', '.join(labels[-edge:])}")


def _time_selected(page: Page, target_minutes: int, candidates: tuple[str, ...]) -> bool:
    """Poll until the time field holds the wanted slot; False if it never does.

    ``data-time-picker-value`` is the picker's own minutes-since-midnight value
    (6:30 AM → ``390``) — locale-proof, so it is authoritative when present.
    The displayed text is the fallback should LinkedIn drop the attribute.
    """
    ti = page.locator(TIME_INPUT_SEL).first
    wanted = {c.lower() for c in candidates}
    deadline = page.evaluate("() => Date.now()") + TIME_SELECTED_WAIT_MS
    while True:
        raw = ti.get_attribute("data-time-picker-value")
        if raw is not None:
            if raw.strip() == str(target_minutes):
                return True
        elif " ".join(ti.input_value().split()).lower() in wanted:
            return True
        if page.evaluate("() => Date.now()") >= deadline:
            return False
        page.wait_for_timeout(200)


def _set_schedule_time(page: Page, hour: int, minute: int) -> None:
    """Pick the time from the dialog's 15-minute-slot menu.

    Typed input is unreliable here — the field re-formats each keystroke and
    ends up rejecting even the exact string it displays by default — so we open
    the menu and click the slot, which is also locale-proof.

    The slot is polled for rather than looked up once (issue #390): the menu
    can open still holding the previous date's slot list. If it never shows
    up, the menu is closed (focusing the date field dismisses it; Escape would
    close the whole Schedule dialog) and reopened once. After the click the
    picker's own value must equal the wanted time, so a wrong slot is an error.
    """
    candidates = time_picker_candidates(hour, minute)
    entry = None
    labels: list[str] = []
    for attempt in range(1, TIME_MENU_OPENS + 1):
        menu = _open_time_menu(page)
        entry = _wait_for_time_slot(page, menu, candidates)
        if entry is not None:
            break
        labels = _time_menu_labels(menu)
        logger.info("ℹ️ time menu open %d/%d has no %02d:%02d slot (%s)",
                    attempt, TIME_MENU_OPENS, hour, minute, _describe_time_labels(labels))
        if attempt < TIME_MENU_OPENS:
            try:
                page.locator(DATE_INPUT_SEL).first.click(timeout=5000)
                page.wait_for_timeout(300)
            except Exception:
                pass
    if entry is None:
        raise RuntimeError(
            f"No time-picker slot matched any locale candidate {candidates} after "
            f"{TIME_MENU_OPENS} menu openings; the menu showed {_describe_time_labels(labels)}"
        )

    try:
        entry.scroll_into_view_if_needed(timeout=5000)
        entry.click(timeout=5000)
    except Exception as err:
        raise RuntimeError(f"Could not click the {hour:02d}:{minute:02d} time slot: {err}")

    if not _time_selected(page, hour * 60 + minute, candidates):
        ti = page.locator(TIME_INPUT_SEL).first
        raise RuntimeError(
            f"Time picker did not take {hour:02d}:{minute:02d}: field reads "
            f"{ti.input_value()!r} (picker value "
            f"{ti.get_attribute('data-time-picker-value')!r})"
        )


def set_schedule_datetime(page: Page, target: date, hour: int, minute: int) -> None:
    """Set Date and Time in the rebuilt Schedule dialog, then verify both.

    Date first: the time menu offers only future slots, so a stale date can
    hide the slot we want. The summary is checked for the time too: it
    re-renders a beat after the picker takes the click (issue #390).
    """
    _set_schedule_date(page, target)
    _set_schedule_time(page, hour, minute)

    if not _wait_for_summary_match(page, target, time=(hour, minute)):
        raise RuntimeError(
            f"Schedule did not stick for {target:%Y-%m-%d} {hour:02d}:{minute:02d} — "
            f"LinkedIn summary reads {_schedule_summary(page)!r}"
        )
    logger.info("🗓️ %s", _schedule_summary(page))


__all__ = ["set_schedule_datetime"]
