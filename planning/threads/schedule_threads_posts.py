"""Schedule next week's Threads content via the native New-thread composer.

Reads rows where ``work in progress TH`` is checked, then for each in-scope
day drives the Threads composer at
``https://www.threads.com/@ferraroroberto`` to schedule a single-image post
at 15:00 local. No carousel handling (the ``clone_to_other_platforms``
step has already collapsed Sunday into a single illustration + canonical
caption).

This is a planner, not a bot. No likes, comments, follows, or DMs are
automated. The script only places pre-written, already-illustrated content
into the Threads native scheduler.

CLI mirrors ``instagram.schedule_instagram_posts``:

    python -m threads.schedule_threads_posts \
        [--week-start YYYY-MM-DD]
        [--date YYYYMMDD]
        [--all-wip]              # schedule every WIP-TH row, no date filter
        [--dry-run | --live]
        [--force]
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
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.threads.threads_session import (  # noqa: E402
    LoginRequiredError,
    ThreadsSession,
    configure_logger,
    load_notion_token,
    load_threads_config,
)
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    init_notion_client,
    query_rows_by_filter,
    retrieve_page,
    set_field,
)
from reporting.notion.notion_update import format_database_id  # noqa: E402

logger = logging.getLogger("threads_schedule")


# ---------- Date helpers ----------

def next_monday(today: Optional[date] = None) -> date:
    today = today or date.today()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def parse_week_start(s: Optional[str]) -> date:
    if not s:
        return next_monday()
    return datetime.strptime(s, "%Y-%m-%d").date()


def parse_single_date(s: str) -> date:
    s = s.strip()
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").date()
    return datetime.strptime(s, "%Y%m%d").date()


def date_to_day_title(d: date) -> str:
    return d.strftime("%Y%m%d")


# ---------- Row model ----------

@dataclass
class ScheduleRow:
    page_id: str
    day: date
    illustration_th_ids: list[str]
    text_th: str
    existing_post_url: Optional[str]

    @property
    def day_title(self) -> str:
        return date_to_day_title(self.day)


@dataclass
class PostPayload:
    image_path: Path
    caption: str


# ---------- Notion query / payload resolution ----------

def fetch_wip_th_rows(notion, db_id: str, ed_cols: dict, days: Optional[list[date]]) -> list[ScheduleRow]:
    """Fetch WIP-TH rows. If ``days`` is None, returns every WIP-TH row
    (used by ``--all-wip`` mode); otherwise filters by title-equals per day."""
    wip_col = ed_cols["wip_checkbox"]
    title_col = ed_cols["title_day"]
    illust_col = ed_cols["illustration_rel"]
    text_col = ed_cols["caption_text"]
    post_url_col = ed_cols["post_url"]

    rows: list[ScheduleRow] = []

    def _row_day(r: dict) -> Optional[date]:
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
            illust_rels = props.get(illust_col, {}).get("relation", []) or []
            text_rt = props.get(text_col, {}).get("rich_text", []) or []
            text_val = "".join(seg.get("plain_text", "") for seg in text_rt).strip()
            url_obj = props.get(post_url_col, {})
            existing_url = url_obj.get("url") if url_obj.get("type") == "url" else None
            rows.append(
                ScheduleRow(
                    page_id=r["id"],
                    day=row_day,
                    illustration_th_ids=[rel["id"] for rel in illust_rels],
                    text_th=text_val,
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


def _resolve_image_path(folder: str, image_filename: str) -> Path:
    if not image_filename:
        raise FileNotFoundError("Illustration row has no filename.")
    first = str(image_filename).split(",")[0].strip()
    if first and not first.lower().endswith(".png"):
        first = f"{first}.png"
    candidate = Path(folder) / first
    if not candidate.exists():
        raise FileNotFoundError(f"Illustration not found: {candidate}")
    return candidate


def _illustration_filename(notion, illustration_page_id: str, illust_cols: dict) -> str:
    page = retrieve_page(notion, illustration_page_id)
    name = get_field(page, "image_filename", illust_cols) or ""
    return str(name).strip()


def resolve_payload(notion, cfg: dict, row: ScheduleRow) -> PostPayload:
    illust_cols = cfg["illustration_columns"]
    folder = cfg["illustrations_folder"]
    if not row.illustration_th_ids:
        raise RuntimeError(f"{row.day_title}: illustration TH is empty.")
    fname = _illustration_filename(notion, row.illustration_th_ids[0], illust_cols)
    return PostPayload(
        image_path=_resolve_image_path(folder, fname),
        caption=row.text_th,
    )


# ---------- Threads composer helpers ----------

def _open_composer(page: Page) -> None:
    """Click the inline 'What's new?' on the profile to open the New thread
    modal. The same placeholder text exists inside the modal, so disambiguate
    by checking the modal's heading after opening.
    """
    candidates = [
        # Profile-row click target. Several variants observed across Threads
        # builds — try in order.
        'text="What\'s new?"',
        '[aria-label="Create new thread" i]',
        'div[role="button"]:has-text("What\'s new?")',
    ]
    opened = False
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count():
                loc.click(timeout=4000)
                page.wait_for_timeout(800)
                if page.locator('[role="dialog"]').count() or page.locator(
                    'text="New thread"'
                ).count():
                    logger.debug("📝 Opened composer via %s", sel)
                    opened = True
                    break
        except Exception:
            continue
    if not opened:
        raise RuntimeError("Could not open the Threads composer modal.")


def _type_caption(page: Page, caption: str) -> None:
    """Type into the composer's What's-new contenteditable inside the dialog."""
    if not caption:
        return
    dialog = page.locator('[role="dialog"]').last
    # The textarea inside the dialog is a contenteditable, anchored by the
    # same "What's new?" placeholder. Anchor by role.
    ta_candidates = [
        dialog.get_by_role("textbox", name=re.compile(r"what's new", re.I)).first,
        dialog.locator('div[contenteditable="true"]').first,
        dialog.locator('[role="textbox"]').first,
    ]
    for ta in ta_candidates:
        try:
            if ta.count():
                ta.click(timeout=4000)
                page.wait_for_timeout(150)
                page.keyboard.type(caption, delay=4)
                page.wait_for_timeout(400)
                logger.debug("📝 Caption typed (%d chars).", len(caption))
                return
        except Exception:
            continue
    raise RuntimeError("Could not find the Threads caption textbox.")


def _upload_image(page: Page, path: Path) -> None:
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
            dialog.get_by_role("button", name=re.compile(r"^(attach media|add media|attach image|attach photo)$", re.I)).first,
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


def _open_three_dots_menu(page: Page) -> None:
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
        if page.get_by_text(
            re.compile(r"^schedule(…|\.\.\.)$", re.I)
        ).count():
            logger.debug("⋯ Opened more-options menu.")
            return
        page.wait_for_timeout(150)
    raise RuntimeError("Clicked the 3-dots, but the Schedule menuitem never appeared.")


def _click_schedule_menuitem(page: Page) -> None:
    """Click 'Schedule…' from the 3-dots menu (Threads uses U+2026 ellipsis)."""
    candidates = [
        lambda: page.get_by_role(
            "menuitem", name=re.compile(r"^schedule(…|\.\.\.)?$", re.I)
        ),
        lambda: page.get_by_text(re.compile(r"^schedule(…|\.\.\.)$", re.I)),
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


_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _navigate_calendar_month(page: Page, target: date) -> None:
    """Click the calendar's '>' until the visible header is `<Month> <Year>`."""
    target_header = f"{_MONTH_NAMES[target.month - 1]} {target.year}"
    for _ in range(36):  # 3 years forward max
        try:
            if page.get_by_text(target_header, exact=True).count():
                return
        except Exception:
            pass
        next_btn_candidates = [
            page.get_by_role("button", name=re.compile(r"^next month$", re.I)).first,
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
    // We want the BLACK one (current-month, not today). Pick lowest lightness.
    cells.sort((a, b) => a.spanLightness - b.spanLightness);
    cells[0].cell.click();
    return {
        ok: true,
        n_cells: cells.length,
        picked_lightness: cells[0].spanLightness,
        all_lightness: cells.map(c => c.spanLightness),
    };
}
"""


def _click_calendar_day(page: Page, target: date) -> None:
    """Click the day-cell in the open calendar popup.

    Calendar days are `<div role="gridcell">` wrappers whose accessible name
    is empty; the digit lives in nested ``<span>``s. Cells from the previous
    and next months are also present but greyed out. JS picks the brightest
    cell with matching text (current-month).
    """
    header_text = f"{_MONTH_NAMES[target.month - 1]} {target.year}"
    day_text = str(target.day)
    try:
        res = page.evaluate(_CLICK_CAL_DAY_JS, {"headerText": header_text, "dayText": day_text})
    except Exception as err:
        raise RuntimeError(f"Calendar day JS picker failed: {err}")
    if not res or not res.get("ok"):
        raise RuntimeError(f"Could not click calendar day {day_text}: {res}")
    page.wait_for_timeout(300)
    logger.debug("📅 Clicked day %s in calendar.", day_text)


def _set_calendar_time(page: Page, hour: int, minute: int) -> None:
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


def _click_calendar_done(page: Page) -> None:
    """Click the calendar popup's bottom-right 'Done'."""
    candidates = [
        lambda: page.get_by_role("button", name=re.compile(r"^done$", re.I)).first,
        lambda: page.get_by_text(re.compile(r"^done$", re.I)).first,
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


def _click_final_schedule_action(page: Page) -> None:
    """After Done, the composer's bottom-right 'Post' is replaced by
    'Schedule'. Click it."""
    dialog = page.locator('[role="dialog"]').last
    candidates = [
        lambda: dialog.get_by_role("button", name=re.compile(r"^schedule$", re.I)).first,
        lambda: page.get_by_role("button", name=re.compile(r"^schedule$", re.I)).last,
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


def _wait_composer_closes(page: Page, timeout_ms: int = 20000) -> bool:
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


def _cancel_composer(page: Page) -> None:
    """Best-effort: cancel any open Threads composer."""
    for name_re in (r"^cancel$", r"^discard$"):
        try:
            btn = page.get_by_role("button", name=re.compile(name_re, re.I))
            if btn.count():
                btn.first.click(timeout=2000)
                page.wait_for_timeout(400)
                # The discard-confirmation pops up after Cancel.
                continue
        except Exception:
            pass


def return_to_profile(page: Page, feed_url: str) -> None:
    """Hard-refresh between days."""
    try:
        page.goto(feed_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
    except Exception as err:
        logger.warning("⚠️ Could not return to profile: %s", err)


# ---------- High-level per-day driver ----------

def schedule_post(
    session: ThreadsSession,
    cfg: dict,
    row: ScheduleRow,
    payload: PostPayload,
    *,
    dry_run: bool,
) -> str:
    page = session.page
    label = row.day_title

    _open_composer(page)
    _type_caption(page, payload.caption)
    _upload_image(page, payload.image_path)
    _open_three_dots_menu(page)
    _click_schedule_menuitem(page)
    _navigate_calendar_month(page, row.day)
    _click_calendar_day(page, row.day)
    _set_calendar_time(page, cfg["post_hour_local"], cfg["post_minute_local"])

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "threads"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-post-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info(
            "✅ DRY-RUN %s: calendar populated, screenshot → %s", label, shot
        )
        _cancel_composer(page)
        # Discard prompt after Cancel.
        for _ in range(3):
            try:
                btn = page.get_by_role("button", name=re.compile(r"^discard$", re.I))
                if btn.count():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                pass
        return "post:DRY-OK"

    _click_calendar_done(page)
    _click_final_schedule_action(page)
    if not _wait_composer_closes(page, timeout_ms=25000):
        shot = out_dir / f"{label}-post-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"Composer did not close — see {shot}")
    page.wait_for_timeout(1500)
    logger.info("✅ LIVE %s post scheduled on Threads", label)
    return "post:LIVE"


# ---------- Main ----------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule Threads content via /@profile composer.")
    parser.add_argument("--week-start", type=str, default=None,
                        help="Monday of the target week (YYYY-MM-DD). Default: next Monday.")
    parser.add_argument("--date", type=str, default=None,
                        help="Single-day mode (YYYYMMDD or YYYY-MM-DD). Overrides --week-start.")
    parser.add_argument("--all-wip", action="store_true",
                        help="Schedule every WIP-TH row in the editorial DB, no date filter "
                             "(supports multi-week planning runs).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Walk the flow up to Done; do NOT submit.")
    mode.add_argument("--live", action="store_true", help="Actually click Done + Schedule.")
    parser.add_argument("--force", action="store_true", help="Schedule even if link TH is already populated.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> tuple[int, list[dict]]:
    args = parse_args()
    configure_logger("threads_schedule", debug=args.debug)
    cfg = load_threads_config()

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
        logger.info("🎯 All-WIP mode: ignoring date filter, scheduling every WIP-TH row.")
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

    db_id = format_database_id(cfg["editorial_db_id"])
    rows = fetch_wip_th_rows(notion, db_id, cfg["editorial_columns"], target_days)
    if not rows:
        logger.warning("⚠️ No WIP-TH rows in target range. Nothing to do.")
        return 0, []

    logger.info("📋 %d in-scope row(s):", len(rows))
    for r in rows:
        logger.info("   - %s (page=%s, link TH=%s)",
                    r.day_title, r.page_id, r.existing_post_url or "(empty)")

    if not args.force:
        before = len(rows)
        rows = [r for r in rows if not r.existing_post_url]
        if len(rows) != before:
            logger.info(
                "⏭️  Skipped %d row(s) whose link TH is already populated (use --force to override).",
                before - len(rows),
            )
    if not rows:
        logger.info("ℹ️ Nothing left to schedule after dedup. Done.")
        return 0, []

    plans: list[tuple[ScheduleRow, PostPayload]] = []
    results: list[dict] = []
    for row in rows:
        try:
            payload = resolve_payload(notion, cfg, row)
        except (RuntimeError, FileNotFoundError) as err:
            logger.error("❌ %s payload resolution failed: %s", row.day_title, err)
            results.append({"day": row.day_title, "status": "FAIL", "detail": f"payload resolution: {err}"})
            continue
        logger.info(
            "🖼️ %s: image=%s, caption=%d chars",
            row.day_title, payload.image_path.name, len(payload.caption),
        )
        plans.append((row, payload))

    if not plans:
        logger.warning("⚠️ All rows failed payload resolution. Nothing to do.")
        return 11, results

    statuses: list[str] = []
    with ThreadsSession(cfg) as session:
        try:
            session.goto_with_login_check(cfg["feed_url"])
        except LoginRequiredError as err:
            logger.error("❌ %s", err)
            for row, _ in plans:
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
            return 4, results
        session.page.wait_for_timeout(3500)

        for row, payload in plans:
            return_to_profile(session.page, cfg["feed_url"])
            try:
                status = schedule_post(session, cfg, row, payload, dry_run=dry_run)
            except (RuntimeError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-error")
                logger.error("❌ %s post failed: %s (screenshot %s)", row.day_title, err, shot)
                _cancel_composer(session.page)
                return_to_profile(session.page, cfg["feed_url"])
                statuses.append(f"{row.day_title}: post:FAIL({err})")
                results.append({"day": row.day_title, "status": "FAIL", "detail": f"{err} (screenshot {shot})"})
                continue

            statuses.append(f"{row.day_title}: {status}")
            if dry_run:
                results.append({"day": row.day_title, "status": "DRY", "detail": status})
            else:
                results.append({"day": row.day_title, "status": "LIVE", "detail": status})

            if not dry_run and "LIVE" in status:
                try:
                    set_field(
                        notion, row.page_id, "wip_checkbox", False,
                        cfg["editorial_columns"], "checkbox",
                    )
                    logger.info("☑️ %s: WIP-TH unticked in Notion", row.day_title)
                except Exception as err:
                    logger.warning(
                        "⚠️ %s: scheduled OK but failed to untick WIP-TH: %s",
                        row.day_title, err,
                    )

    logger.info("══════════ Summary ══════════")
    for s in statuses:
        logger.info("   %s", s)
    failed = [r for r in results if r["status"] in ("FAIL", "LOGIN-REQUIRED")]
    return (0 if not failed else 11), results


if __name__ == "__main__":
    raise SystemExit(main()[0])
