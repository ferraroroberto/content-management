"""The screening queue: serve links to check, record the verdicts.

This is the interface the ``check-ip`` skill drives. The skill pulls a ranked
batch of un-screened links, opens each in a browser, judges whether the
illustration is used and whether it is credited, and writes a *proposed*
verdict back here.

**The screening pass never writes an owner decision.** ``ok`` / ``person`` /
``chat`` / ``report`` / ``fixed`` are ten months of manual triage and remain
the owner's alone — set in the control-panel tab, never from here. This module
refuses to write them at all; ``verdict`` touches only the five screening
columns. That separation is what makes the skill safe to run unattended: the
worst it can do is propose something wrong, which the owner then overrules.

Usage::

    python -m check_ip.screen next --limit 20
    python -m check_ip.screen next --limit 20 --image "bicycle backwards - micromanagement.png"
    python -m check_ip.screen verdict --id 12345 --verdict infringement \\
        --reason "reposted with the signature cropped out" --poster-url https://…
    python -m check_ip.screen stats
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db  # noqa: E402

DEFAULT_SOURCE = "LinkedIn"
DEFAULT_LIMIT = 20

# Fields the queue hands the skill. Deliberately excludes the owner columns:
# the skill should judge the page, not be primed by a past decision.
QUEUE_FIELDS = ("id", "local_image", "found_link", "title", "source", "post_date",
                "match_type", "search_date", "linkedin_count")


def queue_config() -> dict:
    cfg = db.config().get("screen_queue") or {}
    return {
        "source": cfg.get("source", DEFAULT_SOURCE),
        "default_limit": int(cfg.get("default_limit", DEFAULT_LIMIT)),
    }


def next_batch(
    conn: sqlite3.Connection,
    *,
    limit: int,
    source: Optional[str] = None,
    image: Optional[str] = None,
    include_screened: bool = False,
) -> list[dict]:
    """Serve the next batch of links to screen.

    Ranked by how much reuse the image already attracts (its LinkedIn count),
    then by the most recent post first — the freshest reuse on the most-copied
    illustrations is where acting still has a point. Only canonical rows
    (``duplicate`` 0 or 2) are served, so the same URL is never screened twice
    under different images, and rows the owner already decided are skipped.
    """
    where = [
        "r.duplicate in (0, 2)",
        "r.found_link is not null",
        "r.ok is null",  # the owner already ruled on it — nothing to propose
    ]
    params: list = []
    if not include_screened:
        where.append("r.screened_at is null")
    clause, clause_params = db.source_clause(source, "r.source")
    if clause:
        where.append(clause)
        params.extend(clause_params)
    if image:
        where.append("r.local_image = ?")
        params.append(image)

    params.append(int(limit))
    rows = conn.execute(
        f"""
        select r.id, r.local_image, r.found_link, r.title, r.source,
               r.post_date, r.match_type, r.search_date,
               coalesce(i.linkedin_count, 0) as linkedin_count
          from results r
          left join images i on i.filename = r.local_image
         where {" and ".join(where)}
         order by coalesce(i.linkedin_count, 0) desc,
                  r.post_date desc,
                  r.id
         limit ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def record_verdict(
    conn: sqlite3.Connection,
    row_id: int,
    *,
    verdict: str,
    reason: str = "",
    poster_url: Optional[str] = None,
    screen_source: str = "check-ip skill",
) -> dict:
    """Write one proposed verdict. Owner columns are not in the statement."""
    if verdict not in db.VERDICTS:
        raise ValueError(f"verdict must be one of {db.VERDICTS}, got {verdict!r}")

    row = conn.execute("select id, local_image, found_link from results where id = ?",
                       (row_id,)).fetchone()
    if row is None:
        raise KeyError(f"no result row with id {row_id}")

    conn.execute(
        """
        update results
           set screen_verdict = ?,
               screen_reason  = ?,
               screened_at    = ?,
               screen_source  = ?,
               poster_url     = coalesce(?, poster_url)
         where id = ?
        """,
        (verdict, reason or None, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         screen_source, poster_url or None, row_id),
    )
    conn.commit()
    return {"id": row_id, "verdict": verdict, "found_link": row["found_link"]}


def stats(conn: sqlite3.Connection, *, source: Optional[str] = None) -> dict:
    """Queue depth and screening progress, for the tab header and the skill."""
    clause, params = db.source_clause(source, "r.source")
    scope = clause or "1 = 1"
    row = conn.execute(
        f"""
        select
            count(*)                                                  as canonical,
            sum(case when r.ok is not null then 1 else 0 end)          as owner_decided,
            sum(case when r.screened_at is not null then 1 else 0 end) as screened,
            sum(case when r.ok is null and r.screened_at is null
                     then 1 else 0 end)                                as pending,
            sum(case when r.screen_verdict = 'infringement'
                     and r.ok is null then 1 else 0 end)               as proposed_infringement
          from results r
         where r.duplicate in (0, 2) and {scope}
        """,
        params,
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


# ---------------------------------------------------------------------------
# CLI


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="check_ip screening queue")
    sub = parser.add_subparsers(dest="command", required=True)

    cfg = queue_config()

    p_next = sub.add_parser("next", help="serve the next batch of links to screen")
    p_next.add_argument("--limit", type=int, default=cfg["default_limit"])
    p_next.add_argument("--source", default=cfg["source"],
                        help="platform to screen; 'any' removes the filter")
    p_next.add_argument("--image", default=None, help="restrict to one illustration")
    p_next.add_argument("--include-screened", action="store_true",
                        help="also serve rows already screened (for a re-check)")
    p_next.add_argument("--json", action="store_true", help="emit JSON instead of a table")

    p_verdict = sub.add_parser("verdict", help="record one proposed verdict")
    p_verdict.add_argument("--id", type=int, required=True)
    p_verdict.add_argument("--verdict", required=True, choices=list(db.VERDICTS))
    p_verdict.add_argument("--reason", default="")
    p_verdict.add_argument("--poster-url", default=None)
    p_verdict.add_argument("--screen-source", default="check-ip skill")

    p_stats = sub.add_parser("stats", help="queue depth and progress")
    p_stats.add_argument("--source", default=cfg["source"])

    args = parser.parse_args(argv)
    conn = db.connect()

    if args.command == "next":
        source = None if args.source in ("any", "all", "") else args.source
        batch = next_batch(conn, limit=args.limit, source=source, image=args.image,
                           include_screened=args.include_screened)
        if args.json:
            print(json.dumps(batch, indent=2, ensure_ascii=False))
        elif not batch:
            print("queue empty — nothing pending for this filter")
        else:
            for row in batch:
                print(f"[{row['id']}] {row['local_image']}  (image seen on LinkedIn "
                      f"{row['linkedin_count']}x)")
                print(f"    {row['found_link']}")
                print(f"    {row['match_type']} · posted {row['post_date'] or 'unknown'} "
                      f"· found {row['search_date']}")
        return 0

    if args.command == "verdict":
        out = record_verdict(conn, args.id, verdict=args.verdict, reason=args.reason,
                             poster_url=args.poster_url, screen_source=args.screen_source)
        print(f"✅ [{out['id']}] {out['verdict']} — {out['found_link']}")
        return 0

    source = None if args.source in ("any", "all", "") else args.source
    for key, value in stats(conn, source=source).items():
        print(f"{key:24s} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
