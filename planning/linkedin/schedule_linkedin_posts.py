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
    fill_caption_with_mentions,
    wait_for_upload_complete,
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

Route = Literal["ILL", "POST", "CAROUSEL"]

logger = logging.getLogger("linkedin_schedule")


# ---------- Date helpers ----------

def next_monday(today: Optional[date] = None) -> date:
    """Return the next Monday strictly after `today` (or today if it IS Monday)."""
    today = today or date.today()
    days_ahead = (7 - today.weekday()) % 7  # Mon=0
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def parse_week_start(s: Optional[str]) -> date:
    if not s:
        return next_monday()
    return datetime.strptime(s, "%Y-%m-%d").date()


def parse_single_date(s: str) -> date:
    """Accept YYYYMMDD or YYYY-MM-DD."""
    s = s.strip()
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").date()
    return datetime.strptime(s, "%Y%m%d").date()


def date_to_day_title(d: date) -> str:
    return d.strftime("%Y%m%d")


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

def _dialog_button(page: Page, name_re: re.Pattern):
    """Find a button matching `name_re` scoped to any open dialog.

    LinkedIn's feed leaves carousel arrow buttons (aria-label="Next") visible
    behind the modal — those would otherwise win `get_by_role` resolution and
    cause spurious clicks.
    """
    return page.locator('[role="dialog"]').get_by_role("button", name=name_re)


def _click_add_photo(page: Page) -> None:
    """Click the 'Photo' button on the feed (opens post dialog + file picker)."""
    # Confirmed via DOM probe: the visible 'Photo' is a <p> inside a <div role='button'>.
    # `get_by_role('button', name='Photo')` resolves that ancestor cleanly.
    try:
        page.get_by_role("button", name=re.compile(r"^photo$", re.I)).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Photo' on the LinkedIn feed: {err}")


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
        _dialog_button(page, re.compile(r"alternative text", re.I)).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not open ALT dialog: {err}")

    # The ALT dialog contains a single textarea with the placeholder
    # 'How would you describe this image?'.
    try:
        ta = page.locator("textarea[placeholder*='describe this image' i]")
        ta.first.wait_for(state="visible", timeout=10000)
        ta.first.fill(alt_text)
    except Exception as err:
        raise RuntimeError(f"Could not fill ALT textarea: {err}")

    # Close ALT dialog via its 'Add' button (NOT the photo-editor 'Add' for new images).
    try:
        _dialog_button(page, re.compile(r"^add$", re.I)).last.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click ALT 'Add' button: {err}")


def _click_next_after_photo_editor(page: Page) -> None:
    """Click 'Next' in the photo editor → goes to the composer."""
    # Scope to dialog and to a button whose visible TEXT contains 'Next' — the
    # carousel arrow button has aria-label='Next' but empty text, so this
    # disambiguates correctly.
    try:
        page.locator('[role="dialog"] button:has-text("Next")').first.click(timeout=10000)
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
    """Click the schedule clock icon in the composer."""
    # The clock has aria-label='Schedule post' on the live LinkedIn UI.
    try:
        page.get_by_role(
            "button", name=re.compile(r"^schedule post$", re.I)
        ).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not open the Schedule dialog: {err}")


