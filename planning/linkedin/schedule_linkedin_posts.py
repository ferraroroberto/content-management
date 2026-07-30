"""Schedule next week's LinkedIn posts from the Notion editorial database.

Three coexisting routes are dispatched off the WIP-LI rows based purely on
the editorial-row relation pattern (no read of the linked post page's type):

* ``ILL``  — ``illustration LI`` set AND ``article LI`` empty.
             The original photo-with-caption flow: caption is read from the
             illustration's earliest ``publishIG`` row (text IG).
* ``POST`` — ``illustration LI`` set AND ``article LI`` set AND
             ``post LI`` set. Same UI as ILL but the caption comes from the
             linked posts-DB page body (cached into ``textLI``) and is typed
             with ``@mention`` resolution via the LI typeahead.
* ``CAROUSEL`` — ``illustration LI`` empty AND ``article LI`` empty AND
             ``post LI`` set AND no ``newsletter`` relation. A different UI
             entirely: feed → Start a post → More → Add a document → upload
             the PDF located via fuzzy folder match under
             ``<thread_root>/<books|monographic>/``, set the document title,
             type the caption (with mentions), then schedule.

Newsletter rows (any ``newsletter`` relation) are skipped — newsletter
posting is a separate manual / parallel process.

This is a planner, not a bot. No interactions with other users are automated:
no likes, no comments, no follows. The script only places my own pre-written
posts into LinkedIn's native ``Schedule for later`` flow.

CLI:
    python -m linkedin.schedule_linkedin_posts \
        [--week-start YYYY-MM-DD]   # default: next Monday
        [--date YYYYMMDD]           # single-day mode
        [--all-wip]                 # schedule every WIP-LI row, no date filter
        [--dry-run | --live]        # default: dry-run
        [--force]                   # schedule even if link LI already set
        [--debug]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, Optional

from notion_client.errors import HTTPResponseError
from playwright.sync_api import Page, TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.linkedin.linkedin_session import (  # noqa: E402
    LinkedInSession,
    LoginRequiredError,
    configure_logger,
    load_linkedin_config,
    load_notion_token,
)
from planning.linkedin.linkedin_carousel_pdf import (  # noqa: E402
    CarouselDoc,
    locate_pdf,
)
from planning.linkedin.linkedin_composer import (  # noqa: E402
    FEED_ENTRY_CLICK_TIMEOUT_MS,
    FEED_ENTRY_EFFECT_TIMEOUT_MS,
    click_feed_entry,
    fill_caption_with_mentions,
    schedule_pre_state,
    wait_for_schedule_confirmation,
    wait_for_upload_complete,
)
from planning.linkedin.linkedin_labels import (  # noqa: E402
    ADD_BTN_RE,
    ALT_TEXT_BTN_RE,
    CHOOSE_FILE_BTN_RE,
    CONFIRM_BTN_RE,
    DATE_INPUT_SEL,
    DIALOG_SEL,
    DISCARD_BTN_RE,
    DOCUMENT_BTN_RE,
    DONE_BTN_RE,
    EXPAND_CONTENT_TYPES_RE,
    FINAL_SCHEDULE_BTN_RE,
    PHOTO_TEXT_RE,
    SCHEDULE_CLOCK_SEL,
    SCHEDULE_SUMMARY_SEL,
    START_POST_TEXT_RE,
    TIME_INPUT_SEL,
    TIME_MENU_SEL,
    month_token_candidates,
    schedule_date_candidates,
    time_picker_candidates,
)
from planning.linkedin.linkedin_posts_body import (  # noqa: E402
    PostPayload,
    assert_caption_within_linkedin_limit,
    load_post_payload,
)
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    get_property_type,
    init_notion_client,
    query_rows_by_filter,
    retrieve_page,
    set_field,
)
from reporting.notion.notion_update import format_database_id  # noqa: E402
from planning._dates import (  # noqa: E402
    date_to_day_title,
    next_monday,
    parse_single_date,
    parse_week_start,
)

Route = Literal["ILL", "POST", "CAROUSEL"]

logger = logging.getLogger("linkedin_schedule")


def _resolve_schedule_time(cfg: dict, d: date) -> tuple[int, int]:
    """Pick the (hour, minute) for `d` from config.

    Weekdays use ``schedule_hour_local`` / ``schedule_minute_local`` (default
    06:30). Saturdays and Sundays use ``schedule_weekend_hour_local`` /
    ``schedule_weekend_minute_local`` (default 08:00) if set, otherwise fall
    back to the weekday values.
    """
    if d.weekday() >= 5:  # 5=Sat, 6=Sun
        hour = cfg.get("schedule_weekend_hour_local", cfg["schedule_hour_local"])
        minute = cfg.get("schedule_weekend_minute_local", cfg["schedule_minute_local"])
    else:
        hour = cfg["schedule_hour_local"]
        minute = cfg["schedule_minute_local"]
    return hour, minute


# ---------- Row model ----------

@dataclass
class ScheduleRow:
    page_id: str
    day: date
    route: Route
    illustration_page_id: Optional[str]
    post_page_id: Optional[str]
    article_relation_count: int
    newsletter_relation_count: int
    existing_post_url: Optional[str]

    @property
    def day_title(self) -> str:
        return date_to_day_title(self.day)


@dataclass
class IllustrationData:
    image_filename: str
    alt_text: str
    caption_text: str


def _classify_route(
    illust_count: int,
    article_count: int,
    post_count: int,
    newsletter_count: int,
) -> Optional[Route]:
    """Return the route for the given relation counts, or None to skip.

    Pure function — encapsulates the three-way branch documented at the
    top of the module. The caller logs the skip reason.
    """
    if illust_count and not article_count:
        # Existing route: illustration alone (post LI may or may not be set;
        # the caption still comes from text IG of the earliest publishIG row).
        return "ILL"
    if illust_count and article_count and post_count:
        return "POST"
    if not illust_count and not article_count and post_count and not newsletter_count:
        return "CAROUSEL"
    return None


# ---------- Notion query ----------

def fetch_wip_li_rows(
    notion,
    db_id: str,
    editorial_columns: dict,
    days: Optional[list[date]],
) -> list[ScheduleRow]:
    """Fetch editorial rows where ``Work in Progress LI`` is checked.

    If ``days`` is a list, filters by title-equals for each target day
    (the editorial title is a YYYYMMDD string). If ``days`` is ``None``,
    runs a single query with no date filter and returns every WIP-LI
    row — used by ``--all-wip`` mode."""
    wip_col = editorial_columns["wip_checkbox"]
    title_col = editorial_columns["title_day"]
    illust_col = editorial_columns["illustration_rel"]
    article_col = editorial_columns["article_rel"]
    post_url_col = editorial_columns["post_url"]
    post_rel_col = editorial_columns.get("post_rel")
    newsletter_col = editorial_columns.get("newsletter_rel")

    rows: list[ScheduleRow] = []

    def _row_day(r: dict) -> Optional[date]:
        """Parse the YYYYMMDD title back into a date; None if unparseable."""
        title_prop = r.get("properties", {}).get(title_col, {}) or {}
        segs = title_prop.get("title", []) or []
        text = "".join(seg.get("plain_text", "") for seg in segs).strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None

    def _ingest(results, default_day: Optional[date]):
        for r in results:
            props = r.get("properties", {})
            row_day = default_day or _row_day(r)
            if row_day is None:
                logger.warning("⚠️  Skipping row %s: unparseable day title.", r.get("id"))
                continue
            day_label = date_to_day_title(row_day)
            illust_rels = props.get(illust_col, {}).get("relation", []) or []
            article_rels = props.get(article_col, {}).get("relation", []) or []
            post_rels = (
                props.get(post_rel_col, {}).get("relation", []) or []
                if post_rel_col else []
            )
            newsletter_rels = (
                props.get(newsletter_col, {}).get("relation", []) or []
                if newsletter_col else []
            )
            route = _classify_route(
                len(illust_rels), len(article_rels), len(post_rels), len(newsletter_rels),
            )
            if route is None:
                logger.info(
                    "⏭️  %s: no matching route "
                    "(illust=%d article=%d post=%d newsletter=%d) — skipping.",
                    day_label, len(illust_rels), len(article_rels),
                    len(post_rels), len(newsletter_rels),
                )
                continue
            existing_url = None
            url_prop = props.get(post_url_col, {})
            if url_prop.get("type") == "url":
                existing_url = url_prop.get("url")
            rows.append(
                ScheduleRow(
                    page_id=r["id"],
                    day=row_day,
                    route=route,
                    illustration_page_id=illust_rels[0]["id"] if illust_rels else None,
                    post_page_id=post_rels[0]["id"] if post_rels else None,
                    article_relation_count=len(article_rels),
                    newsletter_relation_count=len(newsletter_rels),
                    existing_post_url=existing_url,
                )
            )

    if days is None:
        results = query_rows_by_filter(
            notion,
            db_id,
            filter_obj={"property": wip_col, "checkbox": {"equals": True}},
        )
        _ingest(results, default_day=None)
    else:
        for d in days:
            title = date_to_day_title(d)
            results = query_rows_by_filter(
                notion,
                db_id,
                filter_obj={
                    "and": [
                        {"property": title_col, "title": {"equals": title}},
                        {"property": wip_col, "checkbox": {"equals": True}},
                    ]
                },
            )
            _ingest(results, default_day=d)

    rows.sort(key=lambda r: r.day)
    return rows


def fetch_illustration(notion, illustration_page_id: str, cfg: dict) -> IllustrationData:
    """Read the relevant fields off the source illustration page.

    Caption rule (per user spec): the illustration's ``text IG to copy``
    formula concatenates the captions of EVERY day the illustration was
    published, which produces garbled multi-version captions when an
    illustration has been published more than once. Instead, we follow the
    illustration's ``publishIG`` relation back to all editorial rows that
    published it, sort by ``day`` ascending, and read the ``text IG``
    rich_text from the EARLIEST one — the canonical single-version caption.

    Fallback: if ``publishIG`` is empty (e.g. brand-new illustration never
    published before), use the illustration's ``text IG to copy`` formula —
    which in that case won't have anything to concatenate so it's safe.
    """
    illust_cols = cfg["illustration_columns"]
    ed_cols = cfg["editorial_columns"]

    page = retrieve_page(notion, illustration_page_id)
    fname = get_field(page, "image_filename", illust_cols) or ""
    alt = get_field(page, "alt_text", illust_cols) or ""

    # The illustration title is the bare name (no extension). Local files are
    # stored as <name>.png in `illustrations_folder`.
    fname_str = str(fname).strip()
    if fname_str and not fname_str.lower().endswith(".png"):
        fname_str = f"{fname_str}.png"

    # --- Caption: earliest publishIG editorial row's `text IG` ---
    caption = ""
    publish_col = illust_cols["publish_relation"]
    publish_rels = page.get("properties", {}).get(publish_col, {}).get("relation", []) or []

    if publish_rels:
        candidates: list[tuple[str, str]] = []
        for rel in publish_rels:
            rel_id = rel.get("id")
            if not rel_id:
                continue
            try:
                ed_page = retrieve_page(notion, rel_id)
            except Exception as err:
                logger.warning("⚠️ could not fetch %s for publishIG resolution: %s", rel_id, err)
                continue
            day_str = get_field(ed_page, "title_day", ed_cols) or ""
            text = get_field(ed_page, "caption_text", ed_cols) or ""
            day_str = str(day_str).strip()
            text = str(text).strip()
            if day_str:
                candidates.append((day_str, text))

        candidates.sort(key=lambda x: x[0])  # YYYYMMDD lex = chronological
        for day_str, text in candidates:
            if text:
                caption = text
                logger.info(
                    "📝 caption from earliest publishIG row (%s): %d chars",
                    day_str, len(caption),
                )
                break

    if not caption:
        fallback_text = get_field(page, "caption_fallback", illust_cols) or ""
        caption = str(fallback_text).strip()
        if caption:
            logger.warning(
                "⚠️ publishIG yielded no caption — falling back to '%s' formula (%d chars)",
                illust_cols["caption_fallback"], len(caption),
            )

    return IllustrationData(
        image_filename=fname_str,
        alt_text=str(alt).strip(),
        caption_text=caption,
    )


def resolve_image_path(illustrations_folder: str, image_filename: str) -> Path:
    if not image_filename:
        raise FileNotFoundError("Illustration row has no filename.")
    # The formula output occasionally has comma-joined names — take the first.
    first = str(image_filename).split(",")[0].strip()
    candidate = Path(illustrations_folder) / first
    if not candidate.exists():
        raise FileNotFoundError(f"Illustration not found: {candidate}")
    return candidate


# ---------- LinkedIn UI flow ----------

def _dialog(page: Page):
    """Locator for whatever modal container LinkedIn currently renders.

    Scoping matters: the feed leaves carousel arrow buttons (aria-label="Next")
    visible behind the modal, and those would otherwise win resolution and
    cause spurious clicks.

    ``DIALOG_SEL`` matches both the legacy ``<div role="dialog">`` and the
    native ``<dialog open>`` LinkedIn's 2026 rebuild replaced it with — see
    ``linkedin_labels.DIALOG_SEL`` for the full account (issue #178).

    Always chain off this locator (``_dialog(page).locator("button…")``)
    rather than interpolating ``DIALOG_SEL`` into a bigger selector string:
    the constant is a comma-separated selector list, so ``f'{DIALOG_SEL}
    button'`` would parse as ``[role="dialog"]`` OR ``dialog[open] button``
    and silently match the hidden video.js modals left in the feed.
    """
    return page.locator(DIALOG_SEL)


def _dialog_button(page: Page, name_re: re.Pattern):
    """Find a button matching `name_re` scoped to any open dialog."""
    return _dialog(page).get_by_role("button", name=name_re)


def _composer_dialog(page: Page):
    """The post-composer modal, identified by the caption editor it contains.

    Both the photo/carousel and the video flows watch this locator's count
    drop to confirm the composer actually closed after the final Schedule
    click — a post-scheduled signal that doesn't depend on any copy string.
    Expressed as a ``filter(has=…)`` rather than a ``:has()`` CSS suffix so it
    composes with the two-branch ``DIALOG_SEL`` without re-parsing it.
    """
    return _dialog(page).filter(
        has=page.locator('div[role="textbox"][contenteditable="true"]')
    )


def _dialog_next_button(page: Page):
    """The dialog's primary 'Next' / 'Siguiente' button, matched by visible text.

    Not by accessible name: the feed's carousel arrow also carries
    aria-label="Next" but renders no visible text, so ``:has-text``
    disambiguates correctly across locales.
    """
    return _dialog(page).locator(
        'button:has-text("Next"), button:has-text("Siguiente")'
    )


def _click_add_photo(page: Page) -> None:
    """Click the 'Photo' button on the feed (opens post dialog + file picker)."""
    # The hydrated 'Photo' affordance is a role-less <a> with a <p>Photo</p>
    # label, so it's matched by visible text, not accessible name (issue #140).
    # `click_feed_entry` re-resolves on each attempt, absorbing both the
    # cold-start race (issue #27) and the share-box rehydration swap. Passing
    # `expect_selector` also guards a third failure mode: the click can land
    # on a structurally-ready-but-not-yet-hydrated button and be silently
    # swallowed, so `_upload_photo`'s `input[type="file"]` wait times out with
    # no composer ever having opened (issue #150). `click_feed_entry` retries
    # in that case instead of returning a false success.
    #
    # LinkedIn's 'Photo' click fires a native <input type="file"> click under
    # the hood. Without an active file-chooser listener, Playwright doesn't
    # intercept that at the CDP level, so Chrome renders the real OS "Open"
    # dialog on screen. `_upload_photo` below sets the file directly on the
    # hidden input via CDP, which works regardless — so the post still
    # succeeds — but the orphaned native dialog never gets dismissed and
    # lingers on the desktop. `expect_file_chooser` suppresses it before it
    # can render, mirroring the guard `_share_document_choose_file` already
    # uses for the carousel/PDF route. A timeout here is non-fatal: it just
    # means LinkedIn didn't trigger a native chooser this time.
    try:
        with page.expect_file_chooser(timeout=FEED_ENTRY_CLICK_TIMEOUT_MS + FEED_ENTRY_EFFECT_TIMEOUT_MS):
            click_feed_entry(page, PHOTO_TEXT_RE, "Photo", expect_selector='input[type="file"]')
    except PWTimeoutError:
        logger.debug("No native file chooser observed for the 'Photo' click.")


def _upload_photo(page: Page, image_path: Path) -> None:
    """Set the file input that the Photo button mounts."""
    # After clicking Photo there is exactly one <input type='file'> in the DOM.
    inp = page.locator('input[type="file"]')
    try:
        inp.first.wait_for(state="attached", timeout=10000)
        inp.first.set_input_files(str(image_path))
    except Exception as err:
        raise RuntimeError(f"Could not upload image to LinkedIn editor: {err}")


def _set_alt_text(page: Page, alt_text: str) -> None:
    """Open the ALT dialog from the photo editor and fill the textbox."""
    if not alt_text:
        logger.warning("⚠️ No alt text supplied — skipping ALT step.")
        return
    # Photo editor exposes an 'Alternative text' role=button.
    try:
        _dialog_button(page, ALT_TEXT_BTN_RE).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not open ALT dialog: {err}")

    # The ALT dialog contains a single textarea. EN placeholder is 'How would
    # you describe this image?'; ES is 'describe la imagen' / 'describirías
    # esta imagen'. Fall back to any textarea in the dialog so the selector
    # survives further LinkedIn copy edits.
    try:
        ta = _dialog(page).locator(
            "textarea[placeholder*='describe this image' i], "
            "textarea[placeholder*='describe' i], "
            "textarea[placeholder*='imagen' i], "
            "textarea"
        )
        ta.first.wait_for(state="visible", timeout=10000)
        ta.first.fill(alt_text)
    except Exception as err:
        raise RuntimeError(f"Could not fill ALT textarea: {err}")

    # Close ALT dialog via its 'Add' button. The ALT panel replaces the editor
    # body inside the same dialog, so while it is open its 'Add' is the only
    # ``^add$`` accessible-name match — the editor's add-another-image button
    # (aria-label="Add") is unmounted. ``.last`` is kept as belt-and-braces for
    # a LinkedIn build that stacks the panels instead of swapping them.
    try:
        _dialog_button(page, ADD_BTN_RE).last.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click ALT 'Add' button: {err}")


def _click_next_after_photo_editor(page: Page) -> None:
    """Click 'Next' / 'Siguiente' in the photo editor → goes to the composer."""
    try:
        _dialog_next_button(page).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Next' in the photo editor: {err}")


def _fill_caption(page: Page, caption: str) -> None:
    """Fill the post body in the composer."""
    # Confirmed: the composer body is the unique
    # div[role='textbox'][contenteditable='true'] on the page.
    editor = page.locator('div[role="textbox"][contenteditable="true"]')
    try:
        editor.first.wait_for(state="visible", timeout=10000)
        editor.first.click()
        # contenteditable doesn't accept .fill() reliably; use keyboard.
        page.keyboard.type(caption, delay=5)
    except Exception as err:
        raise RuntimeError(f"Could not fill caption: {err}")


def _open_schedule_dialog(page: Page) -> None:
    """Click the composer's clock affordance to open the Schedule dialog."""
    # Matched structurally by the icon it wraps — its accessible name is a
    # live counter ("Scheduled (2)"), see ``SCHEDULE_CLOCK_SEL``.
    try:
        _dialog(page).locator(SCHEDULE_CLOCK_SEL).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not open the Schedule dialog: {err}")


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


def _wait_for_summary_match(page: Page, target: date, *, timeout_ms: int = 4000) -> bool:
    """Poll the summary line until it describes ``target``, or give up.

    Polled rather than slept: the summary is re-rendered asynchronously after
    the field changes, and a single fixed wait is a coin flip — a 700 ms sleep
    passed for five rows in a row and then failed on a date LinkedIn was in
    fact accepting. Polling also returns as soon as it lands, so the happy path
    is faster than the sleep it replaces.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while True:
        if _summary_matches(_schedule_summary(page), target):
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


def _set_schedule_time(page: Page, hour: int, minute: int) -> None:
    """Pick the time from the dialog's 15-minute-slot menu.

    Typed input is unreliable here — the field re-formats each keystroke and
    ends up rejecting even the exact string it displays by default — so we open
    the menu and click the slot, which is also locale-proof.
    """
    ti = page.locator(TIME_INPUT_SEL).first
    candidates = time_picker_candidates(hour, minute)
    try:
        ti.click(timeout=10000)
        menu = page.locator(TIME_MENU_SEL).first
        menu.wait_for(state="visible", timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not open the time picker: {err}")

    for cand in candidates:
        entry = menu.get_by_text(re.compile(rf"^\s*{re.escape(cand)}\s*$", re.I))
        if entry.count() == 0:
            continue
        try:
            entry.first.scroll_into_view_if_needed(timeout=5000)
            entry.first.click(timeout=5000)
            page.wait_for_timeout(600)
            return
        except Exception:
            continue
    raise RuntimeError(
        f"No time-picker slot matched any locale candidate {candidates}"
    )


def _set_schedule_datetime(page: Page, target: date, hour: int, minute: int) -> None:
    """Set Date and Time in the rebuilt Schedule dialog, then verify both.

    Date first: the time menu offers only future slots, so a stale date can
    hide the slot we want.
    """
    _set_schedule_date(page, target)
    _set_schedule_time(page, hour, minute)

    if not _wait_for_summary_match(page, target):
        raise RuntimeError(
            f"Schedule did not stick for {target:%Y-%m-%d} {hour:02d}:{minute:02d} — "
            f"LinkedIn summary reads {_schedule_summary(page)!r}"
        )
    logger.info("🗓️ %s", _schedule_summary(page))


def _click_schedule_confirm(page: Page) -> None:
    """Commit the date+time and return to the composer.

    The Schedule dialog's primary action is 'Confirm' since the rebuild ('Next'
    before it); ``CONFIRM_BTN_RE`` carries both. It stays ``disabled`` until
    LinkedIn has parsed both fields, so a click that finds it disabled means
    ``_set_schedule_datetime`` did not actually take.
    """
    try:
        _dialog_button(page, CONFIRM_BTN_RE).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Confirm' in Schedule dialog: {err}")


def _click_final_schedule(page: Page) -> None:
    """Click the final 'Schedule' / 'Programar' button in the composer (live mode).

    Once a schedule is attached, the composer's primary action flips from
    'Post' to exactly 'Schedule' (or 'Programar'). ``FINAL_SCHEDULE_BTN_RE`` is
    anchored on ``^...$`` so it matches that standalone button and not the
    'Schedule post' tab inside the Schedule dialog.
    """
    try:
        _dialog_button(page, FINAL_SCHEDULE_BTN_RE).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click final 'Schedule' button: {err}")


def _close_dialogs(page: Page) -> None:
    """Best-effort attempt to close any open dialogs (for dry-run cleanup).

    The post composer has unsaved changes — Escape triggers a discard prompt.
    """
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
    except Exception:
        return
    # A discard-confirmation dialog may appear; accept it.
    try:
        discard = _dialog_button(page, DISCARD_BTN_RE)
        if discard.count() > 0:
            discard.first.click(timeout=2000)
            page.wait_for_timeout(500)
    except Exception:
        pass
    # Repeat in case a second dialog remains.
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


# ---------- CAROUSEL UI helpers ----------

def _click_more(page: Page) -> None:
    """Expand the composer's secondary content types.

    The composer's icon row shows only Photo (plus Enhance/emoji) until this
    '+' toggle is expanded; Document lives in the revealed row. The toggle is
    named 'Expand content types' since the rebuild, 'More' before it.
    """
    try:
        _dialog_button(page, EXPAND_CONTENT_TYPES_RE).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not expand the composer content types: {err}")


def _click_add_a_document(page: Page) -> None:
    """Click Document in the expanded composer actions row.

    Deliberately NOT ``_dialog_button``: the rebuilt affordance is an
    ``<a aria-label="Document">``, i.e. a *link*, so a ``role="button"`` anchor
    finds nothing. ``get_by_label`` matches on the accessible label whatever
    element carries it, which also covers the older 'Add a document' button.
    """
    try:
        _dialog(page).get_by_label(DOCUMENT_BTN_RE).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click the Document content type: {err}")


def _share_document_choose_file(page: Page, pdf_path: Path) -> None:
    """Push the PDF into the 'Share a document' dialog's Choose-file button.

    Strategy:
      1. Fast path: if any ``input[type=file]`` accepting application/pdf is
         already attached, push the file at it directly.
      2. Otherwise click 'Choose file' and intercept the native file chooser
         via ``expect_file_chooser``.
    """
    # Wait briefly for the Share-a-document dialog to mount.
    page.wait_for_timeout(800)

    inp = page.locator('input[type="file"]')
    try:
        if inp.count() > 0:
            inp.first.wait_for(state="attached", timeout=5000)
            inp.first.set_input_files(str(pdf_path))
            return
    except Exception:
        pass

    try:
        with page.expect_file_chooser(timeout=10000) as fc_info:
            _dialog_button(page, CHOOSE_FILE_BTN_RE).first.click(timeout=5000)
        fc_info.value.set_files(str(pdf_path))
    except Exception as err:
        raise RuntimeError(f"Could not push PDF to 'Share a document' dialog: {err}")


def _button_is_enabled(btn) -> bool:
    """True when a located button is actually clickable.

    ``get_attribute("disabled")`` returns ``""`` for ``<button disabled>`` —
    an *empty string*, which is falsy — so the obvious ``if not disabled``
    test reports every disabled button as ready. That is why the PDF wait
    below used to return instantly and hand a still-processing dialog to the
    next step. Presence, not truthiness, is the test.
    """
    try:
        if btn.get_attribute("disabled") is not None:
            return False
        aria_dis = btn.get_attribute("aria-disabled")
        return aria_dis is None or aria_dis.lower() == "false"
    except Exception:
        return False


def _wait_for_pdf_upload(page: Page, *, timeout_ms: int = 180000) -> None:
    """Wait until the Share-a-document dialog will accept 'Done'.

    Must run *after* the title is filled, not before: 'Done' gates on the
    title as well as on PDF processing, so polling it first deadlocks until
    the timeout. Called in the right order it is the single accurate signal
    that both the upload and the title are satisfied.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline:
        done_btn = _dialog_button(page, DONE_BTN_RE).first
        try:
            ready = done_btn.count() and _button_is_enabled(done_btn)
        except Exception:
            ready = False
        if ready:
            return
        page.wait_for_timeout(1000)
    raise RuntimeError("PDF processing did not finish within the timeout window.")


