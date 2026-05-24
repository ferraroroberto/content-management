"""Schedule next week's Instagram + Facebook content via the Meta Business planner.

Reads rows where ``Work in Progress IG`` is checked, then for each in-scope
day drives Meta's content-calendar UI to schedule:

* A **Story** at 10:00 local (Facebook + Instagram, both default-checked) —
  always a single image (the day's first illustration).
* A **Feed Post** at 15:00 local — single image on regular days, or the full
  10-image carousel on Sunday-thread days (read from the ``post IG``
  relation's ``illustration`` field, in user-defined order).

This is a planner, not a bot. No likes, comments, follows, or DMs are
automated. The script only places pre-written, already-illustrated content
into Meta's native scheduler.

CLI mirrors ``linkedin.schedule_linkedin_posts``:

    python -m instagram.schedule_instagram_posts \
        [--week-start YYYY-MM-DD]
        [--date YYYYMMDD]
        [--all-wip]              # schedule every WIP-IG row, no date filter
        [--dry-run | --live]
        [--force]                # ignore link IG idempotency
        [--debug]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.instagram.instagram_session import (  # noqa: E402
    InstagramSession,
    LoginRequiredError,
    configure_logger,
    load_instagram_config,
    load_notion_token,
)
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    init_notion_client,
    query_rows_by_filter,
    retrieve_page,
    set_field,
)
from reporting.notion.notion_update import format_database_id  # noqa: E402

logger = logging.getLogger("instagram_schedule")


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


def fmt_time_12h(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{minute:02d} {suffix}"


# ---------- Row model ----------

@dataclass
class ScheduleRow:
    page_id: str
    day: date
    illustration_ig_ids: list[str]
    text_ig: str
    thread_ig: bool
    post_ig_ids: list[str]
    existing_post_url: Optional[str]

    @property
    def day_title(self) -> str:
        return date_to_day_title(self.day)


@dataclass
class PostPayload:
    """Resolved files + caption ready to feed to the Meta UI."""

    image_paths: list[Path] = field(default_factory=list)
    caption: str = ""

    @property
    def is_thread(self) -> bool:
        return len(self.image_paths) > 1


# ---------- Notion query / payload resolution ----------

def fetch_wip_ig_rows(notion, db_id: str, ed_cols: dict, days: Optional[list[date]]) -> list[ScheduleRow]:
    """Fetch WIP-IG rows. If ``days`` is None, returns every WIP-IG row
    (used by ``--all-wip`` mode); otherwise filters by title-equals per day."""
    wip_col = ed_cols["wip_checkbox"]
    title_col = ed_cols["title_day"]
    illust_col = ed_cols["illustration_rel"]
    text_col = ed_cols["caption_text"]
    thread_col = ed_cols["thread_checkbox"]
    post_col = ed_cols["post_rel"]
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
                logger.warning(
                    "⚠️  Skipping row %s: day title is empty / not YYYYMMDD. "
                    "Likely a stale scratch/template row — consider archiving "
                    "it in Notion (%s).",
                    r.get("id"), r.get("url") or "(no url)",
                )
                continue
            illust_rels = props.get(illust_col, {}).get("relation", []) or []
            post_rels = props.get(post_col, {}).get("relation", []) or []
            text_rt = props.get(text_col, {}).get("rich_text", []) or []
            text_val = "".join(seg.get("plain_text", "") for seg in text_rt).strip()
            thread = bool(props.get(thread_col, {}).get("checkbox", False))
            url_obj = props.get(post_url_col, {})
            existing_url = url_obj.get("url") if url_obj.get("type") == "url" else None
            rows.append(
                ScheduleRow(
                    page_id=r["id"],
                    day=row_day,
                    illustration_ig_ids=[rel["id"] for rel in illust_rels],
                    text_ig=text_val,
                    thread_ig=thread,
                    post_ig_ids=[rel["id"] for rel in post_rels],
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
    """Resolve <folder>/<name>.png. Accepts a name with or without extension.

    The illustration title in Notion is the bare filename stem; on disk the
    Instagram-format copies live as ``<stem>.png`` under
    ``archived_IGformat/``.
    """
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


def resolve_post_payload(notion, cfg: dict, row: ScheduleRow) -> PostPayload:
    """Build the (image paths, caption) for the day's 15:00 feed post."""
    illust_cols = cfg["illustration_columns"]
    posts_cols = cfg["posts_columns"]
    folder = cfg["illustrations_folder"]

    if row.thread_ig:
        if not row.post_ig_ids:
            raise RuntimeError(
                f"{row.day_title}: thread IG checked but post IG is empty."
            )
        post_page = retrieve_page(notion, row.post_ig_ids[0])
        illust_rels = (
            post_page.get("properties", {})
            .get(posts_cols["illustration_rel"], {})
            .get("relation", []) or []
        )
        if not illust_rels:
            raise RuntimeError(
                f"{row.day_title}: thread post has no illustration relations."
            )
        paths: list[Path] = []
        missing = 0
        for rel in illust_rels:
            fname = _illustration_filename(notion, rel["id"], illust_cols)
            try:
                paths.append(_resolve_image_path(folder, fname))
            except FileNotFoundError as err:
                missing += 1
                logger.warning("⚠️  %s: carousel illustration missing, skipping: %s", row.day_title, err)
        if missing:
            logger.info(
                "🖼️ %s: carousel built with %d/%d images (%d missing illustration(s) skipped).",
                row.day_title, len(paths), len(illust_rels), missing,
            )
        if len(paths) < 2:
            raise RuntimeError(
                f"{row.day_title}: carousel has only {len(paths)} surviving illustration(s) "
                f"(IG requires at least 2)."
            )
        return PostPayload(image_paths=paths, caption=row.text_ig)

    if not row.illustration_ig_ids:
        raise RuntimeError(f"{row.day_title}: illustration IG is empty.")
    fname = _illustration_filename(notion, row.illustration_ig_ids[0], illust_cols)
    return PostPayload(
        image_paths=[_resolve_image_path(folder, fname)],
        caption=row.text_ig,
    )


