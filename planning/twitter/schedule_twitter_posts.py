"""Schedule next week's Twitter (X) content via the native /home composer.

Reads rows where ``Work in Progress TW`` is checked, then for each in-scope
day drives the X composer at ``https://x.com/home`` to schedule a single-image
post at 15:00 local. There is no thread/carousel handling here (the
``clone_to_other_platforms`` step has already collapsed Sunday into a single
illustration + canonical caption).

This is a planner, not a bot. No likes, comments, follows, or DMs are
automated. The script only places pre-written, already-illustrated content
into the X native scheduler.

CLI mirrors ``instagram.schedule_instagram_posts``:

    python -m twitter.schedule_twitter_posts \
        [--week-start YYYY-MM-DD]
        [--date YYYYMMDD]
        [--all-wip]              # schedule every WIP-TW row, no date filter
        [--dry-run | --live]
        [--force]                # ignore link TW idempotency
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
from planning.twitter.twitter_session import (  # noqa: E402
    LoginRequiredError,
    TwitterSession,
    configure_logger,
    load_notion_token,
    load_twitter_config,
)
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    init_notion_client,
    query_rows_by_filter,
    retrieve_page,
    set_field,
)
from reporting.notion.notion_update import format_database_id  # noqa: E402

logger = logging.getLogger("twitter_schedule")

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


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
    illustration_tw_ids: list[str]
    text_tw: str
    existing_post_url: Optional[str]

    @property
    def day_title(self) -> str:
        return date_to_day_title(self.day)


@dataclass
class PostPayload:
    image_path: Path
    caption: str


# ---------- Notion query / payload resolution ----------

def fetch_wip_tw_rows(notion, db_id: str, ed_cols: dict, days: Optional[list[date]]) -> list[ScheduleRow]:
    """Fetch WIP-TW rows. If ``days`` is None, returns every WIP-TW row
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
                    illustration_tw_ids=[rel["id"] for rel in illust_rels],
                    text_tw=text_val,
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
    """Resolve <folder>/<name>.png. Accepts a name with or without extension."""
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
    """Build the (image path, caption) for the day's 15:00 X post."""
    illust_cols = cfg["illustration_columns"]
    folder = cfg["illustrations_folder"]
    if not row.illustration_tw_ids:
        raise RuntimeError(f"{row.day_title}: illustration TW is empty.")
    fname = _illustration_filename(notion, row.illustration_tw_ids[0], illust_cols)
    return PostPayload(
        image_path=_resolve_image_path(folder, fname),
        caption=row.text_tw,
    )


# ---------- X composer helpers ----------

def _dismiss_blocking_modals(page: Page) -> bool:
    """X sometimes pops onboarding / 'try premium' modals. Scope dismissals
    to ``[role="dialog"]`` so we don't accidentally click the side-rail
    'Close' button or similar always-visible chrome.
    """
    dismissed = False
    dialog = page.locator('[role="dialog"]')
    if not dialog.count():
        return False
    for name_re in (r"^not now$", r"^skip for now$", r"^maybe later$", r"^dismiss$", r"^close$"):
        try:
            btn = dialog.first.get_by_role("button", name=re.compile(name_re, re.I))
            if btn.count():
                btn.first.click(timeout=2500)
                page.wait_for_timeout(400)
                dismissed = True
                logger.info("ℹ️ Dismissed dialog via %r button.", name_re)
                break
        except Exception:
            pass
    return dismissed


def _click_compose_area(page: Page) -> None:
    """Open a clean composer modal via the side-rail 'Post' button.

    X's side-rail button has the stable testid ``SideNav_NewTweet_Button``.
    Clicking it opens a full-screen modal composer whose textarea is
    ``[data-testid="tweetTextarea_0"]`` inside ``[role="dialog"]``. Preferred
    over the inline composer because the inline one can carry stale draft
    state between sessions.
    """
    candidates = [
        '[data-testid="SideNav_NewTweet_Button"]',
        'a[data-testid="SideNav_NewTweet_Button"]',
        '[aria-label="Post" i][role="button"]',
    ]
    for sel in candidates:
        try:
            loc = page.locator(sel).first
            if loc.count():
                loc.click(timeout=5000)
                page.wait_for_timeout(900)
                # Confirm a dialog opened with a textarea inside.
                ta_in_dialog = page.locator(
                    '[role="dialog"] [data-testid="tweetTextarea_0"]'
                ).count()
                if ta_in_dialog:
                    logger.debug("📝 Opened composer modal via %s", sel)
                    return
        except Exception:
            continue

    # Fallback: try the inline textarea directly.
    try:
        ta = page.locator('[data-testid="tweetTextarea_0"]').first
        if ta.count():
            ta.scroll_into_view_if_needed(timeout=2000)
            ta.click(timeout=4000)
            page.wait_for_timeout(500)
            logger.debug("📝 Clicked inline composer area (fallback).")
            return
    except Exception:
        pass

    raise RuntimeError("Could not open the X compose area.")


