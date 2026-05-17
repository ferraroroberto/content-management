"""Clone next week's Instagram editorial plan to Threads, Twitter, and Substack.

Reads rows from the editorial DB where ``Work in Progress IG`` is checked, then
mirrors the Instagram plan onto each per-platform tab of the same DB:

* Non-thread days: copy ``illustration IG`` + ``text IG`` + ``repost IG`` to
  the matching ``illustration <P>`` / ``text <P>`` / ``repost <P>`` fields
  for each platform.
* Sunday thread days (``thread IG`` = true): the IG-side carries a 10-image
  carousel and a template caption; TW/TH/SB still post a single image, so the
  source is derived:
      - illustration = first item in the related ``post IG`` page's
        ``illustration`` relation (the order Notion returns is the order
        the user added them to the thread).
      - caption = canonical first-publication ``text IG`` of THAT illustration
        (follow ``publishIG`` → earliest editorial row's ``text IG``;
        fallback ``text IG to copy`` formula). Same rule the LinkedIn
        scheduler uses.
  In addition, the Sunday IG row itself is back-filled when missing:
  ``illustration IG`` ← derived first thread image (so the downstream Meta
  planner can read it as the day's "first" image), and ``text IG`` ←
  ``instagram.sunday_template_text`` from config (the canonical template
  "Ten visuals on personal development, management and leadership…").

Per-platform tweaks:

* Threads + Twitter: also set ``Work in Progress <P>`` = true (so the
  downstream per-platform scheduler picks them up next).
* Substack: never set WIP — Substack is posted day-by-day by a separate
  pipeline (see ``substack/daily_pipeline.py``), and a WIP tick here would
  confuse that flow.
* ``thread <P>`` is **never** replicated. The thread concept only exists on
  Instagram.

Idempotency: by default, a target platform row is left alone if it already
has either an illustration relation OR a non-empty caption — use ``--force``
to overwrite.

CLI:
    python -m instagram.clone_to_other_platforms \
        [--week-start YYYY-MM-DD]   # default: next Monday
        [--date YYYYMMDD]           # single-day mode
        [--dry-run | --live]        # default: dry-run
        [--force]                   # overwrite already-populated targets
        [--debug]
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.instagram.instagram_session import (  # noqa: E402
    configure_logger,
    load_clone_config,
    load_instagram_config,
    load_notion_token,
)
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    init_notion_client,
    query_rows_by_filter,
    retrieve_page,
)
from reporting.notion.notion_update import (  # noqa: E402
    format_database_id,
    prepare_notion_update,
)

logger = logging.getLogger("instagram_clone")


# ---------- Date helpers (copied from linkedin to avoid cross-package import) ----------

def next_monday(today: Optional[date] = None) -> date:
    """Return the next Monday strictly after `today` (or 7 days from Mon itself)."""
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
class IgRow:
    """Snapshot of one editorial row's IG side, as read from Notion."""

    page_id: str
    day: date
    illustration_ig_ids: list[str]
    text_ig: str
    repost_ig: bool
    thread_ig: bool
    post_ig_ids: list[str]
    raw_properties: dict  # for force-check on target columns

    @property
    def day_title(self) -> str:
        return date_to_day_title(self.day)


@dataclass
class CloneSource:
    """The resolved (illustration_id, caption) that gets written to TW/TH/SB.

    ``ig_row_illustration_id`` and ``ig_row_text`` are filled ONLY when the IG
    row itself needs a back-fill (thread Sundays whose illustration IG/text IG
    are currently empty). When they are None we leave the IG row alone.
    """

    illustration_id: str
    caption_text: str
    ig_row_illustration_id: Optional[str] = None
    ig_row_text: Optional[str] = None


# ---------- Notion query ----------

def fetch_wip_ig_rows(notion, db_id: str, ed_cols: dict, days: list[date]) -> list[IgRow]:
    """Fetch editorial rows where ``Work in Progress IG`` is checked and the
    row's title (day) is one of `days`."""
    wip_col = ed_cols["wip_checkbox"]
    title_col = ed_cols["title_day"]
    illust_col = ed_cols["illustration_rel"]
    text_col = ed_cols["caption_text"]
    repost_col = ed_cols["repost_checkbox"]
    thread_col = ed_cols["thread_checkbox"]
    post_col = ed_cols["post_rel"]

    rows: list[IgRow] = []
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
        for r in results:
            props = r.get("properties", {})
            illust_rels = props.get(illust_col, {}).get("relation", []) or []
            post_rels = props.get(post_col, {}).get("relation", []) or []
            text_rt = props.get(text_col, {}).get("rich_text", []) or []
            text_val = "".join(seg.get("plain_text", "") for seg in text_rt).strip()
            repost = bool(props.get(repost_col, {}).get("checkbox", False))
            thread = bool(props.get(thread_col, {}).get("checkbox", False))
            rows.append(
                IgRow(
                    page_id=r["id"],
                    day=d,
                    illustration_ig_ids=[rel["id"] for rel in illust_rels],
                    text_ig=text_val,
                    repost_ig=repost,
                    thread_ig=thread,
                    post_ig_ids=[rel["id"] for rel in post_rels],
                    raw_properties=props,
                )
            )
    return rows