def resolve_story_payload(notion, cfg: dict, row: ScheduleRow) -> PostPayload:
    """Story is always 1 image — the day's first illustration (no caption)."""
    illust_cols = cfg["illustration_columns"]
    folder = cfg["illustrations_folder"]

    if row.illustration_ig_ids:
        first_id = row.illustration_ig_ids[0]
    elif row.post_ig_ids:
        # Sunday row whose illustration IG was never back-filled — fall back
        # to the post's first illustration.
        posts_cols = cfg["posts_columns"]
        post_page = retrieve_page(notion, row.post_ig_ids[0])
        rels = (
            post_page.get("properties", {})
            .get(posts_cols["illustration_rel"], {})
            .get("relation", []) or []
        )
        if not rels:
            raise RuntimeError(f"{row.day_title}: no illustration to use for story.")
        first_id = rels[0]["id"]
    else:
        raise RuntimeError(f"{row.day_title}: nothing to use for story.")
    fname = _illustration_filename(notion, first_id, illust_cols)
    return PostPayload(image_paths=[_resolve_image_path(folder, fname)])


# ---------- Meta planner UI helpers ----------

# The day-cell header in the Meta planner is e.g. "Mon 18". Windows strftime
# uses "%#d" rather than "%-d" for an unpadded day. Build the label with
# explicit indexing to stay cross-platform-safe.
_WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day_cell_label(d: date) -> str:
    return f"{_WEEKDAY_ABBR[d.weekday()]} {d.day}"


# JS helper: walk up from the day-header text node until we find an ancestor
# div that itself contains a "Schedule" button. That ancestor IS the day's
# calendar column, which we can then hover and from which we can click
# specific buttons.
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


def dismiss_meta_verified_modal(page: Page) -> bool:
    """First-load Meta planner pops a 'Get Meta Verified' upsell modal that
    intercepts every pointer event (data-surface=GeoIllustrationModal). The
    'Not now' button dismisses it. Returns True if it was dismissed.
    """
    try:
        not_now = page.get_by_role("button", name=re.compile(r"^not now$", re.I))
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
            "button", name=re.compile(r"^close$", re.I)
        )
        if close.count():
            close.first.click(timeout=2000)
            page.wait_for_timeout(500)
            logger.info("ℹ️ Closed blocking dialog via Close button.")
            return True
    except Exception:
        pass
    return False


def _open_day_schedule_menu(page: Page, d: date, action: str) -> None:
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
    label = _day_cell_label(d)
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
    try:
        schedule_btn.click(timeout=8000)
    except Exception as err:
        raise RuntimeError(f"Could not click Schedule button on {label}: {err}")

    page.wait_for_timeout(500)
    # Menu pops up (image 06 / 10): "Schedule post", "Schedule story", etc.
    try:
        page.get_by_role(
            "menuitem", name=re.compile(rf"^{re.escape(action)}$", re.I)
        ).first.click(timeout=8000)
    except Exception:
        try:
            page.get_by_text(
                re.compile(rf"^{re.escape(action)}$", re.I)
            ).first.click(timeout=8000)
        except Exception as err:
            raise RuntimeError(f"Could not click menu item '{action}': {err}")
    page.wait_for_timeout(1000)


_ADD_MEDIA_BTN_SELECTOR = (
    'div[role="button"]:has-text("Add photo/video"), '
    'button:has-text("Add photo/video"), '
    'div[role="button"]:has-text("Añadir foto/vídeo"), '
    'button:has-text("Añadir foto/vídeo"), '
    'div[role="button"]:has-text("Añadir foto"), '
    'button:has-text("Añadir foto"), '
    '[data-surface*="upload_button"]'
)

# Some Meta planner builds open an intermediate dialog instead of a native
# file chooser when "Add photo/video" is clicked — the file picker only fires
# after a second click on an "Upload from computer" affordance inside that
# dialog. We accept EN + ES wordings to survive locale flips (see issue #28).
_UPLOAD_FROM_COMPUTER_SELECTOR = (
    'div[role="dialog"] div[role="button"]:has-text("Upload from computer"), '
    'div[role="dialog"] button:has-text("Upload from computer"), '
    'div[role="dialog"] div[role="button"]:has-text("Upload"), '
    'div[role="dialog"] button:has-text("Upload"), '
    'div[role="dialog"] div[role="button"]:has-text("Subir desde el ordenador"), '
    'div[role="dialog"] button:has-text("Subir desde el ordenador"), '
    'div[role="dialog"] div[role="button"]:has-text("Subir"), '
    'div[role="dialog"] button:has-text("Subir")'
)


def _dump_upload_debug(page: Page, tag: str) -> Path:
    """Save a screenshot + log the visible buttons in any open dialog.

    Called from ``_upload_files`` when an upload strategy fails so the next
    triage doesn't need a live debug session. The returned path is included
    in the raised exception message.
    """
    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "instagram"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    shot = out_dir / f"{ts}-upload-debug-{tag}.png"
    try:
        page.screenshot(path=str(shot), full_page=False)
    except Exception as err:
        logger.debug("Could not save upload debug screenshot: %s", err)
        return shot
    try:
        # Dump up to 12 visible button-like elements in the latest dialog so
        # the log line tells us what affordances Meta is offering at the
        # moment of failure.
        visible_buttons = page.evaluate(
            r"""
            () => {
                const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
                const root = dialogs.length ? dialogs[dialogs.length - 1] : document;
                const buttons = Array.from(root.querySelectorAll('div[role="button"], button'));
                return buttons
                    .filter(b => {
                        const r = b.getBoundingClientRect();
                        return r.width > 0 && r.height > 0;
                    })
                    .slice(0, 12)
                    .map(b => (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 60));
            }
            """
        )
        logger.debug(
            "🔎 Upload-debug (%s) — visible buttons in latest dialog: %s",
            tag, visible_buttons,
        )
    except Exception as err:
        logger.debug("Could not enumerate dialog buttons: %s", err)
    return shot