def _type_caption(page: Page, caption: str) -> None:
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
    logger.debug("📝 Caption typed (%d chars).", len(caption))


def _upload_image(page: Page, path: Path) -> None:
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


def _click_schedule_toolbar(page: Page) -> None:
    """Click the calendar-clock icon in the composer toolbar (tooltip 'Schedule')."""
    candidates = [
        lambda: page.locator('button[data-testid="scheduleOption"]'),
        lambda: page.locator('[aria-label="Schedule post"]'),
        lambda: page.get_by_role("button", name=re.compile(r"^schedule(\spost)?$", re.I)),
    ]
    for find in candidates:
        try:
            loc = find().first
            if loc.count():
                loc.click(timeout=5000)
                page.wait_for_timeout(800)
                logger.debug("📅 Opened Schedule modal.")
                return
        except Exception:
            continue
    raise RuntimeError("Could not click Schedule toolbar button.")


def _set_schedule_modal(page: Page, target: date, hour: int, minute: int) -> None:
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


def _click_confirm_in_modal(page: Page) -> None:
    """Click the bottom-right 'Confirm' in the Schedule modal."""
    candidates = [
        lambda: page.locator('[data-testid="scheduledConfirmationPrimaryAction"]'),
        lambda: page.get_by_role("button", name=re.compile(r"^confirm$", re.I)),
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


def _click_final_schedule_action(page: Page) -> None:
    """The composer's primary action button — after Confirm in the schedule
    modal — reads 'Schedule' instead of 'Post'. Click it.
    """
    candidates = [
        lambda: page.locator('[data-testid="tweetButton"]'),
        lambda: page.locator('button[data-testid="tweetButtonInline"]'),
        lambda: page.get_by_role("button", name=re.compile(r"^schedule$", re.I)),
    ]
    for find in candidates:
        try:
            loc = find().last
            if loc.count():
                loc.scroll_into_view_if_needed(timeout=2000)
                loc.click(timeout=6000)
                page.wait_for_timeout(700)
                logger.debug("🚀 Final Schedule action clicked.")
                return
        except Exception:
            continue
    raise RuntimeError("Could not click the final Schedule action button.")


def _wait_composer_clears(page: Page, timeout_ms: int = 20000) -> bool:
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


def _cancel_composer(page: Page) -> None:
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
    for name_re in (r"^discard$", r"^discard changes$"):
        try:
            btn = page.get_by_role("button", name=re.compile(name_re, re.I))
            if btn.count():
                btn.first.click(timeout=2000)
                page.wait_for_timeout(400)
        except Exception:
            pass


def return_to_home(page: Page, feed_url: str) -> None:
    """Hard-refresh between days so the composer is in a clean state."""
    try:
        page.goto(feed_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        _dismiss_blocking_modals(page)
        page.wait_for_timeout(400)
    except Exception as err:
        logger.warning("⚠️ Could not return to home: %s", err)


# ---------- High-level per-day driver ----------

def schedule_post(
    session: TwitterSession,
    cfg: dict,
    row: ScheduleRow,
    payload: PostPayload,
    *,
    dry_run: bool,
) -> str:
    """Open compose → caption → upload image → Schedule modal → Confirm →
    final Schedule.
    """
    page = session.page
    label = row.day_title

    _click_compose_area(page)
    _type_caption(page, payload.caption)
    _upload_image(page, payload.image_path)
    _click_schedule_toolbar(page)
    _set_schedule_modal(
        page, row.day, cfg["post_hour_local"], cfg["post_minute_local"]
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "twitter"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-post-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info(
            "✅ DRY-RUN %s: composer + schedule modal ready, screenshot → %s",
            label, shot,
        )
        # Cancel the schedule modal AND the composer to leave clean state.
        for name_re in (r"^cancel$", r"^close$"):
            try:
                btn = page.get_by_role("button", name=re.compile(name_re, re.I))
                if btn.count():
                    btn.first.click(timeout=2000)
                    page.wait_for_timeout(400)
                    break
            except Exception:
                pass
        _cancel_composer(page)
        return "post:DRY-OK"

    _click_confirm_in_modal(page)
    _click_final_schedule_action(page)
    if not _wait_composer_clears(page, timeout_ms=25000):
        shot = out_dir / f"{label}-post-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"Composer did not clear — see {shot}")
    page.wait_for_timeout(1200)
    logger.info("✅ LIVE %s post scheduled on X", label)
    return "post:LIVE"


# ---------- Main ----------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule X (Twitter) content via /home composer.")
    parser.add_argument("--week-start", type=str, default=None,
                        help="Monday of the target week (YYYY-MM-DD). Default: next Monday.")
    parser.add_argument("--date", type=str, default=None,
                        help="Single-day mode (YYYYMMDD or YYYY-MM-DD). Overrides --week-start.")
    parser.add_argument("--all-wip", action="store_true",
                        help="Schedule every WIP-TW row in the editorial DB, no date filter "
                             "(supports multi-week planning runs).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Walk the flow up to Confirm; do NOT submit.")
    mode.add_argument("--live", action="store_true", help="Actually click Confirm + Schedule.")
    parser.add_argument("--force", action="store_true", help="Schedule even if link TW is already populated.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> tuple[int, list[dict]]:
    args = parse_args()
    configure_logger("twitter_schedule", debug=args.debug)
    cfg = load_twitter_config()

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
        logger.info("🎯 All-WIP mode: ignoring date filter, scheduling every WIP-TW row.")
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
    rows = fetch_wip_tw_rows(notion, db_id, cfg["editorial_columns"], target_days)
    if not rows:
        logger.warning("⚠️ No WIP-TW rows in target range. Nothing to do.")
        return 0, []

    logger.info("📋 %d in-scope row(s):", len(rows))
    for r in rows:
        logger.info("   - %s (page=%s, link TW=%s)",
                    r.day_title, r.page_id, r.existing_post_url or "(empty)")

    if not args.force:
        before = len(rows)
        rows = [r for r in rows if not r.existing_post_url]
        if len(rows) != before:
            logger.info(
                "⏭️  Skipped %d row(s) whose link TW is already populated (use --force to override).",
                before - len(rows),
            )
    if not rows:
        logger.info("ℹ️ Nothing left to schedule after dedup. Done.")
        return 0, []

    # Pre-resolve payloads BEFORE launching Chrome — fail fast on missing files.
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
    with TwitterSession(cfg) as session:
        try:
            session.goto_with_login_check(cfg["feed_url"])
        except LoginRequiredError as err:
            logger.error("❌ %s", err)
            for row, _ in plans:
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
            return 4, results
        session.page.wait_for_timeout(3500)
        _dismiss_blocking_modals(session.page)
        session.page.wait_for_timeout(400)

        for row, payload in plans:
            return_to_home(session.page, cfg["feed_url"])
            try:
                status = schedule_post(session, cfg, row, payload, dry_run=dry_run)
            except (RuntimeError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-error")
                logger.error("❌ %s post failed: %s (screenshot %s)", row.day_title, err, shot)
                _cancel_composer(session.page)
                return_to_home(session.page, cfg["feed_url"])
                statuses.append(f"{row.day_title}: post:FAIL({err})")
                results.append({"day": row.day_title, "status": "FAIL", "detail": f"{err} (screenshot {shot})"})
                continue

            statuses.append(f"{row.day_title}: {status}")
            if dry_run:
                results.append({"day": row.day_title, "status": "DRY", "detail": status})
            else:
                results.append({"day": row.day_title, "status": "LIVE", "detail": status})

            # Untick WIP TW only on a fully-successful LIVE day.
            if not dry_run and "LIVE" in status:
                try:
                    set_field(
                        notion, row.page_id, "wip_checkbox", False,
                        cfg["editorial_columns"], "checkbox",
                    )
                    logger.info("☑️ %s: WIP-TW unticked in Notion", row.day_title)
                except Exception as err:
                    logger.warning(
                        "⚠️ %s: scheduled OK but failed to untick WIP-TW: %s",
                        row.day_title, err,
                    )

    logger.info("══════════ Summary ══════════")
    for s in statuses:
        logger.info("   %s", s)
    failed = [r for r in results if r["status"] in ("FAIL", "LOGIN-REQUIRED")]
    return (0 if not failed else 11), results


if __name__ == "__main__":
    raise SystemExit(main()[0])