def _set_schedule_datetime(page: Page, target: date, hour: int, minute: int) -> None:
    """Set Date and Time in the Schedule dialog.

    Validated against the live LinkedIn UI:
    * Date — click ``input[name='artdeco-date']`` to open the calendar, then
      click the day by its aria-label (e.g. ``Monday, May 18, 2026.``).
      Clicking the day closes the calendar and sets the input value.
      We never press Escape here: Escape bubbles to the composer and triggers
      the "Save this post as a draft?" prompt, which blocks every following
      click.
    * Time — ``input[name='timepicker']`` is a ``role='combobox'`` typeahead.
      Clicking it reveals a ``.artdeco-typeahead__results-list`` whose
      populated instance (``data-count != "0"``) contains all 15-min slots.
      We click the ``<li>`` whose visible text matches (e.g. ``6:30 AM``).
    """
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12
    if h12 == 0:
        h12 = 12
    time_str = f"{h12}:{minute:02d} {suffix}"
    month_name = target.strftime("%B")
    day_aria = f"{target.strftime('%A')}, {month_name} {target.day}, {target.year}."

    # --- Date via calendar click ---
    di = page.locator('input[name="artdeco-date"]').first
    try:
        di.click(timeout=10000)
        page.wait_for_timeout(600)
        # If the displayed month/year is wrong, click Next month until it matches.
        # The header text in the calendar reads e.g. "May 2026".
        header_text = f"{month_name} {target.year}"
        for _ in range(18):  # generous safety bound (~1.5 years forward)
            try:
                hdr = page.locator('[role="dialog"]').last.locator(
                    f'h2:has-text("{header_text}"), div:has-text("{header_text}")'
                )
                if hdr.count() > 0:
                    break
            except Exception:
                pass
            try:
                page.locator('[role="dialog"]').get_by_role(
                    "button", name=re.compile(r"next month", re.I)
                ).first.click(timeout=2000)
                page.wait_for_timeout(200)
            except Exception:
                break
        day_btn = page.locator('[role="dialog"]').get_by_role(
            "button", name=re.compile(re.escape(day_aria), re.I)
        )
        day_btn.first.click(timeout=10000)
        page.wait_for_timeout(500)
    except Exception as err:
        raise RuntimeError(f"Could not set Date via calendar: {err}")

    # --- Time via typeahead ---
    ti = page.locator('input[name="timepicker"]').first
    try:
        # Use force=True in case the calendar popup tail is still being torn down.
        ti.click(force=True, timeout=10000)
        page.wait_for_timeout(800)
        results = page.locator(
            '.artdeco-typeahead__results-list:not([data-count="0"])'
        )
        results.first.wait_for(state="visible", timeout=8000)
        target_li = results.first.locator(f'li:has-text("{time_str}")')
        target_li.first.scroll_into_view_if_needed(timeout=3000)
        target_li.first.click(timeout=5000)
        page.wait_for_timeout(400)
    except Exception as err:
        raise RuntimeError(f"Could not set Time via typeahead: {err}")

    actual_date = di.input_value()
    actual_time = ti.input_value()
    date_str = f"{target.month}/{target.day}/{target.year}"
    if actual_date != date_str:
        logger.warning("⚠️ Date didn't stick: wanted=%s actual=%s", date_str, actual_date)
    if actual_time != time_str:
        logger.warning("⚠️ Time didn't stick: wanted=%s actual=%s", time_str, actual_time)

    actual_date = di.input_value()
    actual_time = ti.input_value()
    if actual_date != date_str:
        logger.warning("⚠️ Date didn't stick: wanted=%s actual=%s", date_str, actual_date)
    if actual_time != time_str:
        logger.warning("⚠️ Time didn't stick: wanted=%s actual=%s", time_str, actual_time)


def _click_schedule_next(page: Page) -> None:
    """Click Next in the Schedule dialog (returns to composer with schedule attached)."""
    # 'Next' button at bottom-right of the Schedule dialog; visible text 'Next'.
    try:
        page.locator('[role="dialog"] button:has-text("Next")').first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Next' in Schedule dialog: {err}")


def _click_final_schedule(page: Page) -> None:
    """Click the final 'Schedule' button in the composer (live mode only).

    After Schedule-Next, the composer's primary action button reads exactly
    "Schedule". The small clock icon next to it has aria-label "Schedule
    post" — we must target the primary by EXACT accessible name to avoid
    re-opening the schedule sub-dialog by mistake.
    """
    try:
        page.locator('[role="dialog"]').get_by_role(
            "button", name="Schedule", exact=True
        ).first.click(timeout=10000)
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
        discard = page.locator('[role="dialog"]').get_by_role(
            "button", name=re.compile(r"^discard$", re.I)
        )
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
    """Click the composer's 'More' button to reveal the secondary actions row.

    The first row of composer icons shows Photo / Video / Event / +More; the
    'Add a document' button only appears after expanding 'More'.
    """
    try:
        page.locator('[role="dialog"]').get_by_role(
            "button", name=re.compile(r"^more$", re.I)
        ).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'More' on the composer: {err}")


