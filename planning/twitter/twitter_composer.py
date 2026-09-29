"""X (Twitter) composer helpers shared between the photo scheduler and the
video driver.

This module owns the ``/home`` composer steps that ``schedule_twitter_posts``
(the photo/CLI scheduler) and ``planning/videos/videos_twitter.py`` both drive
identically — dismiss blocking modals, open the composer, type the caption,
attach media, open + fill the Schedule modal, confirm it, click the final
Schedule action, and wait for the composer to clear (or best-effort cancel
it). Living here (instead of inside the scheduler module) means a rename or
signature change to any of these is a visible public-API change instead of an
invisible break of the video path, which used to import them as ``_private``
names straight out of ``schedule_twitter_posts`` (issue #324).

All are pure Playwright drivers; nothing in here is route-specific (the photo
vs. video difference is only which file gets uploaded and what happens after
the schedule modal is set).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from planning.twitter.twitter_labels import (
    CONFIRM_BTN_RE,
    DISCARD_BTN_RES,
    DISMISS_DIALOG_BTN_RES,
    FINAL_SCHEDULE_BTN_RE,
    SCHEDULE_TOOLBAR_BTN_RE,
)
from planning._waits import CLICK_TIMEOUT_MS, click_until_effect, wait_for_first_ready

logger = logging.getLogger("twitter_composer")


def dismiss_blocking_modals(page: Page) -> bool:
    """X sometimes pops onboarding / 'try premium' modals. Scope dismissals
    to ``[role="dialog"]`` so we don't accidentally click the side-rail
    'Close' button or similar always-visible chrome.
    """
    dismissed = False
    dialog = page.locator('[role="dialog"]')
    if not dialog.count():
        return False
    for name_re in DISMISS_DIALOG_BTN_RES:
        try:
            btn = dialog.first.get_by_role("button", name=name_re)
            if btn.count():
                btn.first.click(timeout=2500)
                page.wait_for_timeout(400)
                dismissed = True
                logger.info("ℹ️ Dismissed dialog via %r button.", name_re.pattern)
                break
        except Exception:
            pass
    return dismissed


def click_compose_area(page: Page) -> None:
    """Open a clean composer modal via the side-rail 'Post' button.

    X's side-rail button has the stable testid ``SideNav_NewTweet_Button``.
    Clicking it opens a full-screen modal composer whose textarea is
    ``[data-testid="tweetTextarea_0"]`` inside ``[role="dialog"]``. Preferred
    over the inline composer because the inline one can carry stale draft
    state between sessions.

    Every candidate used to be probed with a bare ``count()``, which does not
    wait — so a row that arrived while X was still showing its splash screen
    found nothing mounted, exhausted all four probes in milliseconds and
    raised, while its siblings on the same warm page all succeeded (#235).
    ``click_until_effect`` re-resolves until the composer is genuinely on
    screen, so a cold app shell costs a few extra seconds instead of the row.
    """
    # The effect must be the textarea *inside the dialog*, never a bare
    # ``tweetTextarea_0``: X renders an inline composer on the home timeline,
    # so the looser selector is already satisfied on arrival and the modal
    # would never be opened at all.
    try:
        click_until_effect(
            page,
            [
                ("side-rail testid", page.locator('[data-testid="SideNav_NewTweet_Button"]')),
                ("side-rail anchor", page.locator('a[data-testid="SideNav_NewTweet_Button"]')),
                ("aria Post button", page.locator('[aria-label="Post" i][role="button"]')),
            ],
            effect=page.locator('[role="dialog"] [data-testid="tweetTextarea_0"]'),
            label="X compose modal",
        )
        return
    except RuntimeError as err:
        logger.warning("⚠️ Compose modal did not open (%s) — trying the inline composer.", err)

    # Fallback: the inline composer. Kept strictly behind the modal routes
    # because it can carry stale draft state between sessions.
    ta = wait_for_first_ready(
        page,
        [("inline textarea", page.locator('[data-testid="tweetTextarea_0"]'))],
        label="X inline composer",
        timeout_ms=4000,
    )
    try:
        ta.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    ta.click(timeout=CLICK_TIMEOUT_MS)
    logger.debug("📝 Clicked inline composer area (fallback).")


def type_caption(page: Page, caption: str) -> None:
    """Type into the active composer textarea (Lexical editor)."""
    if not caption:
        return
    ta = page.locator('[data-testid="tweetTextarea_0"]').first
    try:
        ta.click(timeout=4000)
        page.wait_for_timeout(150)
    except Exception:
        pass
    # Lexical ignores .fill(); type via keyboard so onChange fires.
    page.keyboard.type(caption, delay=4)
    page.wait_for_timeout(400)
    _dismiss_mention_typeahead(page)
    logger.debug("📝 Caption typed (%d chars).", len(caption))


def _dismiss_mention_typeahead(page: Page) -> None:
    """Close X's mention typeahead if it's open.

    A caption ending in an unterminated ``@handle`` leaves the typeahead open —
    the caret sits right after the handle, so nothing dismisses it. It reopens
    whenever focus returns to the textarea (e.g. after the Schedule modal
    closes), and it overlays the composer's primary Schedule button, failing
    ``click_final_schedule_action`` (issue #63 follow-on). Escape closes it
    without altering the caption (verified: "@handle" stays as literal text,
    which X still linkifies on publish).

    Guarded by the typeahead's own presence: a bare Escape on a clean composer
    can pop the discard-draft prompt. The typeahead is a body-level portal (NOT
    inside the composer dialog) with a stable id prefix ``typeaheadDropdown``,
    so we match it precisely rather than via a bare ``[role="listbox"]``.
    """
    try:
        if page.locator('[role="listbox"][id^="typeaheadDropdown"]').count() > 0:
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            logger.debug("⌨️ Dismissed mention typeahead.")
    except Exception:
        pass


def _click_through_cover(
    page: Page,
    loc,
    *,
    label: str,
    verify=None,
    timeout_ms: int = 5000,
) -> bool:
    """Click ``loc``, surviving the composer's intermittent full-cover overlay.

    After an image is attached the action toolbar relocates low in the modal
    (probed y≈810 at 1280×900) and an absolutely-positioned full-cover ``<div>``
    (``r-1p0dtai/-1d2f490/-1xcajam/-zchlnj`` ≈ top/left/bottom/right:0) shares
    that stacking context. It is normally *behind* the buttons, but X's
    hit-testing intermittently routes the pointer to the cover and Playwright
    reports the subtree "intercepts pointer events" even though the button is
    visible/enabled/stable (issue #63).

    Scroll into view, try a normal click (which polls actionability — cheap on
    a warm composer), then fall back to a JS ``el.click()`` that bypasses
    pointer-event hit-testing and reliably lands. When ``verify`` is given,
    return True only once it reports success, so a slow UI transition isn't
    mistaken for a failed click and re-clicked.
    """
    try:
        loc.scroll_into_view_if_needed(timeout=2000)
        page.wait_for_timeout(200)
    except Exception:
        pass
    try:
        loc.click(timeout=timeout_ms)
        if verify is None or verify():
            return True
    except PWTimeoutError:
        logger.info(
            "ℹ️ %s click intercepted by the cover overlay — "
            "falling back to JS click (issue #63).", label,
        )
    except Exception:
        pass
    try:
        loc.evaluate("el => el.click()")
        if verify is None or verify():
            return True
    except Exception:
        pass
    return False


def upload_image(page: Page, path: Path) -> None:
    """Upload a single image. X pre-mounts an input[type=file]
    [data-testid='fileInput']. Use it directly when present; otherwise fall
    back to expect_file_chooser around the photo toolbar button.
    """
    file_input = page.locator('input[data-testid="fileInput"]').first
    try:
        if file_input.count():
            file_input.set_input_files(str(path))
            logger.debug("📤 Uploaded %s via fileInput.", path.name)
        else:
            raise RuntimeError("no fileInput")
    except Exception:
        # Fallback: click the photo button and intercept the FileChooser.
        photo_btn = page.locator(
            'button[data-testid="fileButton"], '
            'div[role="button"][aria-label="Media" i], '
            'div[role="button"][aria-label*="photo" i]'
        ).first
        with page.expect_file_chooser(timeout=12000) as fc:
            photo_btn.click(timeout=5000)
        fc.value.set_files(str(path))
        logger.debug("📤 Uploaded %s via FileChooser.", path.name)

    # Wait for the preview thumbnail to render. X shows the attachment under
    # the textarea as a [data-testid="attachments"] or [aria-label="Image"]
    # element.
    for _ in range(40):
        if (
            page.locator(
                '[data-testid="attachments"], '
                'div[aria-label*="image" i] img[src^="blob:"], '
                'img[alt*="Image" i][src^="blob:"]'
            ).count()
            > 0
        ):
            break
        page.wait_for_timeout(250)
    page.wait_for_timeout(800)


def _schedule_modal_open(page: Page, timeout_ms: int = 2500) -> bool:
    """Poll until the Schedule modal's native date/time selects mount.

    The modal adds exactly 6 native ``<select>``s (Month/Day/Year/Hour/Minute/
    AM-PM) inside a dialog; the composer dialog itself has none, so ``>= 6``
    cleanly distinguishes "modal open" from "still on the composer".
    """
    for _ in range(max(1, timeout_ms // 150)):
        try:
            if page.locator('[role="dialog"] select').count() >= 6:
                return True
        except Exception:
            pass
        page.wait_for_timeout(150)
    return False


def click_schedule_toolbar(page: Page) -> None:
    """Open the Schedule modal via the composer's calendar-clock toolbar button.

    The button relocates low in the modal after image attach and is subject to
    the intermittent cover-overlay interception handled by ``_click_through_cover``
    (issue #63). Success is confirmed by the modal's 6 native selects mounting,
    so a slow open isn't mistaken for a failed click. The image is already
    fully attached by now (``upload_image`` waits for the preview), so the
    JS-click bypass is safe — not a blind early click.
    """
    candidates = [
        lambda: page.locator('button[data-testid="scheduleOption"]'),
        lambda: page.locator('[aria-label="Schedule post"]'),
        lambda: page.get_by_role("button", name=SCHEDULE_TOOLBAR_BTN_RE),
    ]
    for find in candidates:
        try:
            loc = find().first
        except Exception:
            continue
        if not loc.count():
            continue
        if _click_through_cover(
            page, loc, label="Schedule toolbar",
            verify=lambda: _schedule_modal_open(page),
        ):
            logger.debug("📅 Opened Schedule modal.")
            return
    raise RuntimeError("Could not open the Schedule modal (toolbar button).")


def set_schedule_modal(page: Page, target: date, hour: int, minute: int) -> None:
    """Fill the Schedule modal's Month/Day/Year + Hour/Minute/AM-PM dropdowns.

    X uses native ``<select>`` elements (confirmed via DOM probe: e.g.
    ``<option value="5">May</option>``). Field order in the modal is fixed:
    Month, Day, Year, Hour, Minute, AM/PM. Use the dialog-scoped select list
    by index so we're robust against missing aria-labels.
    """
    h12 = hour % 12 or 12
    mer = "AM" if hour < 12 else "PM"

    # The first 6 native selects inside the open dialog are, in order:
    # Month (value=1..12), Day (value=1..31), Year, Hour (value=1..12),
    # Minute (value=0..59), AM/PM (value="AM"/"PM").
    fields = [
        ("Month", str(target.month)),       # value = 1..12
        ("Day", str(target.day)),
        ("Year", str(target.year)),
        ("Hour", str(h12)),
        ("Minute", str(minute)),            # value is unpadded (0..59) on X
        ("AM/PM", mer),
    ]

    dialog = page.locator('[role="dialog"]').last
    selects = dialog.locator('select')
    nsel = selects.count()
    if nsel < 6:
        raise RuntimeError(f"Schedule modal: expected ≥6 selects, found {nsel}.")

    for idx, (label, value) in enumerate(fields):
        sel = selects.nth(idx)
        try:
            sel.select_option(value=value, timeout=4000)
            logger.debug("📅 [%d] %s ← %s", idx, label, value)
            continue
        except Exception:
            pass
        # Fallback: try by visible label (e.g. Minute may render as "00").
        try:
            label_value = value
            if label == "Minute":
                label_value = f"{int(value):02d}"
            sel.select_option(label=label_value, timeout=4000)
            logger.debug("📅 [%d] %s ← (label) %s", idx, label, label_value)
            continue
        except Exception as err:
            raise RuntimeError(f"Could not set Schedule {label}={value}: {err}")


def click_confirm_in_modal(page: Page) -> None:
    """Click the bottom-right 'Confirm' in the Schedule modal."""
    candidates = [
        lambda: page.locator('[data-testid="scheduledConfirmationPrimaryAction"]'),
        lambda: page.get_by_role("button", name=CONFIRM_BTN_RE),
    ]
    for find in candidates:
        try:
            loc = find().first
            if loc.count():
                loc.click(timeout=5000)
                page.wait_for_timeout(700)
                logger.debug("✅ Schedule modal: Confirm clicked.")
                return
        except Exception:
            continue
    raise RuntimeError("Could not click Confirm in Schedule modal.")


def click_final_schedule_action(page: Page) -> None:
    """The composer's primary action button — after Confirm in the schedule
    modal — reads 'Schedule' instead of 'Post'. Click it.

    Closing the Schedule modal returns focus to the textarea, which reopens the
    mention typeahead when the caption ends in an ``@handle`` — that dropdown
    overlays this button, so dismiss it first. The button lives in the same
    relocated toolbar as the schedule icon, so it gets the same cover-overlay
    JS-click fallback (issue #63).
    """
    _dismiss_mention_typeahead(page)
    candidates = [
        lambda: page.locator('[data-testid="tweetButton"]'),
        lambda: page.locator('button[data-testid="tweetButtonInline"]'),
        lambda: page.get_by_role("button", name=FINAL_SCHEDULE_BTN_RE),
    ]
    for find in candidates:
        try:
            loc = find().last
        except Exception:
            continue
        if not loc.count():
            continue
        if _click_through_cover(page, loc, label="Final Schedule"):
            page.wait_for_timeout(700)
            logger.debug("🚀 Final Schedule action clicked.")
            return
    raise RuntimeError("Could not click the final Schedule action button.")


def wait_composer_clears(page: Page, timeout_ms: int = 20000) -> bool:
    """After successful schedule, the inline composer reverts to empty
    'What's happening?' (no attachments, no banner). Detect by attachments
    disappearing AND the textarea being empty.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline:
        try:
            atts = page.locator('[data-testid="attachments"]').count()
            banner = page.locator('text=/^Will send on/i').count()
            if atts == 0 and banner == 0:
                # Also confirm the textarea text is empty (or the modal is closed).
                ta = page.locator('[data-testid="tweetTextarea_0"]').first
                if ta.count() == 0:
                    return True
                try:
                    txt = ta.inner_text(timeout=1000) or ""
                except Exception:
                    txt = ""
                if not txt.strip():
                    return True
        except Exception:
            pass
        page.wait_for_timeout(500)
    return False


def cancel_composer(page: Page) -> None:
    """Best-effort: close any open composer and any 'Save draft?' prompt."""
    try:
        # Composer's top-left close (X).
        close = page.locator('[data-testid="app-bar-close"], button[aria-label="Close" i]')
        if close.count():
            close.first.click(timeout=2000)
            page.wait_for_timeout(400)
    except Exception:
        pass
    # If a "Save changes?" / "Discard?" dialog appears, pick Discard.
    for name_re in DISCARD_BTN_RES:
        try:
            btn = page.get_by_role("button", name=name_re)
            if btn.count():
                btn.first.click(timeout=2000)
                page.wait_for_timeout(400)
        except Exception:
            pass


__all__ = [
    "dismiss_blocking_modals",
    "click_compose_area",
    "type_caption",
    "upload_image",
    "click_schedule_toolbar",
    "set_schedule_modal",
    "click_confirm_in_modal",
    "click_final_schedule_action",
    "wait_composer_clears",
    "cancel_composer",
]