def _fill_document_title(page: Page, doc_title: str) -> None:
    """Fill the 'Document title' input in the Share-a-document dialog.

    Anchored primarily on the visible ``<label>Document title*</label>``,
    which the input is bound to by ``for=`` — the input itself carries no
    ``name``, no ``aria-label`` and no ``data-testid``, only a generated id.
    Placeholder and last-resort text-input selectors follow, and the dialog
    scopes the search so a rogue caption editor can't win.

    Waits for the input rather than assuming it: the dialog mounts before
    LinkedIn has finished rendering the PDF preview, and the previous code
    raced it and reported "could not locate" for an input that simply had not
    appeared yet.
    """
    if not doc_title:
        raise RuntimeError("Empty document title — refusing to submit.")

    dlg = _dialog(page)
    candidates = (
        dlg.get_by_label(re.compile(r"document title|título del documento", re.I)),
        dlg.locator('input[placeholder*="title" i]'),
        dlg.locator('input[placeholder*="título" i]'),
        dlg.locator('input[name*="title" i]'),
        dlg.locator('input[aria-label*="title" i]'),
        dlg.locator('input[type="text"]'),
    )

    last_err: Optional[Exception] = None
    deadline = page.evaluate("() => Date.now()") + 30000
    while page.evaluate("() => Date.now()") < deadline:
        for loc in candidates:
            try:
                if not loc.count():
                    continue
                loc.first.wait_for(state="visible", timeout=2000)
                loc.first.fill(doc_title)
                return
            except Exception as err:
                last_err = err
                continue
        page.wait_for_timeout(500)
    raise RuntimeError(f"Could not locate the Document title input (last error: {last_err})")


