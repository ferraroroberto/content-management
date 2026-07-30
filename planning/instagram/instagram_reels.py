"""Instagram Reels composer driver (issue #118).

Meta's day-column ``Schedule ▾`` menu offers a dedicated **Create reel**
composer for video, entirely separate from the feed-post composer driven by
``schedule_instagram_posts.schedule_post``. Instagram rejects vertical 9:16
clips in the feed-post composer ("doesn't fit within Instagram's accepted
aspect ratio range of 4:5 to 16:9", Schedule never enables) and duplicates a
single video ``set_files`` call into two tiles there; the Reels composer
accepts 9:16 and attaches one tile per file, so video always goes through
here instead.

It is a full-page wizard at ``/latest/reels_composer/`` (NOT a modal):
``Add Video`` → fill caption → footer "Next" (enables once the upload hits
100%) → "Scheduling options" → pick "Schedule" (reveals the mm/dd/yyyy +
hours/minutes/meridiem inputs, set the same way the feed composer's
``_set_all_visible_date_time`` does) → footer "Schedule". Selectors verified
live, 2026-06.

Split out of ``schedule_instagram_posts.py`` (issue #198): the Reels wizard
is a self-contained flow with its own dozen-function lifecycle that shares
nothing with the feed-post/story driver beyond the page session and a couple
of low-level helpers (``_fill_input``, the module logger) reused from there.
Consumed by ``planning/videos/videos_instagram.py`` (the weekly-video
orchestrator schedules every clip as a reel, never as a feed post) — mirrors
how ``planning/linkedin/linkedin_composer.py`` splits composer mechanics out
of ``schedule_linkedin_posts.py``.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page

from planning.instagram.instagram_labels import NEXT_MONTH_BTN_RE
from planning.instagram.schedule_instagram_posts import _fill_input

# Shares the "instagram_schedule" logger (rather than a module-local
# "instagram_reels" one) so planning/videos/schedule_videos_posts.py's
# existing ``configure_logger("instagram_schedule", ...)`` call keeps
# covering these lines too — see this repo's issue #37 for what happens to a
# driver's log lines when its logger name is left out of that list.
logger = logging.getLogger("instagram_schedule")


REELS_COMPOSER_URL_FRAGMENT = "reels_composer"

# The footer action ("Next" / "Schedule") is the bottom-most button whose
# trimmed innerText equals the label — the thumbnail widget mounts its own
# "Next", so we always key off the LAST match.
_REEL_FOOTER_STATE_JS = r"""
(label) => {
    const bs = Array.from(document.querySelectorAll('[role="button"],button'))
        .filter(b => ((b.innerText || '') + '').trim() === label);
    if (!bs.length) return {found: false, disabled: null};
    return {found: true, disabled: bs[bs.length - 1].getAttribute('aria-disabled')};
}
"""

_REEL_FOOTER_CLICK_JS = r"""
(label) => {
    const bs = Array.from(document.querySelectorAll('[role="button"],button'))
        .filter(b => ((b.innerText || '') + '').trim() === label);
    if (!bs.length) return false;
    bs[bs.length - 1].click();
    return true;
}
"""

# The "Schedule" radio option on the Scheduling-options step is the FIRST node
# whose trimmed text equals "Schedule" (the footer action is later in the DOM).
_REEL_SCHEDULE_OPTION_JS = r"""
() => {
    const els = Array.from(document.querySelectorAll('[role="radio"],[role="button"],label,div'));
    const opt = els.find(e => ((e.innerText || '') + '').trim() === 'Schedule');
    if (!opt) return false;
    opt.click();
    return true;
}
"""


def _wait_reels_composer_ready(page: Page, *, timeout_ms: int = 30000) -> None:
    """Wait for the Reels composer page to load and its 'Add Video' button.

    "Create reel" is a full-page navigation to /latest/reels_composer/, so we
    wait on both the URL and the visible upload affordance before driving it.
    """
    add = page.get_by_role("button", name=re.compile(r"^Add Video$", re.I))
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline:
        if REELS_COMPOSER_URL_FRAGMENT in (page.url or "").lower() and add.count():
            try:
                add.first.wait_for(state="visible", timeout=2000)
                return
            except Exception:
                pass
        page.wait_for_timeout(500)
    raise RuntimeError(
        "Reels composer did not load (no visible 'Add Video' button) — "
        f"url={page.url!r}."
    )


def _reel_add_video(page: Page, video_path: Path) -> None:
    """Click 'Add Video' and push the clip via the file chooser.

    Mirrors ``_upload_files`` Leg 1 (Playwright click → JS-native retry) because
    the Reels composer's button hits the same React handler-binding race the
    post composer does. One file in → one tile here (the post composer's
    duplicate-attach does not occur in the Reels composer).
    """
    add = page.get_by_role("button", name=re.compile(r"^Add Video$", re.I)).first
    add.wait_for(state="visible", timeout=12000)

    def _try(click_fn) -> Optional[object]:
        try:
            with page.expect_file_chooser(timeout=6000) as fc_info:
                click_fn()
            return fc_info.value
        except Exception as err:
            logger.debug("Reels 'Add Video' click → no FileChooser: %s", err)
            return None

    chooser = _try(lambda: add.click(timeout=5000))
    if chooser is None:
        page.wait_for_timeout(600)
        chooser = _try(lambda: add.evaluate("el => el.click()"))
    if chooser is None:
        raise RuntimeError("Reels 'Add Video' did not open a file chooser.")
    chooser.set_files([str(video_path)])
    logger.info("📤 Reels: video sent via 'Add Video' file chooser.")
    page.wait_for_timeout(2500)


def _wait_reel_footer_enabled(
    page: Page, label: str, *, timeout_ms: int = 120000, poll_ms: int = 1000,
) -> None:
    """Poll until the reels footer ``label`` button is present and enabled.

    Meta keeps footer "Next" ``aria-disabled`` until the upload finishes
    processing (the left rail shows the percentage climbing to 100%); a
    multi-MB clip can take a while, hence the generous default.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    last = None
    while page.evaluate("() => Date.now()") < deadline:
        st = page.evaluate(_REEL_FOOTER_STATE_JS, label)
        if st["found"] and st["disabled"] not in ("true",):
            return
        if st != last:
            logger.debug("⏳ reels footer '%s' state=%s, polling…", label, st)
            last = st
        page.wait_for_timeout(poll_ms)
    raise RuntimeError(
        f"Reels footer '{label}' stayed disabled/absent after {timeout_ms} ms — "
        f"upload likely did not finish."
    )