def _click_add_a_document(page: Page) -> None:
    """Click 'Add a document' from the expanded composer actions row.

    After 'More', the secondary tray exposes Document among the icons; the
    visible label reads exactly 'Add a document'.
    """
    try:
        page.locator('[role="dialog"]').get_by_role(
            "button", name=re.compile(r"add a document", re.I)
        ).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Add a document': {err}")


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
            page.locator('[role="dialog"]').get_by_role(
                "button", name=re.compile(r"choose file", re.I)
            ).first.click(timeout=5000)
        fc_info.value.set_files(str(pdf_path))
    except Exception as err:
        raise RuntimeError(f"Could not push PDF to 'Share a document' dialog: {err}")


def _wait_for_pdf_upload(page: Page, *, timeout_ms: int = 180000) -> None:
    """Wait for the Share-a-document dialog's PDF processing to complete.

    The 'Done' button is disabled until LinkedIn finishes processing the PDF
    (the dialog shows a thin progress bar under the file row). We poll the
    Done button until clickable; bail with a clear error on timeout.
    """
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline:
        try:
            done_btn = page.locator('[role="dialog"]').get_by_role(
                "button", name=re.compile(r"^done$", re.I)
            ).first
            if done_btn.count():
                disabled = done_btn.get_attribute("disabled")
                aria_dis = done_btn.get_attribute("aria-disabled")
                if not disabled and (aria_dis is None or aria_dis.lower() == "false"):
                    return
        except Exception:
            pass
        page.wait_for_timeout(1000)
    raise RuntimeError("PDF processing did not finish within the timeout window.")


def _fill_document_title(page: Page, doc_title: str) -> None:
    """Fill the 'Document title' input in the Share-a-document dialog.

    Selector list defensive against LI rollouts: textbox role + several
    placeholder/aria fallbacks. The dialog itself scopes the search so a
    rogue caption editor on the page can't win.
    """
    if not doc_title:
        raise RuntimeError("Empty document title — refusing to submit.")
    candidates = (
        '[role="dialog"] input[name*="title" i]',
        '[role="dialog"] input[aria-label*="title" i]',
        '[role="dialog"] input[placeholder*="title" i]',
        '[role="dialog"] input[type="text"]',
    )
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count():
                loc.wait_for(state="visible", timeout=3000)
                loc.fill(doc_title)
                return
        except Exception:
            continue
    raise RuntimeError("Could not locate the Document title input.")


def _click_document_done(page: Page) -> None:
    """Click 'Done' in the Share-a-document dialog → returns to composer with PDF attached."""
    try:
        page.locator('[role="dialog"]').get_by_role(
            "button", name=re.compile(r"^done$", re.I)
        ).first.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click 'Done' on the document dialog: {err}")


def _click_start_a_post(page: Page) -> None:
    """Click the feed's 'Start a post' share box to open the composer.

    The carousel route doesn't use the Photo button — it needs a clean
    empty composer to access the secondary actions row via 'More' → 'Add
    a document'. LI's share box exposes a button with accessible name
    matching 'Start a post' (case-insensitive).
    """
    candidates = (
        re.compile(r"start a post", re.I),
        re.compile(r"create a post", re.I),
    )
    for pattern in candidates:
        try:
            btn = page.get_by_role("button", name=pattern).first
            if btn.count():
                btn.click(timeout=5000)
                return
        except Exception:
            continue
    raise RuntimeError("Could not click 'Start a post' on the LinkedIn feed.")


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
    _click_schedule_next(page)
    page.wait_for_timeout(1500)

    pre_shot = out_dir / f"{day_label}-live-pre.png"
    try:
        page.screenshot(path=str(pre_shot), full_page=False)
    except Exception:
        pass

    composer_locator = page.locator(
        '[role="dialog"]:has(div[role="textbox"][contenteditable="true"])'
    )
    pre_count = composer_locator.count()

    _click_final_schedule(page)

    deadline = page.evaluate("() => Date.now()") + 20000
    while page.evaluate("() => Date.now()") < deadline:
        if composer_locator.count() < pre_count:
            break
        page.wait_for_timeout(400)
    else:
        shot = out_dir / f"{day_label}-live-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(
            f"Composer did not close within 20s after clicking Schedule — "
            f"post likely NOT scheduled. See {shot}"
        )

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
    _wait_for_pdf_upload(page)
    _fill_document_title(page, doc.doc_title)
    page.wait_for_timeout(400)
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
            except (FileNotFoundError, RuntimeError, PWTimeoutError) as err:
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
