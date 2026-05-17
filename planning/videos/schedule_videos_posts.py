"""Weekly cross-platform video orchestrator.

Schedules the weekly video clip across LinkedIn, Instagram, Twitter, and
Threads at 19:00 Europe/Madrid on the editorial row's date. Substack is
deliberately NOT in this orchestrator — it is posted by the daily Substack
pipeline as a branch on video days (no native scheduler).

Reads editorial rows where ``Work in Progress Video`` is checked, follows
each row's per-platform ``clip <P>(v)`` relations to the shared clip page,
and dispatches to one ``planning.videos.videos_<P>`` driver per platform.
The clip page is fetched once per row; its ``Text`` property feeds IG/TW/TH/SB
captions, and its page body feeds the LinkedIn long caption.

The ``Work in Progress Video`` checkbox is unticked **only** when all four
scheduled platforms succeed AND ``link SB(v)`` is populated by the daily
Substack pipeline. Re-runnable: a second invocation after Substack posts
will skip the already-scheduled four (idempotent via ``link <P>(v)``
presence) and just untick.

CLI:
    python -m planning.videos.schedule_videos_posts \\
        [--week-start YYYY-MM-DD]
        [--date YYYYMMDD]
        [--all-wip]             # every WIP-Video row, no date filter
        [--dry-run | --live]
        [--force]               # schedule even if link <P>(v) is set
        [--debug]
        [--skip-li] [--skip-ig] [--skip-tw] [--skip-th]
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning.videos.videos_session import (  # noqa: E402
    ClipPayload,
    configure_logger,
    first_clip_relation_id,
    load_clip_payload,
    load_notion_token,
    load_videos_config,
)
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    init_notion_client,
    query_rows_by_filter,
    set_field,
)
from reporting.notion.notion_update import format_database_id  # noqa: E402

logger = logging.getLogger("videos_schedule")

PLATFORMS_SCHEDULED = ("li", "ig", "tw", "th")  # SB is owned by the daily Substack pipeline.
# Platforms that don't have their own ``clip <P>(v)`` column on the editorial DB.
# They are in scope whenever ANY of the other platforms' clip relations is
# populated (i.e. the row has a video planned at all). The shared clip page
# resolves identically regardless of which relation it was followed through.
PLATFORMS_TAG_ALONG = frozenset({"th"})


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


class _RowState:
    """In-memory view of one editorial row across the four scheduled platforms.

    Tracks per-platform whether the row is in scope (clip relation
    populated), already scheduled (post URL non-empty), and the result the
    driver returned. Aggregates into a single per-row status the orchestrator
    surfaces in its summary list.
    """

    def __init__(self, page_id: str, day: date, payload: ClipPayload,
                 link_status: dict, in_scope: dict):
        self.page_id = page_id
        self.day = day
        self.payload = payload
        # Per-platform existing link (None = empty, str = already populated).
        self.link_status: dict[str, Optional[str]] = link_status
        # Per-platform in-scope flag (True = clip relation populated for that platform).
        self.in_scope: dict[str, bool] = in_scope
        # Filled by drivers post-run.
        self.driver_status: dict[str, str] = {}  # platform -> "LIVE"|"DRY"|"FAIL"|"LOGIN-REQUIRED"|"SKIP"
        self.driver_detail: dict[str, str] = {}

    @property
    def day_title(self) -> str:
        return date_to_day_title(self.day)


def fetch_wip_video_rows(notion, db_id: str, video_cols: dict,
                         days: Optional[list[date]], clip_cols: dict) -> list[_RowState]:
    """Query the editorial DB for WIP-Video rows in scope.

    If ``days`` is None, returns every WIP-Video row (``--all-wip`` mode).
    Otherwise filters by title-equals for each target day. For each in-scope
    row, follows the first populated ``clip <P>(v)`` relation, resolves the
    shared clip page once, and returns a ``_RowState`` ready to be dispatched.
    Rows whose clip payload fails to resolve (missing clip relation, missing
    .mp4, etc.) are returned with ``payload=None``-style sentinels so the
    orchestrator can surface them as FAIL in the summary.
    """
    wip_col = video_cols["wip_checkbox"]
    title_col = video_cols["title_day"]

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

    def _ingest(results, default_day: Optional[date]) -> list[_RowState]:
        rows: list[_RowState] = []
        for r in results:
            row_day = default_day or _row_day(r)
            if row_day is None:
                logger.warning("⚠️ Skipping row %s: unparseable day title.", r.get("id"))
                continue

            # In-scope per platform = the platform-specific clip relation is set,
            # OR the platform is a "tag-along" (no per-platform column on the DB)
            # and ANY other platform's clip relation is populated.
            props = r.get("properties", {})
            in_scope: dict[str, bool] = {}
            any_clip_populated = False
            for p in PLATFORMS_SCHEDULED:
                col = video_cols.get(f"clip_rel_{p}")
                rels = props.get(col, {}).get("relation", []) or [] if col else []
                in_scope[p] = bool(rels)
                if rels:
                    any_clip_populated = True
            for p in PLATFORMS_TAG_ALONG:
                if not in_scope.get(p):
                    in_scope[p] = any_clip_populated

            # Existing link <P>(v) per platform — drives idempotency.
            link_status: dict[str, Optional[str]] = {}
            for p in PLATFORMS_SCHEDULED + ("sb",):
                col = video_cols.get(f"post_url_{p}")
                if not col:
                    link_status[p] = None
                    continue
                url_obj = props.get(col, {})
                link_status[p] = url_obj.get("url") if url_obj.get("type") == "url" else None

            # Resolve the shared clip payload via the first populated relation.
            try:
                payload = load_clip_payload(notion, r, video_cols, clip_cols)
            except (RuntimeError, FileNotFoundError) as err:
                logger.error(
                    "❌ %s: clip payload resolution failed: %s",
                    date_to_day_title(row_day), err,
                )
                # Surface as a state with empty payload so summary shows FAIL.
                state = _RowState(
                    page_id=r["id"], day=row_day,
                    payload=ClipPayload(
                        clip_page_id="",
                        title="(unresolved)",
                        video_path=Path(""),
                        thumb_path=Path(""),
                        caption_short="",
                        caption_long="",
                    ),
                    link_status=link_status,
                    in_scope=in_scope,
                )
                for p in PLATFORMS_SCHEDULED:
                    state.driver_status[p] = "FAIL"
                    state.driver_detail[p] = f"payload resolution: {err}"
                rows.append(state)
                continue

            rows.append(_RowState(
                page_id=r["id"], day=row_day, payload=payload,
                link_status=link_status, in_scope=in_scope,
            ))
        return rows

    if days is None:
        results = query_rows_by_filter(
            notion, db_id,
            filter_obj={"property": wip_col, "checkbox": {"equals": True}},
        )
        rows = _ingest(results, default_day=None)
    else:
        rows = []
        for d in days:
            title = date_to_day_title(d)
            r2 = query_rows_by_filter(
                notion, db_id,
                filter_obj={"and": [
                    {"property": title_col, "title": {"equals": title}},
                    {"property": wip_col, "checkbox": {"equals": True}},
                ]},
            )
            rows.extend(_ingest(r2, default_day=d))

    rows.sort(key=lambda r: r.day)
    return rows


def _rows_for_platform(rows: list[_RowState], platform: str, force: bool) -> list:
    """Filter rows to those eligible for the given platform.

    Eligible = clip relation populated for that platform AND
    (link <P>(v) is empty OR --force). Already-failed (payload-failed)
    rows are excluded since they have nothing to schedule.
    """
    from planning.videos.videos_linkedin import VideoRow  # local to avoid cycle
    eligible = []
    for row in rows:
        if row.driver_status.get(platform) == "FAIL":
            continue
        if not row.in_scope.get(platform):
            row.driver_status[platform] = "SKIP"
            row.driver_detail[platform] = f"clip {platform.upper()}(v) not set"
            continue
        if row.link_status.get(platform) and not force:
            row.driver_status[platform] = "SKIP"
            row.driver_detail[platform] = f"link {platform.upper()}(v) already populated"
            continue
        eligible.append(VideoRow(
            page_id=row.page_id,
            day=row.day,
            payload=row.payload,
            existing_post_url=row.link_status.get(platform),
        ))
    return eligible


def _record_driver_results(rows: list[_RowState], platform: str, driver_results: list[dict]) -> None:
    """Merge a driver's per-row results back into the row state."""
    by_day = {r.day_title: r for r in rows}
    for entry in driver_results:
        day = entry["day"]
        state = by_day.get(day)
        if state is None:
            continue
        state.driver_status[platform] = entry["status"]
        state.driver_detail[platform] = entry.get("detail", "")