def _upload_files(page: Page, paths: list[Path], *, is_video: bool = False) -> None:
    """Get files into the Meta composer.

    Meta's "Add photo/video" affordance has shipped in two shapes:

    1. **Direct file chooser** (story path, older post path): clicking it
       opens the OS file picker; ``page.expect_file_chooser`` intercepts
       the FileChooser event and we push files via ``set_files``.
    2. **Intermediate dialog** (current post path on most accounts, issue
       #28): clicking it opens a media dialog whose "Upload from computer"
       button is what actually triggers the file chooser.

    We try (1) first; if no FileChooser appears within 8 s, we look for the
    "Upload from computer" button inside the newly-opened dialog and try
    (2). If neither produces a FileChooser, we fall back to any attached
    ``input[type=file]`` (8 s wait). Each leg logs which one ran, and on
    final failure we save a debug screenshot + dump the visible dialog
    buttons so the next regression triages from artefacts, not a live DOM
    session (AC #5).

    ``is_video=True`` narrows Leg 0's selector to
    ``input[type=file][accept*="video"]`` so we cannot accidentally bind
    files to an image input when Meta's post composer mounts both
    (issue #37 — duplicated attach when the video input is filled AND the
    image affordance re-opens its own chooser).
    """
    file_paths = [str(p) for p in paths]

    add_btn = page.locator(_ADD_MEDIA_BTN_SELECTOR).first
    try:
        add_btn.wait_for(state="visible", timeout=12000)
    except Exception as err:
        raise RuntimeError(f"'Add photo/video' button never appeared: {err}")

    # Diagnostic: dump the add-button's outer HTML so we can see whether
    # Meta wired a click handler or expects a pre-mounted input[type=file]
    # behind it. Cheap; only logged at DEBUG.
    try:
        outer = add_btn.evaluate("el => el.outerHTML")
        logger.debug("🔎 Add-media button outerHTML (first 600 chars): %s",
                     (outer or "")[:600])
    except Exception as err:
        logger.debug("Could not snapshot add-media button HTML: %s", err)

    # --- Leg 0: pre-mounted hidden input[type=file] (Meta planner often uses
    #            a label-wrapped <input> where the visible button is decorative).
    # When uploading a video, narrow to the video-accept input so we do not
    # accidentally fill an image input that Meta also mounts in the same
    # composer (issue #37).
    leg0_selector = (
        'input[type="file"][accept*="video"]' if is_video else 'input[type="file"]'
    )
    pre_inp = page.locator(leg0_selector).last
    leg0_n_inputs = pre_inp.count() if pre_inp else 0
    if leg0_n_inputs > 0:
        # 15 s — set_input_files on a multi-MB video can blow past 4 s while
        # Meta is still accepting the file. A premature TimeoutError there
        # used to throw us into Leg 1, which opened the file chooser and
        # attached the same clip a SECOND time → issue #37's duplicate.
        try:
            pre_inp.set_input_files(file_paths, timeout=15000)
            logger.info(
                "📤 Leg 0 (%s, %d input(s) matched): %d file(s) sent via "
                "pre-mounted input[type=file] (no Add-photo/video click needed).",
                leg0_selector, leg0_n_inputs, len(file_paths),
            )
            page.wait_for_timeout(3000 + 1500 * len(paths))
            return
        except Exception as err:
            logger.warning(
                "⚠️ Leg 0 (%s) raised after %d input(s) matched: %s — "
                "probing composer before fallthrough (issue #37 race guard).",
                leg0_selector, leg0_n_inputs, err,
            )
            # Race guard: the input may have ACCEPTED the file before
            # Playwright timed out. Falling through to Leg 1 in that case
            # double-attaches. Probe the composer; if any media is already
            # attached, treat Leg 0 as a success.
            try:
                already_attached = _count_composer_media(page)
            except Exception as probe_err:
                logger.debug("Race-guard probe failed: %s", probe_err)
                already_attached = 0
            if already_attached > 0:
                logger.info(
                    "📤 Leg 0 (%s): file appears attached despite timeout "
                    "(probe sees %d media tile(s)) — not falling through.",
                    leg0_selector, already_attached,
                )
                page.wait_for_timeout(3000 + 1500 * len(paths))
                return

    # --- Leg 1: click 'Add photo/video' and hope for a direct file chooser ---
    # Meta's post composer sometimes leaves the button without a hydrated
    # click handler for a short window after the sub-modal closes (issue #28).
    # Settle, click, and on miss retry once via a JS-native click — that
    # bypasses any React handler-binding race a Playwright dispatch hits.
    page.wait_for_timeout(1500)

    def _try_click_for_chooser(click_fn) -> Optional[object]:
        try:
            with page.expect_file_chooser(timeout=6000) as fc_info:
                click_fn()
            return fc_info.value
        except Exception as err:
            logger.debug("Add-photo/video click → no FileChooser: %s", err)
            return None

    def _playwright_click() -> None:
        try:
            add_btn.click(timeout=5000)
        except Exception:
            # Pointer-events block: bypass via force.
            add_btn.click(force=True, timeout=5000)

    def _js_click() -> None:
        # Dispatched directly on the DOM node — bypasses Playwright's overlay
        # / pointer-events checks and any half-bound React handler.
        add_btn.evaluate("el => el.click()")

    leg1_chooser = _try_click_for_chooser(_playwright_click)
    if leg1_chooser is None:
        # Re-find: the previous click may have changed the DOM even if no
        # chooser fired. Then JS-click the freshly resolved element.
        page.wait_for_timeout(600)
        add_btn = page.locator(_ADD_MEDIA_BTN_SELECTOR).first
        leg1_chooser = _try_click_for_chooser(_js_click)

    if leg1_chooser is not None:
        leg1_chooser.set_files(file_paths)
        logger.info("📤 Leg 1: %d file(s) sent via direct FileChooser.", len(file_paths))
        page.wait_for_timeout(3000 + 1500 * len(paths))
        return

    # --- Leg 2: intermediate dialog — click 'Upload from computer' inside it ---
    leg2_chooser = None
    upload_btn = page.locator(_UPLOAD_FROM_COMPUTER_SELECTOR).first
    try:
        upload_btn.wait_for(state="visible", timeout=5000)
    except Exception as err:
        logger.debug("Leg 2 ('Upload from computer' button not present): %s", err)
    else:
        try:
            with page.expect_file_chooser(timeout=8000) as fc_info:
                try:
                    upload_btn.click(timeout=5000)
                except Exception:
                    upload_btn.click(force=True, timeout=5000)
            leg2_chooser = fc_info.value
        except Exception as err:
            logger.debug("Leg 2 (FileChooser after Upload-from-computer): %s", err)

    if leg2_chooser is not None:
        leg2_chooser.set_files(file_paths)
        logger.debug(
            "📤 Leg 2: %d file(s) sent via FileChooser after intermediate dialog.",
            len(file_paths),
        )
        page.wait_for_timeout(3000 + 1500 * len(paths))
        return

    # --- Leg 3: an input[type=file] is already attached somewhere ---
    inp = page.locator('input[type="file"]').last
    try:
        inp.wait_for(state="attached", timeout=8000)
        inp.set_input_files(file_paths)
        logger.debug("📤 Leg 3: %d file(s) sent via attached input[type=file].", len(file_paths))
        page.wait_for_timeout(3000 + 1500 * len(paths))
        return
    except Exception as err:
        logger.debug("Leg 3 (attached input[type=file]): %s", err)
        shot = _dump_upload_debug(page, "all-legs-failed")
        raise RuntimeError(
            f"Could not upload files {paths!r}: all three upload strategies "
            f"failed (direct FileChooser, intermediate dialog → 'Upload from "
            f"computer', attached input[type=file]). Last error from leg 3: "
            f"{err}. Debug screenshot: {shot}"
        )