def _click_document_done(page: Page) -> None:
    """Click 'Done' in the Share-a-document dialog → returns to composer with PDF attached."""
    try:
        _dialog_button(page, DONE_BTN_RE).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Done' on the document dialog: {err}")


def _click_start_a_post(page: Page) -> None:
    """Click the feed's 'Start a post' share box to open the composer.

    The carousel route doesn't use the Photo button — it needs a clean
    empty composer to access the secondary actions row via 'More' → 'Add
    a document'. LI's share box exposes a button with accessible name
    matching 'Start a post' (case-insensitive).
    """
    # The hydrated 'Start a post' box is also role-less (issue #140), so it's
    # matched by visible text. `click_feed_entry` absorbs both the cold-start
    # race (issue #27) and the share-box rehydration swap by re-resolving on each
    # attempt. Both English variants ('Start a post' / 'Create a post') and the
    # Spanish variants are folded into the shared ``START_POST_TEXT_RE`` so one
    # call handles every locale.
    click_feed_entry(page, START_POST_TEXT_RE, "Start a post")


# ---------- Per-row driver ----------

def _finalize_schedule(
    page: Page,
    out_dir: Path,
    day_label: str,
    *,
    wait_for_upload: bool = False,
) -> str:
    """Shared tail of every route: Next → final Schedule → wait composer closes.

    ``wait_for_upload`` adds the explicit-signal-or-60s-fallback wait used
    for media-attached posts (CAROUSEL PDFs), borrowing the videos pattern.
    """
    _click_schedule_confirm(page)
    page.wait_for_timeout(1500)

    pre_shot = out_dir / f"{day_label}-live-pre.png"
    try:
        page.screenshot(path=str(pre_shot), full_page=False)
    except Exception:
        pass

    composer_locator = _composer_dialog(page)
    pre_state = schedule_pre_state(page, composer_locator)

    _click_final_schedule(page)

    try:
        signal = wait_for_schedule_confirmation(
            page, composer_locator, pre_state, label=day_label,
        )
    except RuntimeError as err:
        shot = out_dir / f"{day_label}-live-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"{err} See {shot}")
    logger.info("🔔 %s: schedule confirmed via %s", day_label, signal)

    page.wait_for_timeout(1500)
    if wait_for_upload:
        # CRITICAL for PDF posts: LI closes the composer immediately but
        # keeps uploading the document in the background. Without waiting,
        # the scheduled post can end up media-less ("Something went wrong").
        try:
            wait_for_upload_complete(page)
        except Exception as err:
            logger.warning("⚠️ %s: upload-complete wait raised %s — continuing.", day_label, err)

    shot = out_dir / f"{day_label}-live-after.png"
    try:
        page.screenshot(path=str(shot), full_page=False)
    except Exception:
        pass
    logger.info("✅ LIVE %s: scheduled (composer closed). Screenshot → %s", day_label, shot)
    return f"{day_label}: LIVE scheduled"


