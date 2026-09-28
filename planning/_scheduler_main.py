"""Shared ``main()`` prelude + single-post row loop for the planning schedulers.

The Twitter, Threads, Instagram, LinkedIn and Videos schedulers each carried a
~60-line copy of the same prelude — dry-run/live resolution, the ``--all-wip``
vs ``--date`` vs ``--week-start`` target-day logic, Notion init and the
"skip rows whose link is already populated" dedup — and Twitter/Threads also
duplicated the whole attempt-row / untick / summary loop, differing only in the
platform label and the "return to start page" callback.

``resolve_scope``, ``notion_or_none`` and ``drop_already_scheduled`` are the
shared prelude every scheduler calls. ``run_single_post_scheduler`` is the
complete ``main()`` body for the one-post-per-row platforms (Twitter, Threads);
the multi-leg schedulers (Instagram story+post, LinkedIn routes, Videos
drivers) keep their own per-row loop and reuse only the prelude.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Optional

from playwright.sync_api import TimeoutError as PWTimeoutError

from planning._dates import parse_single_date, parse_week_start
from planning._failure import attempt_row
from planning._session_base import LoginRequiredError
from reporting.notion.editorial import init_notion_client, set_field
from reporting.notion.notion_update import format_database_id


@dataclass(frozen=True)
class RunScope:
    """Resolved run mode + target days. ``target_days`` is None for ``--all-wip``."""

    dry_run: bool
    target_days: Optional[list[date]]


def resolve_scope(
    args: argparse.Namespace,
    cfg: dict,
    *,
    wip_label: str,
    log: logging.Logger,
) -> Optional[RunScope]:
    """Resolve dry-run/live and the target days from the parsed CLI args.

    Returns None (after logging the error) when ``--all-wip`` is combined with
    ``--date`` / ``--week-start``; callers exit with code 2.
    """
    if args.live:
        dry_run = False
    elif args.dry_run:
        dry_run = True
    else:
        dry_run = cfg.get("dry_run_default", True)

    if args.all_wip and (args.date or args.week_start):
        log.error("❌ --all-wip is mutually exclusive with --date / --week-start.")
        return None

    if args.all_wip:
        target_days = None
        log.info("🎯 All-WIP mode: ignoring date filter, scheduling every %s row.", wip_label)
    elif args.date:
        d = parse_single_date(args.date)
        target_days = [d]
        log.info("🎯 Single-day mode: %s", d.isoformat())
    else:
        monday = parse_week_start(args.week_start)
        target_days = [monday + timedelta(days=i) for i in range(7)]
        log.info("🗓️  Target week: %s → %s",
                 target_days[0].isoformat(), target_days[-1].isoformat())
    return RunScope(dry_run=dry_run, target_days=target_days)


def notion_or_none(token: str, *, log: logging.Logger) -> Any:
    """Init the Notion client; log and return None when it cannot be built."""
    notion = init_notion_client(token)
    if notion is None:
        log.error("❌ Could not initialize Notion client.")
    return notion


def drop_already_scheduled(
    rows: list,
    *,
    force: bool,
    link_label: str,
    log: logging.Logger,
) -> list:
    """Drop rows whose ``existing_post_url`` is set unless ``--force``."""
    if force:
        return rows
    kept = [r for r in rows if not r.existing_post_url]
    if len(kept) != len(rows):
        log.info(
            "⏭️  Skipped %d row(s) whose link %s is already populated (use --force to override).",
            len(rows) - len(kept), link_label,
        )
    return kept


def run_single_post_scheduler(
    args: argparse.Namespace,
    *,
    platform: str,
    log: logging.Logger,
    cfg: dict,
    load_token: Callable[[], str],
    session_cls: Callable[[dict], Any],
    fetch_rows: Callable[..., list],
    resolve_payload: Callable[..., Any],
    schedule_post: Callable[..., str],
    cancel_composer: Callable[[Any], None],
    return_home: Callable[[Any, str], None],
    after_login: Optional[Callable[[Any], None]] = None,
    login_settle_ms: int = 3500,
) -> tuple[int, list[dict]]:
    """Full ``main()`` body for the one-post-per-row schedulers (Twitter, Threads).

    ``platform`` is the two-letter column label (``"TW"`` / ``"TH"``) used in log
    lines. ``return_home(page, feed_url)`` puts the page back on the composer's
    start view; ``after_login(page)`` is an optional post-login hook.
    """
    wip_label = f"WIP-{platform}"
    link_label = platform

    scope = resolve_scope(args, cfg, wip_label=wip_label, log=log)
    if scope is None:
        return 2, []
    dry_run = scope.dry_run

    notion = notion_or_none(load_token(), log=log)
    if notion is None:
        return 3, []

    db_id = format_database_id(cfg["editorial_db_id"])
    rows = fetch_rows(notion, db_id, cfg["editorial_columns"], scope.target_days)
    if not rows:
        log.warning("⚠️ No %s rows in target range. Nothing to do.", wip_label)
        return 0, []

    log.info("📋 %d in-scope row(s):", len(rows))
    for r in rows:
        log.info("   - %s (page=%s, link %s=%s)",
                 r.day_title, r.page_id, link_label, r.existing_post_url or "(empty)")

    rows = drop_already_scheduled(rows, force=args.force, link_label=link_label, log=log)
    if not rows:
        log.info("ℹ️ Nothing left to schedule after dedup. Done.")
        return 0, []

    # Pre-resolve payloads BEFORE launching Chrome — fail fast on missing files.
    plans: list[tuple[Any, Any]] = []
    results: list[dict] = []
    for row in rows:
        try:
            payload = resolve_payload(notion, cfg, row)
        except (RuntimeError, FileNotFoundError) as err:
            log.error("❌ %s payload resolution failed: %s", row.day_title, err)
            results.append({"day": row.day_title, "status": "FAIL", "detail": f"payload resolution: {err}"})
            continue
        log.info(
            "🖼️ %s: image=%s, caption=%d chars",
            row.day_title, payload.image_path.name, len(payload.caption),
        )
        plans.append((row, payload))

    if not plans:
        log.warning("⚠️ All rows failed payload resolution. Nothing to do.")
        return 11, results

    statuses: list[str] = []
    with session_cls(cfg) as session:
        try:
            session.goto_with_login_check(cfg["feed_url"])
        except LoginRequiredError as err:
            log.error("❌ %s", err)
            for row, _ in plans:
                results.append({"day": row.day_title, "status": "LOGIN-REQUIRED", "detail": str(err)})
            return 4, results
        session.page.wait_for_timeout(login_settle_ms)
        if after_login is not None:
            after_login(session.page)

        for row, payload in plans:
            return_home(session.page, cfg["feed_url"])

            def _reset() -> None:
                """Clean slate between attempts — a half-filled composer left
                by the failed attempt would otherwise capture the retry."""
                cancel_composer(session.page)
                return_home(session.page, cfg["feed_url"])

            try:
                status = attempt_row(
                    lambda: schedule_post(session, cfg, row, payload, dry_run=dry_run),
                    label=row.day_title,
                    reset=_reset,
                )
            except (RuntimeError, PWTimeoutError) as err:
                shot = session.screenshot_failure(f"{row.day_title}-error")
                log.error("❌ %s post failed: %s (screenshot %s)", row.day_title, err, shot)
                cancel_composer(session.page)
                return_home(session.page, cfg["feed_url"])
                statuses.append(f"{row.day_title}: post:FAIL({err})")
                results.append({"day": row.day_title, "status": "FAIL", "detail": f"{err} (screenshot {shot})"})
                continue

            statuses.append(f"{row.day_title}: {status}")
            results.append({
                "day": row.day_title,
                "status": "DRY" if dry_run else "LIVE",
                "detail": status,
            })

            # Untick the WIP checkbox only on a fully-successful LIVE day.
            if not dry_run and "LIVE" in status:
                try:
                    set_field(
                        notion, row.page_id, "wip_checkbox", False,
                        cfg["editorial_columns"], "checkbox",
                    )
                    log.info("☑️ %s: %s unticked in Notion", row.day_title, wip_label)
                except Exception as err:
                    log.warning(
                        "⚠️ %s: scheduled OK but failed to untick %s: %s",
                        row.day_title, wip_label, err,
                    )

    log.info("══════════ Summary ══════════")
    for s in statuses:
        log.info("   %s", s)
    failed = [r for r in results if r["status"] in ("FAIL", "LOGIN-REQUIRED")]
    return (0 if not failed else 11), results