# JS probe that counts attached media tiles inside the post composer.
#
# Verified live (see E:\\tmp\\probe_meta_composer.py results, May 2026):
#
# * Meta's planner does NOT wrap the post composer in [role="dialog"];
#   the composer renders inline in the planner shell. So we scan the
#   whole document — there is no other "Remove video" / "Remove photo"
#   affordance on the planner page to conflict with this.
# * The trash icon on each attached tile has NO aria-label. The visible
#   text inside its <div role="button"> is exactly "Remove video"
#   (followed by a zero-width space + newline). Same shape for the
#   image flow ("Remove photo") and the ES build ("Eliminar vídeo").
# * Counting <video> elements is WRONG: the right-side "Instagram Feed
#   preview" pane mounts its own <video>, so videos.length = N_attached
#   + 1. Using it as a fallback over-deleted in the live probe.
#
# Therefore the canonical signal is: count <div role="button"> whose
# innerText starts with Remove/Delete/Eliminar/Quitar followed by the
# media noun. Exactly one per attached tile.
_MEDIA_COUNT_JS = r"""
() => {
    const labelRe = /^(remove|delete|eliminar|quitar)\s+(video|photo|media|attachment|file|image|v[ií]deo|foto)\b/i;
    const buttons = Array.from(document.querySelectorAll('[role="button"], button'));
    return buttons.filter(b => labelRe.test(((b.innerText || '') + '').trim())).length;
}
"""


def _count_composer_media(page: Page) -> int:
    """Best-effort count of attached media tiles in the open composer.

    Returns 0 on probe failure so callers can decide whether 0 means
    "empty" or "stale selector"; never raises.
    """
    try:
        return int(page.evaluate(_MEDIA_COUNT_JS) or 0)
    except Exception as err:
        logger.debug("composer media-count probe raised: %s", err)
        return 0


def _delete_extra_media_tiles(page: Page, target: int) -> int:
    """Dismiss attached-media tiles until the composer shows ``target`` tiles.

    LIFO — the duplicate from a re-upload race is always the newer tile.
    Verified live (probe_meta_composer.py): clicking the trash button on
    a tile removes it immediately; Meta does NOT open a confirmation
    popover. Stops as soon as ``count <= target`` is reached, or aborts
    if a click makes no progress.

    Issue #37 — designed to recover from a duplicate-attach so the live
    run does not deadlock waiting for the user to intervene in the
    browser.
    """
    js_delete_last = r"""
    () => {
        const labelRe = /^(remove|delete|eliminar|quitar)\s+(video|photo|media|attachment|file|image|v[ií]deo|foto)\b/i;
        const buttons = Array.from(document.querySelectorAll('[role="button"], button'))
            .filter(b => labelRe.test(((b.innerText || '') + '').trim()));
        if (!buttons.length) return false;
        const last = buttons[buttons.length - 1];
        last.scrollIntoView({block: 'center'});
        last.click();
        return true;
    }
    """
    count = _count_composer_media(page)
    if count <= target:
        return count
    logger.warning(
        "⚠️ composer shows %d attached media (target %d) — attempting "
        "LIFO delete recovery (issue #37 duplicate-attach).",
        count, target,
    )
    safety = 0
    while count > target and safety < (count - target) + 3:
        safety += 1
        try:
            clicked = bool(page.evaluate(js_delete_last))
        except Exception as err:
            logger.warning("⚠️ Delete-last click failed: %s — aborting recovery.", err)
            break
        if not clicked:
            logger.warning("⚠️ No delete button found in composer — aborting recovery.")
            break
        page.wait_for_timeout(700)
        new_count = _count_composer_media(page)
        if new_count >= count:
            logger.warning("⚠️ Delete made no progress (%d→%d) — aborting recovery.", count, new_count)
            break
        logger.info("🧹 Dedupe: %d → %d tile(s).", count, new_count)
        count = new_count
    return count


def _assert_composer_media_count(page: Page, expected: int) -> None:
    """Verify Meta's composer shows exactly ``expected`` attached media tiles.

    Raises ``RuntimeError`` on a confirmed mismatch (>0 but != expected).
    A 0 result is logged but does not raise — the probe is best-effort
    and 0 may mean "selector drift" rather than "really empty". Callers
    that need to GUARANTEE attached media should pair this with
    ``_count_composer_media`` themselves.
    """
    count = _count_composer_media(page)
    logger.info("🔢 composer media-count probe: %d (expected %d).", count, expected)
    if count == 0:
        logger.warning(
            "⚠️ media-count probe returned 0 — selector may be stale. "
            "Not blocking; if Schedule fails to enable, this probe is "
            "the first thing to fix (issue #37 reference)."
        )
        return
    if count != expected:
        raise RuntimeError(
            f"IG composer reports {count} attached media after upload "
            f"(expected {expected}) — likely duplicated attach (issue #37). "
            f"Aborting before Schedule deadlock."
        )