def _aggregate_row_status(state: _RowState, dry_run: bool) -> tuple[str, str]:
    """Aggregate per-platform results into one row-level status + detail.

    Returns (status, detail). Status ∈ LIVE / DRY / PARTIAL / FAIL / LOGIN-REQUIRED.
    """
    parts: list[str] = []
    statuses: list[str] = []
    for p in PLATFORMS_SCHEDULED:
        s = state.driver_status.get(p, "SKIP")
        parts.append(f"{p.upper()}:{s}")
        statuses.append(s)

    if any(s == "LOGIN-REQUIRED" for s in statuses):
        return "LOGIN-REQUIRED", ", ".join(parts)
    if all(s == "SKIP" for s in statuses):
        return "SKIP", ", ".join(parts)
    if all(s in ("SKIP", "FAIL") for s in statuses) and any(s == "FAIL" for s in statuses):
        return "FAIL", ", ".join(parts)
    if dry_run:
        return ("DRY" if any(s == "DRY" for s in statuses) else "FAIL"), ", ".join(parts)

    has_live = any(s == "LIVE" for s in statuses)
    has_fail = any(s == "FAIL" for s in statuses)
    if has_live and not has_fail:
        return "LIVE", ", ".join(parts)
    if has_live and has_fail:
        return "PARTIAL", ", ".join(parts)
    return "FAIL", ", ".join(parts)