# ---------- Source resolution ----------

def _canonical_caption_from_publish_ig(
    notion,
    illustration_page_id: str,
    illust_cols: dict,
    ed_cols: dict,
) -> str:
    """Follow an illustration's ``publishIG`` relation back to all editorial
    rows that published it, sort by day ascending, and return the earliest
    one's ``text IG`` (the canonical first-publication caption).

    Fallback: ``text IG to copy`` formula on the illustration page.

    Mirror of ``linkedin.schedule_linkedin_posts.fetch_illustration`` caption
    logic (kept local to avoid a cross-package import, since the LinkedIn
    module pulls in Playwright at import time).
    """
    page = retrieve_page(notion, illustration_page_id)
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
                logger.info(
                    "📝 canonical caption from publishIG row %s: %d chars", day_str, len(text)
                )
                return text

    fallback = str(get_field(page, "caption_fallback", illust_cols) or "").strip()
    if fallback:
        logger.warning(
            "⚠️ publishIG yielded no caption — falling back to '%s' formula (%d chars)",
            illust_cols["caption_fallback"], len(fallback),
        )
    return fallback


def _first_thread_illustration_id(notion, post_page_id: str, posts_cols: dict) -> str:
    """Read the posts-page ``illustration`` relation and return its first id."""
    page = retrieve_page(notion, post_page_id)
    col = posts_cols["illustration_rel"]
    rels = page.get("properties", {}).get(col, {}).get("relation", []) or []
    if not rels:
        raise RuntimeError(
            f"post page {post_page_id} has no '{col}' relations — cannot derive "
            "first thread illustration."
        )
    return rels[0]["id"]


def resolve_source(notion, cfg: dict, row: IgRow) -> CloneSource:
    """Compute the (illustration_id, caption) to write to TW/TH/SB.

    Sunday-thread rows also surface back-fills for the IG row itself
    (illustration IG ← derived; text IG ← sunday_template_text from config).
    """
    illust_cols = cfg["illustration_columns"]
    ed_cols = cfg["editorial_columns"]
    posts_cols = cfg["posts_columns"]
    template_text = cfg.get("sunday_template_text", "")

    if not row.thread_ig:
        # Regular day — IG row already carries everything we need.
        if not row.illustration_ig_ids:
            raise RuntimeError(
                f"{row.day_title}: non-thread day but illustration IG is empty."
            )
        if not row.text_ig:
            raise RuntimeError(
                f"{row.day_title}: non-thread day but text IG is empty."
            )
        return CloneSource(
            illustration_id=row.illustration_ig_ids[0],
            caption_text=row.text_ig,
        )

    # Thread day (Sunday).
    if not row.post_ig_ids:
        raise RuntimeError(
            f"{row.day_title}: thread IG is checked but post IG is empty — "
            "set the post relation first."
        )
    first_illust_id = _first_thread_illustration_id(
        notion, row.post_ig_ids[0], posts_cols
    )
    canonical = _canonical_caption_from_publish_ig(
        notion, first_illust_id, illust_cols, ed_cols
    )
    if not canonical:
        raise RuntimeError(
            f"{row.day_title}: could not resolve canonical caption for the "
            "first thread illustration."
        )

    ig_row_illust = None if row.illustration_ig_ids else first_illust_id
    ig_row_text = None if row.text_ig else (template_text or None)

    return CloneSource(
        illustration_id=first_illust_id,
        caption_text=canonical,
        ig_row_illustration_id=ig_row_illust,
        ig_row_text=ig_row_text,
    )


# ---------- Write phase ----------

def _target_already_populated(props: dict, target: dict) -> bool:
    """True if the target platform row already has illustration OR text set."""
    illust_col = target["illustration_rel"]
    text_col = target["caption_text"]
    illust_rels = props.get(illust_col, {}).get("relation", []) or []
    text_rt = props.get(text_col, {}).get("rich_text", []) or []
    has_text = any(seg.get("plain_text", "").strip() for seg in text_rt)
    return bool(illust_rels) or has_text