def _check_meta_video_error_toast(page: Page) -> Optional[str]:
    """Scan the composer for Meta's video-rejection error toasts.

    Returns the matched toast text (truncated) if one is visible, else
    None. Callers raise so the existing FAIL handler screenshots and
    records the row. Known strings (Meta A/Bs these — substring scan over
    role=alert / role=status nodes):

    - "more than 1 minute"
    - "only post one video"
    - "video is too long"
    """
    patterns = [
        "more than 1 minute",
        "only post one video",
        "can only post one video",
        "can only upload one video",
        "video is too long",
    ]
    try:
        toast_text = page.evaluate(
            """
            (patterns) => {
                const nodes = Array.from(document.querySelectorAll(
                    '[role="alert"],[role="status"]'
                ));
                for (const n of nodes) {
                    const t = (n.innerText || '').toLowerCase();
                    for (const p of patterns) {
                        if (t.includes(p)) return t.trim().slice(0, 240);
                    }
                }
                return null;
            }
            """,
            patterns,
        )
    except Exception as err:
        logger.debug("Meta-error-toast probe failed: %s", err)
        return None
    return toast_text


# Meta renders date and time controls as ``div[role="button"]`` with the
# formatted text inside, NOT as <input> elements. Click opens a calendar /
# typeahead — same pattern LinkedIn uses. Regexes are intentionally NOT
# anchored: Meta wraps the text inside a button that also contains an
# icon/glyph, so the full text node content includes more than just the
# date/time string.
_DATE_BUTTON_RE = re.compile(r"[A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}")
_TIME_BUTTON_RE = re.compile(r"\d{1,2}:\d{2}\s*[AP]M")


def _fill_post_text(page: Page, caption: str) -> None:
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


def _ensure_set_date_toggle_on(page: Page) -> None:
    """The post composer needs the 'Set date and time' toggle on (image 13).

    Confirmed via DOM probe: it's an ``<input type="checkbox"
    aria-label="Set date and time">`` whose ``value`` reads "false"/"true".
    Real-checkbox inputs in React + Meta's design system don't always
    respond to a programmatic ``check()`` on the input itself (the visible
    UI is a separate clickable element). Strategy:
      1. If the underlying checkbox already reports value="true", noop.
      2. Otherwise click the closest visible label / styled-toggle parent
         via the checkbox's parent chain (just .click() on the input often
         works because Meta wires onChange on the input).
    """
    cb = page.locator('input[type="checkbox"][aria-label="Set date and time"]').first
    try:
        cb.wait_for(state="attached", timeout=4000)
    except Exception:
        return
    # Already on?
    try:
        if (cb.input_value(timeout=2000) or "").lower() == "true":
            return
    except Exception:
        pass
    # Try clicking the checkbox directly (Meta wires the change handler on it).
    try:
        cb.click(force=True, timeout=4000)
        page.wait_for_timeout(800)
        if (cb.input_value(timeout=1000) or "").lower() == "true":
            page.wait_for_timeout(800)
            return
    except Exception:
        pass
    # Fallback: click the visible label text.
    try:
        page.get_by_text(re.compile(r"^set date and time$", re.I)).first.click(timeout=4000)
        page.wait_for_timeout(1200)
    except Exception:
        pass
    # Final scroll so the (now-mounted) date/time row is in viewport.
    try:
        page.get_by_text(re.compile(r"^schedule$", re.I)).last.scroll_into_view_if_needed(timeout=2000)
        page.wait_for_timeout(400)
    except Exception:
        pass


def _click_calendar_day(page: Page, target: date) -> None:
    """In an open Meta calendar popup, navigate to the right month if needed
    and click the target day. Meta's calendar uses ``aria-label`` like
    ``Monday, May 18, 2026`` on day buttons.
    """
    day_aria = target.strftime("%A, %B ") + f"{target.day}, {target.year}"
    month_year = target.strftime("%B %Y")  # e.g. "May 2026"

    # Walk forward via "Next month" until the header matches. The calendar
    # popup is a freshly-mounted floating layer; scope to the page.
    for _ in range(18):  # ~1.5 years forward bound
        if page.locator(f'div:has-text("{month_year}")').count() > 0:
            break
        try:
            next_btn = page.get_by_role(
                "button", name=re.compile(r"next month", re.I)
            )
            if next_btn.count():
                next_btn.first.click(timeout=2000)
                page.wait_for_timeout(250)
                continue
        except Exception:
            break
        break

    day_btn = page.get_by_role("button", name=re.compile(re.escape(day_aria), re.I))
    if day_btn.count() == 0:
        # Fallback: aria-label may be a date-only string like "May 18, 2026"
        short = target.strftime("%B ") + f"{target.day}, {target.year}"
        day_btn = page.get_by_role("button", name=re.compile(re.escape(short), re.I))
    if day_btn.count() == 0:
        raise RuntimeError(f"Could not find calendar day for {day_aria}")
    try:
        day_btn.first.click(timeout=5000)
    except Exception as err:
        raise RuntimeError(f"Could not click calendar day {day_aria}: {err}")
    page.wait_for_timeout(400)


def _click_time_slot(page: Page, hour: int, minute: int) -> None:
    """In an open time-picker dropdown, click the slot for HH:MM (12h)."""
    slot_str = fmt_time_12h(hour, minute)
    # Meta's time dropdown items are role="option" or simple <li>/<div>.
    candidates = [
        lambda: page.get_by_role("option", name=re.compile(rf"^{re.escape(slot_str)}$", re.I)),
        lambda: page.locator(f'li:has-text("{slot_str}")'),
        lambda: page.locator(f'div[role="button"]:has-text("{slot_str}")'),
        lambda: page.get_by_text(slot_str, exact=True),
    ]
    for find in candidates:
        try:
            loc = find()
            if loc.count():
                loc.first.scroll_into_view_if_needed(timeout=2000)
                loc.first.click(timeout=4000)
                page.wait_for_timeout(300)
                return
        except Exception:
            continue
    # Last resort: type the time into whatever input is now focused.
    try:
        page.keyboard.type(slot_str, delay=8)
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
    except Exception as err:
        raise RuntimeError(f"Could not pick time slot {slot_str}: {err}")


def _click_action_button(page: Page, name: str, exact: bool = True) -> None:
    """Click a top-level composer action button (Save / Schedule / Cancel)."""
    pattern = re.compile(rf"^{re.escape(name)}$", re.I) if exact else re.compile(name, re.I)
    try:
        page.get_by_role("button", name=pattern).last.click(timeout=10000)
    except Exception as err:
        raise RuntimeError(f"Could not click action button '{name}': {err}")


