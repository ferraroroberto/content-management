"""Instagram (Meta planner) composer helpers shared between the photo/story
scheduler and the video driver.

This module owns the planner-calendar steps that ``schedule_instagram_posts``
(the photo/story/CLI scheduler) and ``planning/videos/videos_instagram.py``
both drive identically — dismiss the "Get Meta Verified" upsell modal,
navigate the week view to a target day, open that day's Schedule ▾ menu and
click "Schedule post" / "Schedule story" / "Create reel", fill the composer's
caption field, and best-effort cancel an open composer. Living here (instead
of inside the scheduler module) means a rename or signature change to any of
these is a visible public-API change instead of an invisible break of the
video path, which used to import them as ``_private`` names straight out of
``schedule_instagram_posts`` (issue #324).

``open_day_schedule_menu`` pulls in ``navigate_to_week`` and the menu-click
retry helper as a unit — they have no callers outside this cluster, and
splitting them across the module boundary would just relocate the same
private-import problem one file over.

All are pure Playwright drivers; nothing in here is route-specific.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from playwright.sync_api import Page

from planning.instagram.instagram_labels import (
    CLOSE_BTN_RE,
    DISCARD_LEAVE_BTN_RE,
    NEXT_WEEK_BTN_RE,
    NOT_NOW_BTN_RE,
    PREV_WEEK_BTN_RE,
    day_cell_label,
)

logger = logging.getLogger("instagram_composer")


def dismiss_meta_verified_modal(page: Page) -> bool:
    """First-load Meta planner pops a 'Get Meta Verified' upsell modal that
    intercepts every pointer event (data-surface=GeoIllustrationModal). The
    'Not now' button dismisses it. Returns True if it was dismissed.
    """
    try:
        not_now = page.get_by_role("button", name=NOT_NOW_BTN_RE)
        if not_now.count():
            not_now.first.click(timeout=4000)
            page.wait_for_timeout(500)
            logger.info("ℹ️ Dismissed 'Get Meta Verified' upsell modal.")
            return True
    except Exception:
        pass
    # Fallback: dialog-scoped Close (×) button.
    try:
        close = page.locator('[role="dialog"]').get_by_role(
            "button", name=CLOSE_BTN_RE
        )
        if close.count():
            close.first.click(timeout=2000)
            page.wait_for_timeout(500)
            logger.info("ℹ️ Closed blocking dialog via Close button.")
            return True
    except Exception:
        pass
    return False


# JS probe that locates a day-column ancestor by walking up from the day
# header's leaf text node until it finds a descendant with a "Schedule…"
# label — the day columns carry no stable id/testid of their own.
_FIND_COLUMN_JS = r"""
(label) => {
    const all = document.querySelectorAll('*');
    let header = null;
    for (const el of all) {
        if (el.children.length === 0 && (el.textContent || '').trim() === label) {
            header = el;
            break;
        }
    }
    if (!header) return null;
    let node = header;
    for (let depth = 0; depth < 25 && node; depth++) {
        if (node.querySelectorAll) {
            const candidates = node.querySelectorAll('div[role="button"], button, [aria-haspopup]');
            for (const c of candidates) {
                const txt = (c.textContent || '').trim();
                if (/^Schedule(\s|$)/i.test(txt) || txt === 'Schedule') {
                    return node;
                }
            }
        }
        node = node.parentElement;
    }
    return null;
}
"""


def navigate_to_week(page: Page, target: date, *, max_clicks: int = 10) -> None:
    """Advance the week view forward (or back) until the target day's column
    is visible. The planner's chevron buttons are labelled 'Next week' /
    'Previous week' aria-wise; the visible label is just the chevron glyph.

    Strategy: try clicking 'Next week' until the target's column appears,
    then 'Previous week' if we overshot.
    """
    label = day_cell_label(target)

    def col_present() -> bool:
        try:
            return page.evaluate(_FIND_COLUMN_JS, label) is not None
        except Exception:
            return False

    if col_present():
        return

    # Meta uses chevron-glyph text "Left" / "Right" as the button label
    # (probed via DOM); aria-label is empty.
    next_btn = page.get_by_role("button", name=NEXT_WEEK_BTN_RE)
    prev_btn = page.get_by_role("button", name=PREV_WEEK_BTN_RE)

    # Try forward first.
    for _ in range(max_clicks):
        try:
            if next_btn.count():
                next_btn.first.click(timeout=4000)
                page.wait_for_timeout(800)
            else:
                break
        except Exception:
            break
        if col_present():
            logger.debug("📅 Advanced to week containing %s", label)
            return

    # If we got here, try going back instead.
    for _ in range(max_clicks):
        try:
            if prev_btn.count():
                prev_btn.first.click(timeout=4000)
                page.wait_for_timeout(800)
            else:
                break
        except Exception:
            break
        if col_present():
            logger.debug("📅 Retreated to week containing %s", label)
            return

    logger.warning("⚠️ Could not navigate to week containing %s", label)


# The day-column Schedule ▾ control is a split-button (aria-haspopup="menu")
# whose menu — "Schedule post" / "Schedule story" / "Create reel" /
# "Schedule ad" — only opens on a JS-native element.click(). Playwright's
# mouse-event .click() leaves it closed (the same half-bound React handler that
# breaks the Add-media button, issue #28), so a Playwright click never reveals
# the menu item and the click times out. We therefore open the menu with a
# JS-native click, then select the item. Each (open + click) attempt is retried
# with an escalating click timeout in case Meta's menu is also slow to paint.
_MENU_ITEM_CLICK_TIMEOUTS_MS = (8000, 16000, 32000)  # 8/16/32s ceiling per attempt


def _click_schedule_menu_item(page: Page, schedule_btn, action: str) -> None:
    """Open the day's Schedule menu and click the ``action`` item.

    ``schedule_btn`` is the ElementHandle for the column's Schedule ▾ split-
    button. The menu is opened with a JS-native ``element.click()`` because
    Meta's split-button ignores Playwright's synthetic mouse click; the item
    selection then proceeds normally. Retried with escalating click timeouts.
    """
    item = re.compile(rf"^{re.escape(action)}$", re.I)
    last_err: Optional[Exception] = None
    for attempt, timeout in enumerate(_MENU_ITEM_CLICK_TIMEOUTS_MS, start=1):
        # (Re-)open the Schedule menu with a JS-native click — the only thing
        # that actually pops it (Playwright .click() leaves it closed).
        try:
            schedule_btn.evaluate("el => el.click()")
            page.wait_for_timeout(600)
        except Exception as err:
            last_err = err
        try:
            page.get_by_role("menuitem", name=item).first.click(timeout=timeout)
            return
        except Exception:
            try:
                page.get_by_text(item).first.click(timeout=timeout)
                return
            except Exception as err:
                last_err = err
        if attempt < len(_MENU_ITEM_CLICK_TIMEOUTS_MS):
            logger.warning(
                "⚠️ Menu item '%s' not clickable (attempt %d/%d, timeout %dms): "
                "%s — reopening menu and retrying.",
                action, attempt, len(_MENU_ITEM_CLICK_TIMEOUTS_MS), timeout,
                str(last_err).splitlines()[0][:160] if last_err else "?",
            )
    raise RuntimeError(
        f"Could not click menu item '{action}' after "
        f"{len(_MENU_ITEM_CLICK_TIMEOUTS_MS)} attempts: {last_err}"
    )


def open_day_schedule_menu(page: Page, d: date, action: str) -> None:
    """Hover the day's calendar column to reveal the bottom-right Schedule ▾
    button, then click the requested menu item.

    ``action`` ∈ {"Schedule post", "Schedule story"}.

    The menu only appears on hover (image 06/10 in the design notes), except
    for today's column where it's already visible. We find the column by
    locating the day-header text node ("Mon 18") and walking up the DOM until
    we hit an ancestor that contains a Schedule button — that ancestor IS the
    column. The hover + click then happen via the bounding-box of that
    ancestor's Schedule button.
    """
    label = day_cell_label(d)
    logger.debug("🖱  resolving column for day cell %s", label)

    # Make a small attempt to dismiss any modal that crept in between actions.
    dismiss_meta_verified_modal(page)

    # Make sure the target day's week is on-screen.
    navigate_to_week(page, d)

    col_handle = page.evaluate_handle(_FIND_COLUMN_JS, label)
    if col_handle is None:
        raise RuntimeError(f"Could not locate planner column for {label}.")
    el = col_handle.as_element()
    if el is None:
        raise RuntimeError(f"Day {label} not present in current week — wrong view?")

    # Hover the column to reveal the Schedule button (no-op for today's cell,
    # required for every other day).
    try:
        el.hover(timeout=8000)
        page.wait_for_timeout(400)
    except Exception as err:
        raise RuntimeError(f"Could not hover day column {label}: {err}")

    # Find the Schedule button inside this column. There are usually 1 or 2
    # buttons whose visible text starts with "Schedule" — pick the LAST one
    # (the menu button anchors the bottom of the column). ElementHandle uses
    # query_selector_all rather than Locator.
    candidates = el.query_selector_all(
        'div[role="button"], button, [aria-haspopup="menu"]'
    )
    schedule_btn = None
    for c in candidates:
        try:
            txt = (c.inner_text() or "").strip()
        except Exception:
            txt = ""
        if re.match(r"^schedule\b", txt, re.I) or txt.lower() == "schedule":
            schedule_btn = c
    if schedule_btn is None:
        # Some columns render the menu as a separate chevron with no text.
        for c in candidates:
            try:
                if c.get_attribute("aria-haspopup"):
                    schedule_btn = c
            except Exception:
                pass
    if schedule_btn is None:
        raise RuntimeError(f"Could not find Schedule button inside {label} column.")

    # Open the Schedule ▾ menu (JS-native click — Playwright's mouse click does
    # not pop it) and click the requested item ("Schedule post" / "Schedule
    # story"). All of that, with retries, lives in the helper.
    _click_schedule_menu_item(page, schedule_btn, action)
    page.wait_for_timeout(1000)


def fill_post_text(page: Page, caption: str) -> None:
    """Fill the Create post composer's caption text area.

    The composer's caption field is a contenteditable div (Lexical editor)
    OR a plain <textarea>. Try the textarea first; if not present, focus the
    contenteditable and type via keyboard so Lexical's React handlers run.
    """
    if not caption:
        return
    ta_candidates = page.locator('textarea, [role="textbox"][contenteditable="true"], div[contenteditable="true"]')
    n = ta_candidates.count()
    for i in range(n):
        try:
            el = ta_candidates.nth(i)
            el.click(timeout=4000)
            page.wait_for_timeout(200)
            try:
                el.fill(caption)
                logger.debug("📝 Caption filled via .fill() (element #%d)", i)
                return
            except Exception:
                page.keyboard.type(caption, delay=4)
                logger.debug("📝 Caption typed via keyboard (element #%d)", i)
                return
        except Exception:
            continue
    raise RuntimeError("Could not find any caption field to fill.")


def cancel_composer(page: Page) -> None:
    """Best-effort: cancel the open composer to return to the planner."""
    for name in ("Cancel", "Close"):
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^{re.escape(name)}$", re.I))
            if btn.count():
                btn.first.click(timeout=2000)
                page.wait_for_timeout(500)
                break
        except Exception:
            pass
    # Discard confirmation, if Meta asks.
    try:
        discard = page.get_by_role("button", name=DISCARD_LEAVE_BTN_RE)
        if discard.count():
            discard.first.click(timeout=2000)
            page.wait_for_timeout(500)
    except Exception:
        pass


__all__ = [
    "dismiss_meta_verified_modal",
    "navigate_to_week",
    "open_day_schedule_menu",
    "fill_post_text",
    "cancel_composer",
]