def _click_reel_footer(page: Page, label: str) -> None:
    """JS-native click the bottom-most reels footer button named ``label``."""
    if not page.evaluate(_REEL_FOOTER_CLICK_JS, label):
        raise RuntimeError(f"Reels footer '{label}' button not found.")
    page.wait_for_timeout(1500)


def _select_reel_schedule_option(page: Page) -> None:
    """On the Scheduling-options step, pick 'Schedule' (vs 'Share now').

    Selecting it reveals the date/time inputs and flips the footer action from
    'Share now' to 'Schedule'. The time triplet (``aria-label="hours"`` etc.)
    mounts a beat after the date input, so we wait for it before returning —
    otherwise ``_set_all_visible_date_time`` finds the date input but 0 time
    triplets and Schedule never enables.
    """
    if not page.evaluate(_REEL_SCHEDULE_OPTION_JS):
        raise RuntimeError("Could not find the 'Schedule' option on the reels scheduling step.")
    try:
        page.locator('input[aria-label="hours"]').first.wait_for(state="attached", timeout=15000)
    except Exception:
        logger.warning("⚠️ reels time inputs did not mount after selecting 'Schedule' — proceeding anyway.")
    page.wait_for_timeout(800)


def _click_reel_calendar_day(page: Page, target: date) -> None:
    """In the open reels date-calendar popup, navigate to the target month and
    click the day cell.

    The reels calendar labels day cells "Tuesday, 16 June 2026" (day-before-
    month, no comma after the day) — distinct from the post/story composer's
    "Tuesday, June 16, 2026", so it needs its own matcher. Forward-only month
    navigation is enough: the calendar opens on the current month and video is
    always scheduled for a future day.
    """
    day_aria = f"{target.strftime('%A')}, {target.day} {target.strftime('%B %Y')}"
    for _ in range(18):  # ~1.5 years forward bound
        if page.locator(f'[aria-label="{day_aria}"]').count():
            break
        nxt = page.get_by_role("button", name=NEXT_MONTH_BTN_RE)
        if not nxt.count():
            break
        try:
            nxt.first.click(timeout=3000)
            page.wait_for_timeout(400)
        except Exception:
            break
    cell = page.locator(f'[aria-label="{day_aria}"]')
    if not cell.count():
        raise RuntimeError(f"Reels calendar: could not find day cell {day_aria!r}.")
    cell.first.click(timeout=5000)
    page.wait_for_timeout(600)