def wait_action_button_enabled(
    page: Page, name: str, *, exact: bool = True, timeout_ms: int = 90000,
    poll_ms: int = 500,
) -> None:
    """Wait until the composer's action button is no longer ``aria-disabled``.

    Meta's composer keeps Schedule / Share now ``aria-disabled="true"`` while
    the uploaded media is still transcoding server-side. Videos in particular
    can take well over the 6 s fixed sleep the drivers used to wait — once
    they're past that, the button enables and the click succeeds instantly.
    Raises ``RuntimeError`` if the button stays disabled past ``timeout_ms``.
    """
    pattern = re.compile(rf"^{re.escape(name)}$", re.I) if exact else re.compile(name, re.I)
    btn = page.get_by_role("button", name=pattern).last
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    last_state = None
    while page.evaluate("() => Date.now()") < deadline:
        try:
            disabled = btn.get_attribute("aria-disabled")
        except Exception:
            disabled = None
        if disabled in (None, "false"):
            return
        if disabled != last_state:
            logger.debug("⏳ '%s' button still aria-disabled=%s, polling…", name, disabled)
            last_state = disabled
        page.wait_for_timeout(poll_ms)
    raise RuntimeError(
        f"Action button '{name}' stayed aria-disabled after {timeout_ms} ms — "
        f"media upload likely did not finish."
    )


def _wait_composer_closes(page: Page, planner_url: str, *, timeout_ms: int = 25000) -> bool:
    """Wait until the URL leaves /composer/ (meaning the composer finished
    and Meta navigated back to the planner)."""
    deadline = page.evaluate("() => Date.now()") + timeout_ms
    while page.evaluate("() => Date.now()") < deadline:
        url = (page.url or "").lower()
        if "/composer" not in url and "/composer/?" not in url:
            return True
        page.wait_for_timeout(500)
    return False


def _cancel_composer(page: Page) -> None:
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
        discard = page.get_by_role("button", name=re.compile(r"^(discard|leave)$", re.I))
        if discard.count():
            discard.first.click(timeout=2000)
            page.wait_for_timeout(500)
    except Exception:
        pass