def schedule_one_illustration_row(
    session: LinkedInSession,
    cfg: dict,
    row: ScheduleRow,
    illust: IllustrationData,
    image_path: Path,
    *,
    dry_run: bool,
    use_mention_resolution: bool = False,
) -> str:
    """ILL and POST routes share the photo+caption flow; only the caption
    source and mention-resolution differ.

    ``use_mention_resolution=True`` swaps the plain ``_fill_caption`` for
    the videos-package mention-aware typer (POST route caption may contain
    ``@FirstName Last`` references that must resolve through LI's typeahead).
    """
    page = session.page
    day_label = row.day_title

    session.goto_with_login_check(cfg["feed_url"])

    # Clicking 'Photo' on the feed opens BOTH the post dialog and the file
    # picker (the <input type="file"> appears in the DOM) — one click, no
    # need for a separate 'Start a post' step.
    _click_add_photo(page)
    page.wait_for_timeout(1500)
    _upload_photo(page, image_path)
    # The photo editor takes a moment to open after upload completes.
    page.wait_for_timeout(4000)
    _set_alt_text(page, illust.alt_text)
    page.wait_for_timeout(1000)
    _click_next_after_photo_editor(page)
    page.wait_for_timeout(2500)
    if use_mention_resolution:
        fill_caption_with_mentions(page, illust.caption_text)
    else:
        _fill_caption(page, illust.caption_text)
    page.wait_for_timeout(800)
    _open_schedule_dialog(page)
    page.wait_for_timeout(1500)
    hour, minute = _resolve_schedule_time(cfg, row.day)
    _set_schedule_datetime(page, row.day, hour, minute)

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "linkedin"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{day_label}-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s: schedule dialog ready, screenshot → %s (NOT scheduled)",
                    day_label, shot)
        _close_dialogs(page)
        return f"{day_label}: DRY-RUN OK"

    return _finalize_schedule(page, out_dir, day_label, wait_for_upload=False)