def _set_reel_schedule_datetime(page: Page, target: date, hour: int, minute: int) -> None:
    """Set the reel's scheduled date + time on the Scheduling-options step.

    The reels date field is a segmented editor pre-set to today; *typing* into
    it (even valid digits) corrupts it and unmounts the whole scheduling form,
    so the date is set by opening its calendar popup and clicking the target day
    (see ``_click_reel_calendar_day``). The hours/minutes/meridiem triplet is a
    plain input set and is typed exactly like the post/story composer.
    """
    date_inp = page.locator('input[placeholder="mm/dd/yyyy"]').first
    date_inp.click(timeout=4000)
    page.wait_for_timeout(800)
    _click_reel_calendar_day(page, target)

    h12 = hour % 12 or 12
    mer = "AM" if hour < 12 else "PM"
    try:
        _fill_input(page, page.locator('input[aria-label="hours"]').first, str(h12))
        _fill_input(page, page.locator('input[aria-label="minutes"]').first, f"{minute:02d}")
        _fill_input(page, page.locator('input[aria-label="meridiem"]').first, mer)
        page.keyboard.press("Tab")
        page.wait_for_timeout(300)
    except Exception as err:
        raise RuntimeError(f"Could not set reel schedule time: {err}")
    logger.info("🕒 Reel scheduled for %s %d:%02d %s", target.isoformat(), h12, minute, mer)


# Anchored so it matches only the post-Schedule confirmation heading, never the
# earlier "Schedule" radio option or the "Scheduling options" step label.
REEL_SCHEDULED_DIALOG_RE = re.compile(r"^Reel scheduled$", re.I)


def _reel_scheduled_dialog_visible(page: Page) -> bool:
    """True when Meta's post-Schedule 'Reel scheduled' confirmation dialog is up."""
    try:
        loc = page.get_by_text(REEL_SCHEDULED_DIALOG_RE)
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _dismiss_reel_success_dialog(page: Page) -> None:
    """Best-effort click 'Done' on the 'Reel scheduled' confirmation dialog so
    the next day starts from a clean planner state."""
    try:
        done = page.get_by_role("button", name=re.compile(r"^Done$", re.I))
        if done.count():
            done.first.click(timeout=3000)
            page.wait_for_timeout(800)
    except Exception:
        pass


def _wait_reel_composer_closes(page: Page, *, timeout_ms: int = 30000) -> bool:
    """Wait for either success signal that means the reel was scheduled.

    Two outcomes count as success, either of which ends the wait:
    (1) the URL leaves /reels_composer/ (Meta navigates back to the planner), or
    (2) the in-place "Reel scheduled" confirmation dialog appears over the
        composer while the URL stays on /latest/reels_composer/ (issue #125).
        A URL-only check times out here and falsely reports failure even though
        the reel IS scheduled; the caller dismisses the dialog via "Done".
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline:
        if REELS_COMPOSER_URL_FRAGMENT not in (page.url or "").lower():
            return True
        if _reel_scheduled_dialog_visible(page):
            return True
        page.wait_for_timeout(500)
    return False