def _maybe_untick_wip(notion, video_cols: dict, state: _RowState, dry_run: bool) -> None:
    """Untick Work-in-Progress-Video iff every scheduled platform is OK AND
    link SB is populated.

    "OK" per platform is one of:
      * LIVE                          (just scheduled this run)
      * SKIP with link populated      (idempotent skip — already scheduled in a prior run)
      * SKIP with platform out-of-scope (clip relation deliberately empty)

    SKIP via a ``--skip-<P>`` flag does NOT count — the platform genuinely
    hasn't been scheduled and WIP-Vd must stay checked so the user can
    re-run later. FAIL / LOGIN-REQUIRED also block the untick.

    Note: tag-along platforms (no ``post_url_<P>`` column on the editorial
    DB — currently TH) have no link-based idempotency marker. They are
    only considered OK if the driver returned LIVE this run.
    """
    if dry_run:
        return

    for p in PLATFORMS_SCHEDULED:
        s = state.driver_status.get(p, "")
        if s == "LIVE":
            continue
        if s == "SKIP" and state.link_status.get(p):
            continue  # idempotent skip
        if s == "SKIP" and not state.in_scope.get(p):
            continue  # out of scope, user opted out
        logger.info(
            "⏳ %s: keeping WIP-Vd checked — %s status is %r (not LIVE / idempotent-skip / out-of-scope).",
            state.day_title, p.upper(), s,
        )
        return

    sb_link = state.link_status.get("sb")
    if not sb_link:
        logger.info(
            "⏳ %s: all scheduled platforms OK but link SB not yet populated — "
            "leaving WIP-Vd checked. Daily Substack pipeline will close the loop.",
            state.day_title,
        )
        return

    try:
        set_field(
            notion, state.page_id, "wip_checkbox", False,
            video_cols, "checkbox",
        )
        logger.info("☑️ %s: Work-in-Progress-Video unticked in Notion.", state.day_title)
    except Exception as err:
        logger.warning(
            "⚠️ %s: scheduled OK but failed to untick WIP-Video: %s",
            state.day_title, err,
        )


def _set_post_url(notion, page_id: str, video_cols: dict, platform: str,
                  url: str, day_title: str) -> None:
    """Write a per-platform link <P>(v) so re-runs skip this platform."""
    role = f"post_url_{platform}"
    try:
        set_field(notion, page_id, role, url, video_cols, "url")
        logger.debug("🔗 %s: %s set to %s", day_title, role, url)
    except Exception as err:
        logger.warning(
            "⚠️ %s: scheduled OK on %s but could not write %s: %s",
            day_title, platform.upper(), role, err,
        )


def _scheduled_sentinel(platform: str, day_title: str) -> str:
    """Sentinel URL written into link <P>(v) so re-runs skip already-scheduled platforms.

    The data-collection pipeline (``reporting/social_client``) will overwrite
    with the real post URL once the scheduled post goes live. The sentinel
    is a real-looking URL so Notion's url-field validation accepts it.
    """
    return f"https://scheduled.local/{platform}/{day_title}"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Schedule the weekly video clip across LinkedIn / Instagram / Twitter / Threads."
    )
    p.add_argument("--week-start", type=str, default=None,
                   help="Monday of the target week (YYYY-MM-DD). Default: next Monday.")
    p.add_argument("--date", type=str, default=None,
                   help="Single-day mode (YYYYMMDD or YYYY-MM-DD). Overrides --week-start.")
    p.add_argument("--all-wip", action="store_true",
                   help="Schedule every WIP-Video row, no date filter.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Walk each platform's flow up to Schedule; do NOT submit.")
    mode.add_argument("--live", action="store_true",
                      help="Actually schedule on every platform.")
    p.add_argument("--force", action="store_true",
                   help="Schedule even if link <P>(v) is already populated.")
    p.add_argument("--debug", action="store_true", help="Enable debug logging.")
    p.add_argument("--skip-li", action="store_true")
    p.add_argument("--skip-ig", action="store_true")
    p.add_argument("--skip-tw", action="store_true")
    p.add_argument("--skip-th", action="store_true")
    return p.parse_args()


