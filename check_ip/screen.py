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
        --reason "no mention of the owner anywhere in the post" --poster-url https://…
    python -m check_ip.screen verdict --id 12345 --verdict infringement --promotional --altered \\
        --reason "watermark cropped off; caption sells the poster's course"
    python -m check_ip.screen stats

The verdict answers one question: **does the post credit the owner?** A visible
watermark is not credit on its own — the policy is that the post must mention
him. ``--promotional`` and ``--altered`` record what makes an infringement
worse; see ``db.severity``.
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

from check_ip import db, process  # noqa: E402

DEFAULT_SOURCE = "LinkedIn"
DEFAULT_LIMIT = 10  # one worker batch; the skill fans out N/10 of them

# Fields the queue hands the skill. Deliberately excludes the owner columns:
# the skill should judge the page, not be primed by a past decision.
QUEUE_FIELDS = ("id", "local_image", "found_link", "title", "source", "post_date",
                "match_type", "search_date", "linkedin_count", "poster_pending")


def queue_config() -> dict:
    cfg = db.config().get("screen_queue") or {}
    return {
        "source": cfg.get("source", DEFAULT_SOURCE),
        "default_limit": int(cfg.get("default_limit", DEFAULT_LIMIT)),
        # Accounts that are the owner's own. Their posts are not findings at
        # all, and without this they dominate the ranking: the owner's own
        # handle holds 491 pending rows, far more than any real reuser, so the
        # first fifty batches would screen his own posts. Empty by default —
        # config.json is gitignored, so the pre-flight reports the exclusion
        # rather than letting a missing setting fail silently.
        "exclude_posters": [str(p).lower() for p in (cfg.get("exclude_posters") or [])],
    }