def return_to_planner(page: Page, planner_url: str) -> None:
    """Navigate explicitly back to the calendar view between days."""
    try:
        if "/composer" in (page.url or "").lower() or "/content_calendar" not in (page.url or "").lower():
            page.goto(planner_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            dismiss_meta_verified_modal(page)
            page.wait_for_timeout(500)
    except Exception as err:
        logger.warning("⚠️ Could not return to planner: %s", err)


def navigate_to_week(page: Page, target: date, *, max_clicks: int = 10) -> None:
    """Advance the week view forward (or back) until the target day's column
    is visible. The planner's chevron buttons are labelled 'Next week' /
    'Previous week' aria-wise; the visible label is just the chevron glyph.

    Strategy: try clicking 'Next week' until the target's column appears,
    then 'Previous week' if we overshot.
    """
    label = _day_cell_label(target)

    def col_present() -> bool:
        try:
            return page.evaluate(_FIND_COLUMN_JS, label) is not None
        except Exception:
            return False

    if col_present():
        return

    # Meta uses chevron-glyph text "Left" / "Right" as the button label
    # (probed via DOM); aria-label is empty.
    next_btn = page.get_by_role(
        "button", name=re.compile(r"^(next week|right)$", re.I)
    )
    prev_btn = page.get_by_role(
        "button", name=re.compile(r"^(previous week|left)$", re.I)
    )

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


# ---------- High-level per-action drivers ----------

def _dismiss_initial_schedule_submodal(page: Page) -> None:
    """When the Create story / Create post composer opens, Meta pops a
    'Schedule story' / 'Schedule post' SUB-modal with default date+time that
    are rarely what we want. Click its Cancel so we can drive the main
    composer's own date+time inputs instead.

    Both the sub-modal AND the main composer expose a Cancel button — so
    page-wide "first Cancel" can hit the wrong one. We locate Cancel via the
    sub-modal's heading ancestor by walking up the DOM from the heading until
    we find an ancestor that contains BOTH the heading AND a Cancel button.
    """
    title_loc = page.locator(
        'h1:has-text("Schedule story"), h1:has-text("Schedule post"), '
        'div[role="heading"]:has-text("Schedule story"), '
        'div[role="heading"]:has-text("Schedule post")'
    )
    appeared = False
    for _ in range(20):
        if title_loc.count():
            appeared = True
            break
        page.wait_for_timeout(200)

    if not appeared:
        logger.debug("No initial schedule sub-modal appeared — proceeding.")
        return

    # Walk up from the heading to find a panel that contains a Cancel button.
    # This guarantees we click the sub-modal's Cancel, not the main composer's.
    js = r"""
() => {
    const titles = [
        ...document.querySelectorAll('h1, [role="heading"], div'),
    ].filter(el => {
        const t = (el.innerText || '').trim();
        return t === 'Schedule story' || t === 'Schedule post';
    });
    if (!titles.length) return false;
    let node = titles[0];
    for (let depth = 0; depth < 30 && node; depth++) {
        const cancel = [...(node.querySelectorAll ? node.querySelectorAll('div[role="button"], button') : [])]
            .find(b => (b.innerText || '').trim() === 'Cancel');
        if (cancel) {
            cancel.click();
            return true;
        }
        node = node.parentElement;
    }
    return false;
}
"""
    clicked = False
    try:
        clicked = bool(page.evaluate(js))
    except Exception as err:
        logger.debug("Sub-modal Cancel via JS failed: %s", err)

    if not clicked:
        # Fallback: Escape (the sub-modal has no unsaved input so no discard
        # prompt fires).
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
    page.wait_for_timeout(700)

    # Confirm the sub-modal really went away.
    for _ in range(15):
        if title_loc.count() == 0:
            logger.debug("✅ Schedule sub-modal dismissed.")
            return
        page.wait_for_timeout(200)
    logger.warning("⚠️ Schedule sub-modal still present after dismissal attempt.")


def _probe_date_time_dom(page: Page) -> dict:
    """Return a snapshot of all candidate date/time controls in the page,
    used for debugging when the regex selectors miss.
    """
    return page.evaluate(r"""
() => {
    const dateRe = /[A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}/;
    const timeRe = /\d{1,2}:\d{2}\s*[AP]M/;
    const out = {dates: [], times: [], inputs: []};
    const all = document.querySelectorAll('input, [role="button"], [contenteditable], div, span');
    for (const el of all) {
        const txt = (el.innerText || '').trim();
        const val = (el.value || '').trim();
        const aria = el.getAttribute('aria-label') || '';
        if (el.tagName === 'INPUT') {
            out.inputs.push({
                tag: el.tagName,
                type: el.type,
                value: val,
                aria,
                placeholder: el.placeholder || '',
            });
            continue;
        }
        if (dateRe.test(txt) && txt.length < 60) {
            out.dates.push({tag: el.tagName, role: el.getAttribute('role'), text: txt, aria});
        }
        if (timeRe.test(txt) && txt.length < 30) {
            out.times.push({tag: el.tagName, role: el.getAttribute('role'), text: txt, aria});
        }
    }
    // Trim to 8 each
    out.dates = out.dates.slice(0, 8);
    out.times = out.times.slice(0, 8);
    out.inputs = out.inputs.slice(0, 12);
    return out;
}
""")


def _fill_input(page: Page, locator, value: str) -> None:
    """Click, clear, and type into a Meta composer text input."""
    locator.click(timeout=4000)
    page.wait_for_timeout(150)
    # Meta inputs honor select-all + type; .fill() sometimes leaves the old
    # value (Meta's React onChange handler is picky).
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    page.keyboard.type(value, delay=8)
    page.wait_for_timeout(150)


def _set_all_visible_date_time(
    page: Page,
    target: date,
    hour: int,
    minute: int,
) -> None:
    """Update every visible date+time input set on the open composer.

    Confirmed via DOM probe: Meta uses real <input type="text"> elements
    (not button-styled divs). The shape per "set" is:

    - 1 date input: ``placeholder="mm/dd/yyyy"`` — fill with "MM/DD/YYYY".
    - 3 time inputs (split): ``aria-label="hours"``, ``aria-label="minutes"``,
      ``aria-label="meridiem"`` — fill with "H" (1-12), "MM", "AM" or "PM".

    The story composer surfaces 2 sets (Facebook + Instagram); the post
    composer surfaces 1 set (only after 'Set date and time' is on).
    """
    try:
        page.get_by_text(re.compile(r"^schedule$", re.I)).last.scroll_into_view_if_needed(timeout=2000)
        page.wait_for_timeout(500)
    except Exception:
        pass

    # Date inputs.
    date_inputs = page.locator('input[placeholder="mm/dd/yyyy"]')
    n_dates = date_inputs.count()
    date_str = f"{target.month:02d}/{target.day:02d}/{target.year}"
    for i in range(n_dates):
        try:
            _fill_input(page, date_inputs.nth(i), date_str)
            page.keyboard.press("Tab")  # confirm value
            page.wait_for_timeout(200)
        except Exception as err:
            logger.warning("⚠️ Could not set date input #%d: %s", i, err)

    # Time inputs come in triplets (hours/minutes/meridiem) per platform.
    hours = page.locator('input[aria-label="hours"]')
    minutes = page.locator('input[aria-label="minutes"]')
    meridiems = page.locator('input[aria-label="meridiem"]')
    n_hours = min(hours.count(), minutes.count(), meridiems.count())
    h12 = hour % 12 or 12
    mer = "AM" if hour < 12 else "PM"
    for i in range(n_hours):
        try:
            _fill_input(page, hours.nth(i), str(h12))
            _fill_input(page, minutes.nth(i), f"{minute:02d}")
            _fill_input(page, meridiems.nth(i), mer)
            page.keyboard.press("Tab")
            page.wait_for_timeout(200)
        except Exception as err:
            logger.warning("⚠️ Could not set time triplet #%d: %s", i, err)

    logger.info(
        "🕒 Updated %d date input(s) and %d time triplet(s) → %s %d:%02d %s",
        n_dates, n_hours, date_str, h12, minute, mer,
    )


def schedule_story(
    session: InstagramSession,
    cfg: dict,
    row: ScheduleRow,
    story: PostPayload,
    *,
    dry_run: bool,
) -> str:
    """Open the day's Schedule menu → Schedule story → close sub-modal →
    upload image → set date+time → Schedule.

    Both Facebook story and Instagram story are default-checked in the main
    composer (image 8); we leave the share-to selection alone.
    """
    page = session.page
    label = row.day_title
    _open_day_schedule_menu(page, row.day, "Schedule story")
    _dismiss_initial_schedule_submodal(page)
    _upload_files(page, story.image_paths)
    _set_all_visible_date_time(
        page, row.day, cfg["story_hour_local"], cfg["story_minute_local"]
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "instagram"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-story-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info("✅ DRY-RUN %s story: composer ready, screenshot → %s", label, shot)
        _cancel_composer(page)
        return "story:DRY-OK"

    # Story composer's primary action: 'Schedule' (greys out 'Share now').
    _click_action_button(page, "Schedule")
    if not _wait_composer_closes(page, cfg["feed_url"], timeout_ms=30000):
        shot = out_dir / f"{label}-story-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"Story composer did not close — see {shot}")
    page.wait_for_timeout(1500)
    logger.info("✅ LIVE %s story scheduled", label)
    return "story:LIVE"


def schedule_post(
    session: InstagramSession,
    cfg: dict,
    row: ScheduleRow,
    post: PostPayload,
    *,
    dry_run: bool,
) -> str:
    """Open the day's Schedule menu → Schedule post → close sub-modal →
    upload → caption → ensure 'Set date and time' on → set date/time →
    Schedule.
    """
    page = session.page
    label = row.day_title
    _open_day_schedule_menu(page, row.day, "Schedule post")
    _dismiss_initial_schedule_submodal(page)
    _upload_files(page, post.image_paths)
    _fill_post_text(page, post.caption)
    _ensure_set_date_toggle_on(page)
    _set_all_visible_date_time(
        page, row.day, cfg["post_hour_local"], cfg["post_minute_local"]
    )

    out_dir = Path(__file__).resolve().parent.parent.parent / "results" / "instagram"
    out_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        shot = out_dir / f"{label}-post-dryrun.png"
        page.screenshot(path=str(shot), full_page=False)
        logger.info(
            "✅ DRY-RUN %s post: %d image(s), %d-char caption — screenshot → %s",
            label, len(post.image_paths), len(post.caption), shot,
        )
        _cancel_composer(page)
        return "post:DRY-OK"

    _click_action_button(page, "Schedule")
    if not _wait_composer_closes(page, cfg["feed_url"], timeout_ms=30000):
        shot = out_dir / f"{label}-post-FAIL.png"
        page.screenshot(path=str(shot), full_page=False)
        raise RuntimeError(f"Post composer did not close — see {shot}")
    page.wait_for_timeout(1500)
    logger.info("✅ LIVE %s post scheduled (%d image(s))", label, len(post.image_paths))
    return "post:LIVE"


# ---------- Main ----------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schedule Instagram + Facebook content via the Meta planner.")
    parser.add_argument("--week-start", type=str, default=None,
                        help="Monday of the target week (YYYY-MM-DD). Default: next Monday.")
    parser.add_argument("--date", type=str, default=None,
                        help="Single-day mode (YYYYMMDD or YYYY-MM-DD). Overrides --week-start.")
    parser.add_argument("--all-wip", action="store_true",
                        help="Schedule every WIP-IG row in the editorial DB, no date filter "
                             "(supports multi-week planning runs).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Walk the flow up to Schedule; do NOT submit.")
    mode.add_argument("--live", action="store_true", help="Actually click Save/Schedule.")
    parser.add_argument("--force", action="store_true", help="Schedule even if link IG is already populated.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> tuple[int, list[dict]]:
    args = parse_args()
    configure_logger("instagram_schedule", debug=args.debug)
    cfg = load_instagram_config()

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
        logger.info("🎯 All-WIP mode: ignoring date filter, scheduling every WIP-IG row.")
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
    rows = fetch_wip_ig_rows(notion, db_id, cfg["editorial_columns"], target_days)
    if not rows:
        logger.warning("⚠️ No WIP-IG rows in target range. Nothing to do.")
        return 0, []

    logger.info("📋 %d in-scope row(s):", len(rows))
    for r in rows:
        logger.info("   - %s (page=%s, thread=%s, link IG=%s)",
                    r.day_title, r.page_id, r.thread_ig, r.existing_post_url or "(empty)")

    if not args.force:
        before = len(rows)
        rows = [r for r in rows if not r.existing_post_url]
        if len(rows) != before:
            logger.info(
                "⏭️  Skipped %d row(s) whose link IG is already populated (use --force to override).",
                before - len(rows),
            )
    if not rows:
        logger.info("ℹ️ Nothing left to schedule after dedup. Done.")
        return 0, []

    # Pre-resolve payloads BEFORE launching Chrome — fail fast on missing files.
    plans: list[tuple[ScheduleRow, PostPayload, PostPayload]] = []
    results: list[dict] = []
    for row in rows:
        try:
            story = resolve_story_payload(notion, cfg, row)
            post = resolve_post_payload(notion, cfg, row)
        except (RuntimeError, FileNotFoundError) as err:
            logger.error("❌ %s payload resolution failed: %s", row.day_title, err)
            results.append({"day": row.day_title, "status": "FAIL", "detail": f"payload resolution: {err}"})
            continue
        logger.info(
            "🖼️ %s: story=1 image, post=%d image(s), caption=%d chars",
            row.day_title, len(post.image_paths), len(post.caption),
        )
        plans.append((row, story, post))

    if not plans:
        logger.warning("⚠️ All rows failed payload resolution. Nothing to do.")
        return 11, results

    statuses: list[str] = []
    with InstagramSession(cfg) as session:
        try:
            session.goto_with_login_check(cfg["feed_url"])
        except LoginRequiredError as err:
            logger.error("❌ %s", err)
            for row, _, _ in plans:
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
            return 4, results
        # Let the planner SPA fully mount.
        session.page.wait_for_timeout(4500)
        # Dismiss any first-load upsell modal (e.g. 'Get Meta Verified').
        dismiss_meta_verified_modal(session.page)
        session.page.wait_for_timeout(500)

        for row, story, post in plans:
            day_statuses: list[str] = []
            return_to_planner(session.page, cfg["feed_url"])
            try:
                day_statuses.append(schedule_story(session, cfg, row, story, dry_run=dry_run))
            except (RuntimeError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-story-error")
                logger.error("❌ %s story failed: %s (screenshot %s)", row.day_title, err, shot)
                day_statuses.append(f"story:FAIL({err})")
                _cancel_composer(session.page)
                return_to_planner(session.page, cfg["feed_url"])
            return_to_planner(session.page, cfg["feed_url"])
            try:
                day_statuses.append(schedule_post(session, cfg, row, post, dry_run=dry_run))
            except (RuntimeError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-post-error")
                logger.error("❌ %s post failed: %s (screenshot %s)", row.day_title, err, shot)
                day_statuses.append(f"post:FAIL({err})")
                _cancel_composer(session.page)
                return_to_planner(session.page, cfg["feed_url"])

            line = f"{row.day_title}: " + ", ".join(day_statuses)
            statuses.append(line)

            all_live = all("LIVE" in s for s in day_statuses)
            any_fail = any("FAIL" in s for s in day_statuses)
            if dry_run:
                row_status = "DRY"
            elif any_fail:
                row_status = "FAIL"
            elif all_live:
                row_status = "LIVE"
            else:
                row_status = "PARTIAL"
            results.append({"day": row.day_title, "status": row_status, "detail": ", ".join(day_statuses)})

            # Untick WIP IG only on a fully-successful LIVE day (both story+post).
            if not dry_run and all_live:
                try:
                    set_field(
                        notion, row.page_id, "wip_checkbox", False,
                        cfg["editorial_columns"], "checkbox",
                    )
                    logger.info("☑️ %s: WIP-IG unticked in Notion", row.day_title)
                except Exception as err:
                    logger.warning(
                        "⚠️ %s: scheduled OK but failed to untick WIP-IG: %s",
                        row.day_title, err,
                    )

    logger.info("══════════ Summary ══════════")
    for s in statuses:
        logger.info("   %s", s)
    failed = [r for r in results if r["status"] in ("FAIL", "PARTIAL", "LOGIN-REQUIRED")]
    return (0 if not failed else 11), results


if __name__ == "__main__":
    raise SystemExit(main()[0])