def schedule_one_carousel_row(
    session: LinkedInSession,
    cfg: dict,
    row: ScheduleRow,
    doc: CarouselDoc,
    caption: str,
    *,
    dry_run: bool,
) -> str:
    """CAROUSEL route: feed → Start a post → More → Add a document → upload
    → title → Done → caption (with mentions) → Schedule.
    """
    page = session.page
    day_label = row.day_title

    session.goto_with_login_check(cfg["feed_url"])

    _click_start_a_post(page)
    page.wait_for_timeout(1500)
    _click_more(page)
    page.wait_for_timeout(800)
    _click_add_a_document(page)
    page.wait_for_timeout(1200)
    _share_document_choose_file(page, doc.pdf_path)
    # Title first: 'Done' gates on the title as well as on PDF processing, so
    # waiting for it before typing one would deadlock (see _wait_for_pdf_upload).
    _fill_document_title(page, doc.doc_title)
    page.wait_for_timeout(400)
    _wait_for_pdf_upload(page)
    _click_document_done(page)
    page.wait_for_timeout(2500)

    if not caption:
        raise RuntimeError("Carousel caption is empty — refusing to schedule.")
    fill_caption_with_mentions(page, caption)
    page.wait_for_timeout(800)

    _open_schedule_dialog(page)
    page.wait_for_timeout(1500)
    hour, minute = _resolve_schedule_time(cfg, row.day)
    _set_schedule_datetime(page, row.day, hour, minute)

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "linkedin"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{day_label}-carousel-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s CAROUSEL: schedule dialog ready, screenshot → %s (NOT scheduled)",
                    day_label, shot)
        _close_dialogs(page)
        return f"{day_label}: DRY-RUN OK"

    return _finalize_schedule(page, out_dir, day_label, wait_for_upload=True)