def main() -> tuple[int, list[dict]]:
    args = parse_args()
    # Configure every videos-side logger up front so per-driver log lines
    # propagate (the bare ``getLogger`` calls inside videos_<P>.py inherit
    # whatever the first ``setup_logger`` configured for the root).
    for name in (
        "videos_schedule",
        "videos_session",
        "videos_linkedin",
        "videos_instagram",
        "videos_twitter",
        "videos_threads",
    ):
        configure_logger(name, debug=args.debug)
    cfg = load_videos_config()

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
        logger.info("🎯 All-WIP mode: scheduling every WIP-Video row.")
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
    rows = fetch_wip_video_rows(notion, db_id, cfg["editorial_columns"],
                                target_days, cfg["clip_columns"])
    if not rows:
        logger.warning("⚠️ No WIP-Video rows in target range. Nothing to do.")
        return 0, []

    logger.info("📋 %d in-scope row(s):", len(rows))
    for r in rows:
        scope_summary = " ".join(
            f"{p.upper()}:{'✓' if r.in_scope[p] else '✗'}"
            for p in PLATFORMS_SCHEDULED
        )
        link_summary = " ".join(
            f"{p.upper()}={'set' if r.link_status.get(p) else 'empty'}"
            for p in PLATFORMS_SCHEDULED + ("sb",)
        )
        logger.info("   - %s scope=[%s] links=[%s]",
                    r.day_title, scope_summary, link_summary)

    # Pre-compute eligibility per platform (so we don't waste a session if 0 rows).
    skip = {"li": args.skip_li, "ig": args.skip_ig, "tw": args.skip_tw, "th": args.skip_th}

    # Dispatch per platform. Each driver opens its own browser session.
    for platform in PLATFORMS_SCHEDULED:
        if skip[platform]:
            logger.info("⏭️  Skipping %s (per --skip-%s).", platform.upper(), platform)
            for state in rows:
                state.driver_status[platform] = "SKIP"
                state.driver_detail[platform] = "platform skipped via --skip-* flag"
            continue

        eligible = _rows_for_platform(rows, platform, force=args.force)
        if not eligible:
            logger.info("ℹ️  %s: no eligible rows after filtering.", platform.upper())
            continue

        logger.info("━━━━━━━━━━ %s: %d row(s) ━━━━━━━━━━", platform.upper(), len(eligible))
        if platform == "li":
            from planning.videos.videos_linkedin import run as run_li
            driver_results = run_li(eligible, cfg, dry_run=dry_run)
        elif platform == "ig":
            from planning.videos.videos_instagram import run as run_ig
            driver_results = run_ig(eligible, cfg, dry_run=dry_run)
        elif platform == "tw":
            from planning.videos.videos_twitter import run as run_tw
            driver_results = run_tw(eligible, cfg, dry_run=dry_run)
        elif platform == "th":
            from planning.videos.videos_threads import run as run_th
            driver_results = run_th(eligible, cfg, dry_run=dry_run)
        else:
            continue

        _record_driver_results(rows, platform, driver_results)

        # Write sentinel link <P>(v) for any LIVE row so re-runs skip it.
        if not dry_run:
            for entry in driver_results:
                if entry["status"] != "LIVE":
                    continue
                # Find the matching state.
                state = next((s for s in rows if s.day_title == entry["day"]), None)
                if state is None:
                    continue
                sentinel = _scheduled_sentinel(platform, state.day_title)
                _set_post_url(notion, state.page_id, cfg["editorial_columns"],
                              platform, sentinel, state.day_title)
                state.link_status[platform] = sentinel

    # Aggregate + coordinate WIP-Video untick per row.
    summary_rows: list[dict] = []
    for state in rows:
        # Re-read link SB(v) before deciding untick — daily SB pipeline may
        # have run concurrently.
        try:
            page = notion.pages.retrieve(page_id=state.page_id)
            sb_col = cfg["editorial_columns"].get("post_url_sb")
            if sb_col:
                sb_prop = page.get("properties", {}).get(sb_col, {})
                if sb_prop.get("type") == "url":
                    state.link_status["sb"] = sb_prop.get("url")
        except Exception as err:
            logger.warning(
                "⚠️ %s: could not re-read link SB(v) before untick decision: %s",
                state.day_title, err,
            )

        status, detail = _aggregate_row_status(state, dry_run)
        summary_rows.append({"day": state.day_title, "status": status, "detail": detail})
        _maybe_untick_wip(notion, cfg["editorial_columns"], state, dry_run)

    logger.info("══════════ Summary ══════════")
    for entry in summary_rows:
        logger.info("   %s: %s | %s", entry["day"], entry["status"], entry["detail"])

    # SKIP-only is not a failure (e.g. --skip-* flags or all-already-scheduled).
    failed = [r for r in summary_rows if r["status"] in ("FAIL", "PARTIAL", "LOGIN-REQUIRED")]
    return (0 if not failed else 11), summary_rows


if __name__ == "__main__":
    raise SystemExit(main()[0])
