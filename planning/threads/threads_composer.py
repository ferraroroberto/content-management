"""Threads composer helpers shared between the photo scheduler and the video
driver.

This module owns the New-thread composer steps that ``schedule_threads_posts``
(the photo/CLI scheduler) and ``planning/videos/videos_threads.py`` both drive
identically — open the composer, type the caption, attach media, open the
3-dots menu, walk the Schedule calendar, click the final Schedule action, wait
for the composer to close, and best-effort cancel it. Living here (instead of
inside the scheduler module) means a rename or signature change to any of
these is a visible public-API change instead of an invisible break of the
video path, which used to import them as ``_private`` names straight out of
``schedule_threads_posts`` (issue #324).

All are pure Playwright drivers; nothing in here is route-specific (the photo
vs. video difference is only which file gets uploaded and what happens after
the calendar is set).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from playwright.sync_api import Page

from planning.threads.threads_labels import (
    ATTACH_MEDIA_BTN_RE,
    CANCEL_DISCARD_BTN_RES,
    COMPOSE_ENTRY_BTN_RE,
    DONE_BTN_RE,
    FINAL_SCHEDULE_BTN_RE,
    NEXT_MONTH_BTN_RE,
    SCHEDULE_MENUITEM_RE,
    SCHEDULE_TEXT_RE,
    WHATS_NEW_PLACEHOLDER_RE,
    WHATS_NEW_TEXTBOX_RE,
    calendar_header,
)
from planning._waits import CLICK_TIMEOUT_MS, click_until_effect, wait_for_first_ready

logger = logging.getLogger("threads_composer")


def open_composer(page: Page) -> None:
    """Click the inline 'What's new?' on the profile to open the New thread
    modal. The same placeholder text exists inside the modal, so disambiguate
    by checking the modal's heading after opening.

    The modal is only accepted once its **caption box** is on screen, not
    merely its ``[role="dialog"]`` shell. Those two mount separately, and
    treating the shell as "open" is what let ``type_caption`` run against a
    still-spinning modal and fail the first row of a batch while the six after
    it succeeded (issue #235).
    """
    click_until_effect(
        page,
        [
            # Profile-row click target. Several variants observed across
            # Threads builds — tried in order on every round. The placeholder
            # switched to a curly apostrophe in 2026-09 (#315), so match it
            # through the labels regex, never a straight-quote literal.
            ("aria compose-entry",
             page.get_by_role("button", name=COMPOSE_ENTRY_BTN_RE)),
            ("placeholder text", page.get_by_text(WHATS_NEW_PLACEHOLDER_RE)),
            ("role=button placeholder",
             page.locator('div[role="button"]').filter(
                 has_text=WHATS_NEW_PLACEHOLDER_RE
             )),
        ],
        effect=page.locator(
            '[role="dialog"] div[contenteditable="true"], '
            '[role="dialog"] [role="textbox"]'
        ),
        label="Threads composer modal",
    )


def type_caption(page: Page, caption: str) -> None:
    """Type into the composer's What's-new contenteditable inside the dialog."""
    if not caption:
        return
    dialog = page.locator('[role="dialog"]').last
    # The textarea inside the dialog is a contenteditable, anchored by the
    # same "What's new?" placeholder. Anchor by role.
    #
    # Bounded wait rather than a bare count(): open_composer now holds until
    # this box exists, so reaching here and finding nothing should be rare —
    # but the contenteditable is also re-created when Threads swaps the
    # composer between reply/thread modes, so the wait stays as the second
    # line of defence (issue #235).
    ta = wait_for_first_ready(
        page,
        [
            ("placeholder textbox", dialog.get_by_role("textbox", name=WHATS_NEW_TEXTBOX_RE)),
            ("contenteditable", dialog.locator('div[contenteditable="true"]')),
            ("role=textbox", dialog.locator('[role="textbox"]')),
        ],
        label="Threads caption textbox",
    )
    ta.click(timeout=CLICK_TIMEOUT_MS)
    page.wait_for_timeout(150)
    page.keyboard.type(caption, delay=4)
    page.wait_for_timeout(400)
    logger.debug("📝 Caption typed (%d chars).", len(caption))


def upload_image(page: Page, path: Path) -> None:
    """The first icon in the media row (image, GIF, emoji, poll, ...) opens
    an OS file picker. Wrap the click in expect_file_chooser. Some Threads
    builds also pre-mount an input[type=file] inside the dialog; try that
    first as a fast path.
    """
    dialog = page.locator('[role="dialog"]').last

    # Fast path: any pre-mounted file input inside the dialog.
    try:
        inp = dialog.locator('input[type="file"]').first
        if inp.count():
            inp.set_input_files(str(path))
            page.wait_for_timeout(800)
            logger.debug("📤 Uploaded %s via existing input[type=file].", path.name)
        else:
            raise RuntimeError("no input")
    except Exception:
        # Click the first media icon → intercept the FileChooser.
        media_btn_candidates = [
            dialog.get_by_role("button", name=ATTACH_MEDIA_BTN_RE).first,
            dialog.locator('[aria-label="Attach media" i]').first,
            dialog.locator('[aria-label*="image" i][role="button"]').first,
            # Last-resort positional: the first <svg>-containing button in
            # the media-icons row at the bottom of the dialog.
            dialog.locator('div[role="button"]:has(svg)').first,
        ]
        clicked = False
        for btn in media_btn_candidates:
            try:
                if btn.count():
                    with page.expect_file_chooser(timeout=12000) as fc:
                        btn.click(timeout=5000)
                    fc.value.set_files(str(path))
                    clicked = True
                    logger.debug("📤 Uploaded %s via FileChooser.", path.name)
                    break
            except Exception:
                continue
        if not clicked:
            raise RuntimeError("Could not click any media-attach button.")

    # Wait for the image preview to render inside the dialog.
    for _ in range(40):
        if (
            dialog.locator(
                'img[src^="blob:"], img[alt*="attached" i], '
                'div[aria-label*="image preview" i]'
            ).count()
            > 0
        ):
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(700)


_FIND_HEADER_3DOTS_JS = r"""
() => {
    const dlg = document.querySelector('[role="dialog"]');
    if (!dlg) return null;
    const dlgRect = dlg.getBoundingClientRect();
    // Find icon-only buttons in the dialog's header band (top 80px),
    // exclude the one whose text is 'Cancel'. Pick the rightmost.
    const candidates = [...dlg.querySelectorAll('[role="button"]')]
        .filter(b => {
            const r = b.getBoundingClientRect();
            const inHeader = r.top - dlgRect.top < 80;
            const text = (b.innerText || '').trim();
            const hasSvg = !!b.querySelector('svg');
            return inHeader && hasSvg && text !== 'Cancel' && text === '';
        });
    if (!candidates.length) return null;
    // Pick rightmost.
    candidates.sort((a, b) => b.getBoundingClientRect().left - a.getBoundingClientRect().left);
    candidates[0].click();
    return true;
}
"""


def open_three_dots_menu(page: Page) -> None:
    """Click the 3-dots button at the top-right of the dialog header.

    Buttons in the header have NO aria-label, NO text, and no stable
    testid. The probe confirms exactly two icon-only buttons sit in the
    header band (y<80 from dialog top); the rightmost is the 3-dots /
    more-options menu, the one next to it is the drafts icon. Use a JS
    positional pick.
    """
    try:
        clicked = bool(page.evaluate(_FIND_HEADER_3DOTS_JS))
    except Exception as err:
        raise RuntimeError(f"3-dots JS picker failed: {err}")
    if not clicked:
        raise RuntimeError("No icon-only buttons in the dialog header to click.")
    page.wait_for_timeout(600)
    # Confirm a Schedule menuitem appeared.
    for _ in range(20):
        if page.get_by_text(SCHEDULE_TEXT_RE).count():
            logger.debug("⋯ Opened more-options menu.")
            return
        page.wait_for_timeout(150)
    raise RuntimeError("Clicked the 3-dots, but the Schedule menuitem never appeared.")


def click_schedule_menuitem(page: Page) -> None:
    """Click 'Schedule…' from the 3-dots menu (Threads uses U+2026 ellipsis)."""
    candidates = [
        lambda: page.get_by_role("menuitem", name=SCHEDULE_MENUITEM_RE),
        lambda: page.get_by_text(SCHEDULE_TEXT_RE),
    ]
    for find in candidates:
        try:
            loc = find().first
            if loc.count():
                loc.click(timeout=5000)
                page.wait_for_timeout(800)
                logger.debug("📅 Clicked Schedule… menuitem.")
                return
        except Exception:
            continue
    raise RuntimeError("Could not click 'Schedule…' menuitem.")


def navigate_calendar_month(page: Page, target: date) -> None:
    """Click the calendar's '>' until the visible header is `<Month> <Year>`."""
    target_header = calendar_header(target)
    for _ in range(36):  # 3 years forward max
        try:
            if page.get_by_text(target_header, exact=True).count():
                return
        except Exception:
            pass
        next_btn_candidates = [
            page.get_by_role("button", name=NEXT_MONTH_BTN_RE).first,
            page.locator('[aria-label="Next month" i]').first,
            page.locator('[aria-label="Next" i]').first,
        ]
        clicked = False
        for nb in next_btn_candidates:
            try:
                if nb.count():
                    nb.click(timeout=2500)
                    page.wait_for_timeout(250)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            break
    raise RuntimeError(f"Could not navigate calendar to {target_header}.")


_CLICK_CAL_DAY_JS = r"""
(args) => {
    const headerText = args.headerText;
    const dayText = args.dayText;
    const isToday = args.isToday;
    // Verify the right month is showing (failsafe).
    const headerSeen = [...document.querySelectorAll('*')]
        .some(el => (el.innerText||'').trim() === headerText);
    if (!headerSeen) return {ok: false, why: 'header ' + headerText + ' not visible'};

    // The day digit lives in a leaf SPAN deep inside the cell DOM. The
    // CLICKABLE element is its nearest [role="gridcell"] ancestor (size
    // ~28x28, with an attached onclick handler — confirmed via probe). The
    // previous "click the span" approach was inert: the calendar's React
    // handler is bound to the gridcell, not to its descendants, and click
    // events did not bubble to it. Locate by walking from the span up to the
    // first gridcell.
    const spans = [...document.querySelectorAll('span')]
        .filter(s => s.children.length === 0 && (s.textContent||'').trim() === dayText);
    if (!spans.length) {
        return {ok: false, why: 'no leaf span with text ' + dayText};
    }
    function lightness(el) {
        try {
            const c = window.getComputedStyle(el).color;
            const m = c.match(/(\d+(?:\.\d+)?)/g);
            if (!m) return 0;
            return (parseFloat(m[0]) + parseFloat(m[1]) + parseFloat(m[2])) / 3;
        } catch (e) { return 0; }
    }
    const cells = [];
    for (const sp of spans) {
        let cur = sp;
        for (let d = 0; d < 8 && cur; d++) {
            if (cur.getAttribute && cur.getAttribute('role') === 'gridcell') {
                cells.push({cell: cur, spanLightness: lightness(sp)});
                break;
            }
            cur = cur.parentElement;
        }
    }
    if (!cells.length) {
        return {ok: false, why: 'no gridcell ancestor for ' + dayText};
    }
    // Color semantics in the Threads calendar:
    //   today           = WHITE text (rgb 255,255,255) inside a dark-pill bg → lightness ≈ 255
    //   current month   = BLACK text (rgb 0,0,0)                               → lightness ≈ 0
    //   prev/next month = LIGHT grey text                                     → lightness ≈ 150–200
    // When the target IS today, the current-month cell for that digit renders
    // WHITE (not black) — so the only candidates are today's white cell and a
    // same-digit grey overflow cell, and "pick lowest" would wrongly grab the
    // overflow cell. Flip direction based on isToday instead of always
    // assuming black is the answer.
    if (isToday) {
        cells.sort((a, b) => b.spanLightness - a.spanLightness);
    } else {
        cells.sort((a, b) => a.spanLightness - b.spanLightness);
    }
    cells[0].cell.click();
    return {
        ok: true,
        n_cells: cells.length,
        picked_lightness: cells[0].spanLightness,
        all_lightness: cells.map(c => c.spanLightness),
    };
}
"""


def click_calendar_day(page: Page, target: date) -> None:
    """Click the day-cell in the open calendar popup.

    Calendar days are `<div role="gridcell">` wrappers whose accessible name
    is empty; the digit lives in nested ``<span>``s. Cells from the previous
    and next months are also present but greyed out. JS disambiguates
    same-digit cells by text lightness: current-month-non-today is black
    (lowest), the overflow cell is grey (middle), and today is white
    (highest) — so it picks the *lowest* lightness cell normally, but the
    *highest* when ``target`` is today (its current-month cell renders white,
    not black, leaving no black candidate to prefer over the grey overflow
    cell).
    """
    header_text = calendar_header(target)
    day_text = str(target.day)
    is_today = target == date.today()
    try:
        res = page.evaluate(
            _CLICK_CAL_DAY_JS,
            {"headerText": header_text, "dayText": day_text, "isToday": is_today},
        )
    except Exception as err:
        raise RuntimeError(f"Calendar day JS picker failed: {err}")
    if not res or not res.get("ok"):
        raise RuntimeError(f"Could not click calendar day {day_text}: {res}")
    page.wait_for_timeout(300)
    logger.debug("📅 Clicked day %s in calendar.", day_text)


def set_calendar_time(page: Page, hour: int, minute: int) -> None:
    """Set the calendar's time field. Confirmed via probe: two separate
    inputs with placeholders ``hh`` and ``mm``, 24-hour, zero-padded values
    (e.g. ``value="12"`` ``value="00"`` for noon).
    """
    hh = page.locator('input[placeholder="hh"]').first
    mm = page.locator('input[placeholder="mm"]').first
    if not hh.count() or not mm.count():
        raise RuntimeError("Calendar time inputs (hh / mm) not found.")
    for inp, value, label in ((hh, f"{hour:02d}", "hh"), (mm, f"{minute:02d}", "mm")):
        try:
            inp.click(timeout=2500)
            page.keyboard.press("Control+A")
            page.keyboard.press("Delete")
            page.keyboard.type(value, delay=20)
            page.wait_for_timeout(150)
            try:
                got = inp.input_value(timeout=1000) or ""
            except Exception:
                got = ""
            logger.debug("⏰ %s ← %s (read-back %r)", label, value, got)
        except Exception as err:
            raise RuntimeError(f"Could not set time {label}={value}: {err}")
    # Blur so the value commits before Done is clicked.
    page.keyboard.press("Tab")
    page.wait_for_timeout(250)


def click_calendar_done(page: Page) -> None:
    """Click the calendar popup's bottom-right 'Done'."""
    candidates = [
        lambda: page.get_by_role("button", name=DONE_BTN_RE).first,
        lambda: page.get_by_text(DONE_BTN_RE).first,
    ]
    for find in candidates:
        try:
            loc = find()
            if loc.count():
                loc.click(timeout=4000)
                page.wait_for_timeout(700)
                logger.debug("✅ Calendar 'Done' clicked.")
                return
        except Exception:
            continue
    raise RuntimeError("Could not click 'Done' in calendar popup.")


def click_final_schedule_action(page: Page) -> None:
    """After Done, the composer's bottom-right 'Post' is replaced by
    'Schedule'. Click it."""
    dialog = page.locator('[role="dialog"]').last
    candidates = [
        lambda: dialog.get_by_role("button", name=FINAL_SCHEDULE_BTN_RE).first,
        lambda: page.get_by_role("button", name=FINAL_SCHEDULE_BTN_RE).last,
    ]
    for find in candidates:
        try:
            loc = find()
            if loc.count():
                loc.click(timeout=6000)
                page.wait_for_timeout(900)
                logger.debug("🚀 Final Schedule action clicked.")
                return
        except Exception:
            continue
    raise RuntimeError("Could not click the final 'Schedule' action.")


def wait_composer_closes(page: Page, timeout_ms: int = 20000) -> bool:
    """Wait until the New-thread dialog disappears.

    Detect ONLY by the absence of ``[role="dialog"]`` — the literal text
    "New thread" also appears in the side-nav and would never disappear.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline:
        try:
            if page.locator('[role="dialog"]').count() == 0:
                return True
        except Exception:
            pass
        page.wait_for_timeout(400)
    return False


def cancel_composer(page: Page) -> None:
    """Best-effort: cancel any open Threads composer."""
    for name_re in CANCEL_DISCARD_BTN_RES:
        try:
            btn = page.get_by_role("button", name=name_re)
            if btn.count():
                btn.first.click(timeout=2000)
                page.wait_for_timeout(400)
                # The discard-confirmation pops up after Cancel.
                continue
        except Exception:
            pass


__all__ = [
    "open_composer",
    "type_caption",
    "upload_image",
    "open_three_dots_menu",
    "click_schedule_menuitem",
    "navigate_calendar_month",
    "click_calendar_day",
    "set_calendar_time",
    "click_calendar_done",
    "click_final_schedule_action",
    "wait_composer_closes",
    "cancel_composer",
]