def schedule_one_row(
    session: LinkedInSession,
    cfg: dict,
    row: ScheduleRow,
    notion,
    *,
    dry_run: bool,
) -> str:
    """Dispatch to the per-route scheduler. Returns a one-line status string.

    Resolves all per-route inputs (illustration data, post body, PDF) here
    so the route helpers stay narrowly focused on the LI UI itself.
    """
    if row.route == "ILL":
        illust = fetch_illustration(notion, row.illustration_page_id, cfg)
        logger.info("🖼️ %s ILL: filename=%s alt_len=%d caption_len=%d",
                    row.day_title, illust.image_filename,
                    len(illust.alt_text), len(illust.caption_text))
        image_path = resolve_image_path(cfg["illustrations_folder"], illust.image_filename)
        return schedule_one_illustration_row(
            session, cfg, row, illust, image_path,
            dry_run=dry_run, use_mention_resolution=False,
        )

    if row.route == "POST":
        illust = fetch_illustration(notion, row.illustration_page_id, cfg)
        image_path = resolve_image_path(cfg["illustrations_folder"], illust.image_filename)
        payload = load_post_payload(notion, row.post_page_id, cfg["posts_columns"])
        assert_caption_within_linkedin_limit(payload)
        # Override the illustration's text-IG caption with the post body.
        illust = IllustrationData(
            image_filename=illust.image_filename,
            alt_text=illust.alt_text,
            caption_text=payload.caption,
        )
        logger.info("🖼️📝 %s POST: illustration=%s post=%r caption_len=%d",
                    row.day_title, illust.image_filename, payload.title, len(payload.caption))
        return schedule_one_illustration_row(
            session, cfg, row, illust, image_path,
            dry_run=dry_run, use_mention_resolution=True,
        )

    if row.route == "CAROUSEL":
        payload = load_post_payload(notion, row.post_page_id, cfg["posts_columns"])
        assert_caption_within_linkedin_limit(payload)
        doc = locate_pdf(payload.title, cfg["carousel"])
        logger.info("📎 %s CAROUSEL: post=%r pdf=%s doc_title=%r caption_len=%d",
                    row.day_title, payload.title, doc.pdf_path.name,
                    doc.doc_title, len(payload.caption))
        return schedule_one_carousel_row(
            session, cfg, row, doc, payload.caption, dry_run=dry_run,
        )

    raise RuntimeError(f"Unknown route on row {row.day_title}: {row.route!r}")