def next_batch(
    conn: sqlite3.Connection,
    *,
    limit: int,
    source: Optional[str] = None,
    image: Optional[str] = None,
    include_screened: bool = False,
    exclude_posters: Optional[list[str]] = None,
) -> list[dict]:
    """Serve the next batch of links to screen.

    Ranked by how many pending rows the same poster holds, then by how much
    reuse the image already attracts (its LinkedIn count), then most recent
    post first. Repeat offenders lead because they are one conversation rather
    than many: clearing one settles several findings at once, while the large
    majority of posters appear exactly once and are worth far less attention.

    ``exclude_posters`` drops accounts that are the owner's own — see
    ``queue_config``. Without it the owner's handle tops the ranking by a wide
    margin and the queue serves his own posts first.

    Only canonical rows (``duplicate`` 0 or 2) are served, so the same URL is
    never screened twice under different images, and rows the owner already
    decided are skipped.

    Two predicates keep ``Similar Match`` out (issue #292), and the redundancy
    is deliberate: ``retired`` covers whatever else gets retired later, while
    the explicit ``match_type`` test holds on a store where the retirement
    migration has not run yet — a fresh clone against an old database must not
    start proposing accusations off style lookalikes.
    """
    where = [
        "r.duplicate in (0, 2)",
        "r.found_link is not null",
        "r.ok is null",  # the owner already ruled on it — nothing to propose
        "r.retired = 0",
        "r.match_type = ?",
    ]
    params: list = [process.EXACT_MATCH]
    if not include_screened:
        where.append("r.screened_at is null")
    clause, clause_params = db.source_clause(source, "r.source")
    if clause:
        where.append(clause)
        params.extend(clause_params)
    if image:
        where.append("r.local_image = ?")
        params.append(image)
    if exclude_posters:
        placeholders = ", ".join("?" for _ in exclude_posters)
        where.append(f"(r.poster_key is null or r.poster_key not in ({placeholders}))")
        params.extend(exclude_posters)

    params.append(int(limit))
    rows = conn.execute(
        f"""
        with pending as (
            select r.id, r.poster_key
              from results r
             where {" and ".join(where)}
        ),
        poster_load as (
            select poster_key, count(*) as n
              from pending
             where poster_key is not null
             group by poster_key
        )
        select r.id, r.local_image, r.found_link, r.title, r.source,
               r.post_date, r.match_type, r.search_date,
               coalesce(i.linkedin_count, 0) as linkedin_count,
               coalesce(pl.n, 1)             as poster_pending
          from results r
          join pending p        on p.id = r.id
          left join images i    on i.filename = r.local_image
          left join poster_load pl on pl.poster_key = r.poster_key
         order by coalesce(pl.n, 1) desc,
                  coalesce(i.linkedin_count, 0) desc,
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
    promotional: bool = False,
    altered: bool = False,
) -> dict:
    """Write one proposed verdict. Owner columns are not in the statement.

    ``promotional`` (a self-promotional or commercial call to action) and
    ``altered`` (the image was edited — watermark cropped, painted over or
    removed) are aggravators, and only mean anything alongside an
    ``infringement`` verdict. Recording them on an ``acceptable`` row would
    make the stored severity disagree with ``db.severity()``, so they are
    cleared rather than trusted.
    """
    if verdict not in db.VERDICTS:
        raise ValueError(f"verdict must be one of {db.VERDICTS}, got {verdict!r}")
    if verdict != "infringement":
        promotional = altered = False

    row = conn.execute("select id, local_image, found_link from results where id = ?",
                       (row_id,)).fetchone()
    if row is None:
        raise KeyError(f"no result row with id {row_id}")

    conn.execute(
        """
        update results
           set screen_verdict     = ?,
               screen_reason      = ?,
               screened_at        = ?,
               screen_source      = ?,
               poster_url         = coalesce(?, poster_url),
               screen_promotional = ?,
               screen_altered     = ?
         where id = ?
        """,
        (verdict, reason or None, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         screen_source, poster_url or None,
         1 if promotional else 0, 1 if altered else 0, row_id),
    )
    conn.commit()
    return {
        "id": row_id,
        "verdict": verdict,
        "found_link": row["found_link"],
        "severity": db.severity(verdict, promotional, altered),
    }


def stats(conn: sqlite3.Connection, *, source: Optional[str] = None) -> dict:
    """Queue depth and screening progress, for the tab header and the skill.

    Every figure is over the *workable* rows: retired ones are excluded from
    all of them and reported on their own as ``retired``, so the totals match
    what the queue and the tab will actually serve rather than counting 115k
    rows nothing will ever look at again.
    """
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
                     and r.ok is null then 1 else 0 end)               as proposed_infringement,
            sum(case when r.screen_verdict = 'acceptable' then 1 else 0 end) as proposed_acceptable,
            sum(case when r.screen_verdict = 'unclear' then 1 else 0 end)    as proposed_unclear,
            sum(case when r.ok is null and ({db.severity_sql()}) = 3
                     then 1 else 0 end)                                as severity_3,
            sum(case when r.ok is null and ({db.severity_sql()}) = 2
                     then 1 else 0 end)                                as severity_2
          from results r
         where r.duplicate in (0, 2) and r.retired = 0 and {scope}
        """,
        params,
    ).fetchone()
    out = {k: (row[k] or 0) for k in row.keys()}
    out["retired"] = conn.execute(
        f"""select count(*) from results r
             where r.duplicate in (0, 2) and r.retired = 1 and {scope}""",
        params,
    ).fetchone()[0]
    return out


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
    p_verdict.add_argument("--promotional", action="store_true",
                           help="the post carries a self-promotional or commercial CTA")
    p_verdict.add_argument("--altered", action="store_true",
                           help="the image was edited: watermark cropped, painted over, removed")

    p_stats = sub.add_parser("stats", help="queue depth and progress")
    p_stats.add_argument("--source", default=cfg["source"])

    args = parser.parse_args(argv)
    conn = db.connect()

    if args.command == "next":
        source = None if args.source in ("any", "all", "") else args.source
        batch = next_batch(conn, limit=args.limit, source=source, image=args.image,
                           include_screened=args.include_screened,
                           exclude_posters=cfg["exclude_posters"])
        if args.json:
            print(json.dumps(batch, indent=2, ensure_ascii=False))
        elif not batch:
            print("queue empty — nothing pending for this filter")
        else:
            for row in batch:
                repeat = (f" · poster has {row['poster_pending']} pending"
                          if row["poster_pending"] > 1 else "")
                print(f"[{row['id']}] {row['local_image']}  (image seen on LinkedIn "
                      f"{row['linkedin_count']}x{repeat})")
                print(f"    {row['found_link']}")
                print(f"    {row['match_type']} · posted {row['post_date'] or 'unknown'} "
                      f"· found {row['search_date']}")
        return 0

    if args.command == "verdict":
        out = record_verdict(conn, args.id, verdict=args.verdict, reason=args.reason,
                             poster_url=args.poster_url, screen_source=args.screen_source,
                             promotional=args.promotional, altered=args.altered)
        tag = f" · severity {out['severity']}" if out["severity"] else ""
        print(f"✅ [{out['id']}] {out['verdict']}{tag} — {out['found_link']}")
        return 0

    source = None if args.source in ("any", "all", "") else args.source
    for key, value in stats(conn, source=source).items():
        # The retired count carries its reason inline: a checkout that has not
        # run `migrate --retire-similar` shows 0 here, and one that has should
        # not have to go read the schema to find out what was skipped and why.
        note = f"  ({db.RETIRED_REASON})" if key == "retired" else ""
        print(f"{key:24s} {value}{note}")
    # Surfaced rather than silent: config.json is gitignored, so a checkout
    # without this set would quietly rank the owner's own posts first.
    excluded = cfg["exclude_posters"]
    if excluded:
        held = conn.execute(
            f"""select count(*) from results
                 where duplicate in (0, 2) and ok is null and screened_at is null
                   and poster_key in ({", ".join("?" for _ in excluded)})""",
            excluded,
        ).fetchone()[0]
        print(f"{'own posts excluded':24s} {held} ({', '.join(excluded)})")
    else:
        print(f"{'own posts excluded':24s} 0 ⚠ screen_queue.exclude_posters is not configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