def apply_to_targets(
    notion,
    row: IgRow,
    source: CloneSource,
    ig_cfg: dict,
    clone_cfg: dict,
    *,
    dry_run: bool,
    force: bool,
) -> list[str]:
    """Write the cloned plan onto each platform target. Returns per-target status strings."""
    ed_cols = ig_cfg["editorial_columns"]
    statuses: list[str] = []

    # IG row back-fill (Sunday only; only when fields are currently empty).
    ig_updates: dict = {}
    if source.ig_row_illustration_id:
        ig_updates[ed_cols["illustration_rel"]] = prepare_notion_update(
            "relation", source.ig_row_illustration_id
        )
    if source.ig_row_text:
        ig_updates[ed_cols["caption_text"]] = prepare_notion_update(
            "rich_text", source.ig_row_text
        )
    if ig_updates:
        if dry_run:
            logger.info(
                "🟡 DRY-RUN %s IG back-fill: %s",
                row.day_title, sorted(ig_updates.keys()),
            )
        else:
            notion.pages.update(page_id=row.page_id, properties=ig_updates)
            logger.info(
                "✅ %s IG back-fill written: %s",
                row.day_title, sorted(ig_updates.keys()),
            )

    for target in clone_cfg["targets"]:
        name = target["name"]
        if not force and _target_already_populated(row.raw_properties, target):
            statuses.append(f"{name}:SKIP-populated")
            logger.info(
                "⏭️  %s/%s already has illustration/text — skipping (use --force to overwrite).",
                row.day_title, name,
            )
            continue

        payload: dict = {
            target["illustration_rel"]: prepare_notion_update(
                "relation", source.illustration_id
            ),
            target["caption_text"]: prepare_notion_update(
                "rich_text", source.caption_text
            ),
            target["repost_checkbox"]: prepare_notion_update(
                "checkbox", row.repost_ig
            ),
        }
        if target.get("set_wip"):
            payload[target["wip_checkbox"]] = prepare_notion_update("checkbox", True)

        if dry_run:
            logger.info(
                "🟡 DRY-RUN %s → %s: write %s",
                row.day_title, name, sorted(payload.keys()),
            )
            statuses.append(f"{name}:DRY-OK")
        else:
            notion.pages.update(page_id=row.page_id, properties=payload)
            logger.info(
                "✅ %s → %s written (%d fields)",
                row.day_title, name, len(payload),
            )
            statuses.append(f"{name}:WRITTEN")

    return statuses


# ---------- Main ----------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone IG editorial plan to Threads / Twitter / Substack."
    )
    parser.add_argument("--week-start", type=str, default=None,
                        help="Monday of the target week (YYYY-MM-DD). Default: next Monday.")
    parser.add_argument("--date", type=str, default=None,
                        help="Single-day mode (YYYYMMDD or YYYY-MM-DD). Overrides --week-start.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Log planned writes only.")
    mode.add_argument("--live", action="store_true",
                      help="Actually update Notion.")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite target rows that already have illustration/text set.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logger("instagram_clone", debug=args.debug)
    ig_cfg = load_instagram_config()
    clone_cfg = load_clone_config()

    if args.live:
        dry_run = False
    elif args.dry_run:
        dry_run = True
    else:
        dry_run = ig_cfg.get("dry_run_default", True)

    if args.date:
        d = parse_single_date(args.date)
        target_days = [d]
        logger.info("🎯 Single-day mode: %s", d.isoformat())
    else:
        monday = parse_week_start(args.week_start)
        target_days = [monday + timedelta(days=i) for i in range(7)]
        logger.info("🗓️  Target week: %s → %s",
                    target_days[0].isoformat(), target_days[-1].isoformat())
    logger.info("🛠  Mode: %s%s", "DRY-RUN" if dry_run else "LIVE",
                " --force" if args.force else "")

    notion = init_notion_client(load_notion_token())
    if notion is None:
        logger.error("❌ Could not initialize Notion client.")
        return 3

    db_id = format_database_id(ig_cfg["editorial_db_id"])
    rows = fetch_wip_ig_rows(notion, db_id, ig_cfg["editorial_columns"], target_days)
    if not rows:
        logger.warning("⚠️ No WIP-IG rows in target range. Nothing to do.")
        return 0

    logger.info("📋 %d in-scope WIP-IG row(s):", len(rows))
    for r in rows:
        logger.info(
            "   - %s  thread=%s  illust_ids=%d  text_len=%d  repost=%s",
            r.day_title, r.thread_ig, len(r.illustration_ig_ids), len(r.text_ig), r.repost_ig,
        )

    all_statuses: list[tuple[str, list[str]]] = []
    failures: list[str] = []
    for row in rows:
        try:
            source = resolve_source(notion, ig_cfg, row)
        except RuntimeError as err:
            logger.error("❌ %s: source resolution failed: %s", row.day_title, err)
            failures.append(f"{row.day_title}: {err}")
            continue
        try:
            statuses = apply_to_targets(
                notion, row, source, ig_cfg, clone_cfg,
                dry_run=dry_run, force=args.force,
            )
        except Exception as err:  # Notion API surface is broad
            logger.exception("❌ %s: write failed: %s", row.day_title, err)
            failures.append(f"{row.day_title}: write {err}")
            continue
        all_statuses.append((row.day_title, statuses))

    logger.info("══════════ Clone summary ══════════")
    for day_title, statuses in all_statuses:
        logger.info("   %s → %s", day_title, ", ".join(statuses))
    for f in failures:
        logger.error("   FAIL %s", f)
    return 0 if not failures else 11


if __name__ == "__main__":
    raise SystemExit(main())