# ---------- Main ----------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule LinkedIn posts from Notion editorial.")
    parser.add_argument("--week-start", type=str, default=None,
                        help="Monday of the target week (YYYY-MM-DD). Default: next Monday.")
    parser.add_argument("--date", type=str, default=None,
                        help="Single-day mode (YYYYMMDD or YYYY-MM-DD). Overrides --week-start.")
    parser.add_argument("--all-wip", action="store_true",
                        help="Schedule every WIP-LI row in the editorial DB, no date filter "
                             "(supports multi-week planning runs).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Walk the flow up to Schedule dialog; do NOT schedule.")
    mode.add_argument("--live", action="store_true", help="Actually click Schedule.")
    parser.add_argument("--force", action="store_true", help="Schedule even if link LI is already populated.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> tuple[int, list[dict]]:
    args = parse_args()
    configure_logger("linkedin_schedule", debug=args.debug)
    cfg = load_linkedin_config()

    # Resolve mode (default = dry-run via config).
    if args.live:
        dry_run = False
    elif args.dry_run:
        dry_run = True
    else:
        dry_run = cfg.get("dry_run_default", True)

    if args.all_wip and (args.date or args.week_start):
        logger.error("❌ --all-wip is mutually exclusive with --date / --week-start.")
        return 2, []

    if args.all_wip:
        target_days = None
        logger.info("🎯 All-WIP mode: ignoring date filter, scheduling every WIP-LI row.")
    elif args.date:
        d = parse_single_date(args.date)
        target_days = [d]
        logger.info("🎯 Single-day mode: %s", d.isoformat())
    else:
        monday = parse_week_start(args.week_start)
        target_days = [monday + timedelta(days=i) for i in range(7)]
        logger.info("🗓️  Target week: %s → %s",
                    target_days[0].isoformat(), target_days[-1].isoformat())

    notion = init_notion_client(load_notion_token())
    if notion is None:
        logger.error("❌ Could not initialize Notion client.")
        return 3, []

    rows = fetch_wip_li_rows(
        notion,
        cfg["editorial_db_id"],
        cfg["editorial_columns"],
        target_days,
    )

    if not rows:
        logger.warning("⚠️ No in-scope WIP-LI illustration-only rows in target range. Nothing to do.")
        return 0, []

    logger.info("📋 %d in-scope row(s):", len(rows))
    for r in rows:
        logger.info("   - %s route=%s (page=%s, link LI=%s)",
                    r.day_title, r.route, r.page_id, r.existing_post_url or "(empty)")

    # Filter on existing post_url unless --force.
    if not args.force:
        before = len(rows)
        rows = [r for r in rows if not r.existing_post_url]
        if len(rows) != before:
            logger.info("⏭️  Skipped %d row(s) whose link LI is already populated (use --force to override).",
                        before - len(rows))
    if not rows:
        logger.info("ℹ️ Nothing left to schedule after dedup. Done.")
        return 0, []

    statuses: list[str] = []
    results: list[dict] = []
    with LinkedInSession(cfg) as session:
        for row in rows:
            try:
                logger.info("🛤️ %s: route=%s", row.day_title, row.route)
                status = schedule_one_row(session, cfg, row, notion, dry_run=dry_run)
                statuses.append(status)
                if dry_run:
                    results.append({"day": row.day_title, "status": "DRY", "detail": status})
                else:
                    results.append({"day": row.day_title, "status": "LIVE", "detail": status})
                # On successful live schedule, untick "Work in Progress LI" on
                # the editorial row so the next run doesn't re-schedule it.
                if not dry_run and "LIVE scheduled" in status:
                    try:
                        set_field(
                            notion,
                            row.page_id,
                            "wip_checkbox",
                            False,
                            cfg["editorial_columns"],
                            "checkbox",
                        )
                        logger.info("☑️ %s: WIP-LI unticked in Notion", row.day_title)
                    except Exception as err:
                        logger.warning(
                            "⚠️ %s: scheduled OK but failed to untick WIP-LI in Notion: %s",
                            row.day_title, err,
                        )
            except LoginRequiredError as err:
                logger.error("❌ %s", err)
                statuses.append(f"{row.day_title}: LOGIN-REQUIRED")
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
                break
            except (FileNotFoundError, RuntimeError, PWTimeoutError,
                    HTTPResponseError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-error")
                logger.error("❌ %s failed: %s (screenshot %s)", row.day_title, err, shot)
                statuses.append(f"{row.day_title}: FAILED ({err})")
                results.append({"day": row.day_title, "status": "FAIL", "detail": f"{err} (screenshot {shot})"})
                # Try to recover the UI for the next row.
                try:
                    _close_dialogs(session.page)
                except Exception:
                    pass

    logger.info("══════════ Summary ══════════")
    for s in statuses:
        logger.info("   %s", s)
    failed = [r for r in results if r["status"] in ("FAIL", "LOGIN-REQUIRED")]
    return (0 if not failed else 11), results


if __name__ == "__main__":
    raise SystemExit(main()[0])
