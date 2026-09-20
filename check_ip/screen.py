"""The screening queue: serve links to check, record the verdicts.

This is the interface the ``check-ip`` skill drives. The skill pulls a ranked
batch of un-screened links, opens each in a browser, judges the post against
the three conditions of the licence the illustrations are published under, and
writes a *proposed* verdict back here.

**The screening pass never writes an owner decision.** ``ok`` / ``person`` /
``chat`` / ``report`` / ``fixed`` are ten months of manual triage and remain
the owner's alone — set in the control-panel tab, never from here. This module
refuses to write them at all; ``verdict`` touches only ``db.SCREEN_COLUMNS``.
That separation is what makes the skill safe to run unattended: the
worst it can do is propose something wrong, which the owner then overrules.

**The re-screen backlog is a queue of its own** (issue #300). ``next --unassessed``
serves only rows that were screened while at least one licence condition went
unestablished — the ones ``stats`` counts as ``not_fully_assessed``. They were
judged under the old credit-only question, and left in the general queue they
would sit behind thousands of never-screened rows forever.

**A re-screen no longer destroys the answer it replaces** (issue #301). Before
overwriting the screening columns, ``record_verdict`` copies them into
``screen_history`` in the same transaction — so the observation behind a
previous verdict stays retrievable, which matters because the screening
question itself has changed twice. ``screen_history`` holds no owner column
either.

Usage::

    python -m check_ip.screen next --limit 20
    python -m check_ip.screen next --limit 20 --image "bicycle backwards - micromanagement.png"
    python -m check_ip.screen next --limit 20 --unassessed   # the re-screen backlog
    python -m check_ip.screen verdict --id 12345 --verdict infringement \\
        --credit violated --noncommercial met --unmodified met \\
        --reason "no mention of the owner anywhere in the post" --poster-url https://…
    python -m check_ip.screen verdict --id 12345 --verdict infringement \\
        --credit met --noncommercial violated --unmodified met \\
        --reason "credited in the caption, but the post sells the poster's course"
    python -m check_ip.screen verdict --id 12345 --verdict unclear --outcome nothing_to_assess \\
        --reason "the post no longer exists"
    python -m check_ip.screen stats

**An honest "could not tell" no longer re-queues a row forever** (issue #305).
A condition now holds four states: met, violated, *assessed but not
establishable*, and never assessed. The third used to be stored as the fourth,
so a worker that opened a post and could not establish (say) non-commercial use
left the row looking untouched — and ``--unassessed`` served it again, and
again. Two live batches were handed the identical ten rows. ``--credit`` and
friends take ``unknown`` for the third; **omitting** the flag is the fourth.

The licence is **CC BY-NC-ND 4.0**: credit, non-commercial use and no
derivatives must *all* hold, and each is recorded separately. Neither a
never-assessed condition nor an indeterminate one is a pass — see
``db.severity`` and ``db.all_conditions_met``, neither of which reads a missing
or unsettled answer as compliance.
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

# Added to those on a re-screen serve only (``next_batch(unassessed=True)``).
# The owner columns stay excluded on every path — this is the *screening*
# opinion being replaced, which a worker re-judging the row needs to see and a
# first-time screening must never be shown.
RESCREEN_FIELDS = ("screen_verdict", "screen_reason")

# How the three condition states are spelled on the CLI and in error messages.
# "unknown" is a first-class choice on purpose: a worker has to be able to say
# it, and a reader has to be able to tell it from "met". The words go straight
# to db.condition_state — argparse restricts the input, that function owns the
# meaning, and there is no second word-to-value table to drift out of step.
#
# There is deliberately no word for NULL (issue #305). NULL means nobody ever
# looked, and the only honest way to say that is to not answer at all — which
# is what omitting the flag does. A worker that looked and could not tell says
# `unknown`, which is an answer and stores a 2.
CONDITION_CHOICES = ("met", "violated", "unknown")
CONDITION_WORDS = {1: "met", 0: "violated", 2: "could not be established",
                   None: "not assessed"}


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
    unassessed: bool = False,
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

    Two flags move the ``screened_at`` line, and they are not the same thing
    (issue #300). ``include_screened`` merely *drops* the "never screened"
    predicate, so a re-check competes with every unscreened row under the same
    ranking — with 7k pending rows behind them, the backlog #295 created is
    never reached that way. ``unassessed`` serves that backlog and nothing
    else: screened, at least one licence condition never looked at (still
    null — an ``unknown`` answer is a ``2`` and does not qualify, which is what
    makes the queue drain, issue #305), and not permanently unassessable. It is
    the same set ``stats()`` counts as
    ``not_fully_assessed`` — deliberately rendered from the same
    ``db.assessed_sql`` / ``db.permanent_sql`` helpers so the number the tab
    reports and the rows the queue serves cannot drift apart. Dropping the
    permanent ones matters beyond parity: a ``nothing_to_assess`` row can
    never become fully assessed, so including it would leave a queue that
    never drains. ``unassessed`` wins when both are passed — it is the
    narrower of the two, not a contradiction.

    Everything else is identical on all three paths: same source filter, same
    excluded posters, same retired/duplicate/``ok is null``/Exact Match
    predicates, same ranking. A re-screen batch is ordered like any other.

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
    if unassessed:
        where.append("r.screened_at is not null")
        where.append(f"not ({db.assessed_sql()})")
        where.append(f"not ({db.permanent_sql()})")
    elif not include_screened:
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
    # One conditional fragment rather than a second query: the re-screen serve
    # differs from a normal one by two columns and nothing else, and a forked
    # copy of this statement would be a second place for the ranking and the
    # predicates to drift.
    prior = "".join(f", r.{column}" for column in RESCREEN_FIELDS) if unassessed else ""
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
               coalesce(pl.n, 1)             as poster_pending{prior}
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
    credit_ok: object = None,
    noncommercial_ok: object = None,
    unmodified_ok: object = None,
    outcome: Optional[str] = None,
) -> dict:
    """Write one proposed verdict. Owner columns are not in the statement.

    The three conditions are recorded as given — ``1`` met, ``0`` violated,
    ``2`` assessed but not establishable, ``None`` never assessed — and
    **nothing is cleared**. The old model blanked
    the aggravators on a non-infringement verdict, which is exactly how a
    credited-but-commercial post ended up stored as compliant (issue #295).

    ``verdict`` is checked against ``db.verdict_for()`` rather than normalised:
    a worker that says ``acceptable`` while reporting a violated condition has
    contradicted itself, and silently picking one of the two answers would hide
    which. ``outcome`` says which kind of ``unclear`` this is and is required
    with that verdict, refused with any other.
    """
    if verdict not in db.VERDICTS:
        raise ValueError(f"verdict must be one of {db.VERDICTS}, got {verdict!r}")
    conditions = (db.condition_state(credit_ok), db.condition_state(noncommercial_ok),
                  db.condition_state(unmodified_ok))

    implied = db.verdict_for(*conditions)
    if implied != verdict:
        named = ", ".join(f"{column.removeprefix('screen_')}={CONDITION_WORDS[state]}"
                          for column, state in zip(db.CONDITION_COLUMNS, conditions))
        raise ValueError(
            f"verdict {verdict!r} contradicts the conditions ({named}), which imply "
            f"{implied!r} — record what you saw, not a summary of it"
        )

    if verdict == "unclear":
        if outcome not in db.UNCLEAR_OUTCOMES:
            raise ValueError(
                f"an 'unclear' verdict needs --outcome one of {db.UNCLEAR_OUTCOMES}: "
                f"'{db.OUTCOME_AMBIGUOUS}' if another look could settle it, "
                f"'{db.OUTCOME_NOTHING_TO_ASSESS}' if the post is gone or carries no "
                f"illustration at all"
            )
    elif outcome is not None:
        raise ValueError(f"--outcome only applies to an 'unclear' verdict, not {verdict!r}")

    row = conn.execute("select id, local_image, found_link from results where id = ?",
                       (row_id,)).fetchone()
    if row is None:
        raise KeyError(f"no result row with id {row_id}")

    # One transaction: the opinion being replaced is copied into screen_history
    # and the replacement is written, or neither happens (issue #301). `with
    # conn` commits on success and rolls back on any exception — the copy must
    # never survive a failed update, nor the update a failed copy.
    with conn:
        preserved = db.push_screen_history(conn, row_id)
        conn.execute(
            """
            update results
               set screen_verdict          = ?,
                   screen_outcome          = ?,
                   screen_reason           = ?,
                   screened_at             = ?,
                   screen_source           = ?,
                   poster_url              = coalesce(?, poster_url),
                   screen_credit_ok        = ?,
                   screen_noncommercial_ok = ?,
                   screen_unmodified_ok    = ?
             where id = ?
            """,
            (verdict, outcome, reason or None, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             screen_source, poster_url or None, *conditions, row_id),
        )
    return {
        "id": row_id,
        "verdict": verdict,
        "outcome": outcome,
        "found_link": row["found_link"],
        "severity": db.severity(*conditions),
        "fully_assessed": db.fully_assessed(*conditions),
        # True when this write replaced an earlier opinion and that opinion is
        # confirmed preserved in screen_history; False on a first screening,
        # which supersedes nothing. A prior opinion that could not be preserved
        # raises instead of landing here — the update never happens.
        "superseded": preserved,
    }


def stats(conn: sqlite3.Connection, *, source: Optional[str] = None) -> dict:
    """Queue depth and screening progress, for the tab header and the skill.

    Every figure is over the *workable* rows: retired ones are excluded from
    all of them and reported on their own as ``retired``, so the totals match
    what the queue and the tab will actually serve rather than counting 115k
    rows nothing will ever look at again.

    ``not_fully_assessed`` is the number issue #295 exists for: rows that were
    screened but whose licence conditions were not all looked at. They are
    **not** compliant rows and are counted apart from ``proposed_acceptable``,
    which only holds rows where all three conditions were met. The permanently
    unassessable ones (``nothing_to_assess``) are counted on their own instead,
    since no re-screen will ever move them.

    ``indeterminate`` is the number issue #305 exists for, and it is reported
    separately because it asks for something different: every condition was
    answered, and at least one answer was "could not be established". A
    ``not_fully_assessed`` row wants another screening pass; an
    ``indeterminate`` row has already had one and wants the owner's own
    judgement, or nothing. The two never overlap — ``db.indeterminate_sql``
    requires every condition answered — so they add up rather than
    double-counting, and neither is ever folded into ``proposed_acceptable``.
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
            sum(case when r.screened_at is not null
                      and not ({db.assessed_sql()})
                      and not ({db.permanent_sql()})
                     then 1 else 0 end)                                as not_fully_assessed,
            sum(case when r.screened_at is not null
                      and ({db.indeterminate_sql()})
                      and not ({db.permanent_sql()})
                     then 1 else 0 end)                                as indeterminate,
            sum(case when {db.permanent_sql()} then 1 else 0 end)      as nothing_to_assess,
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


def build_parser(cfg: dict) -> argparse.ArgumentParser:
    """The CLI surface, built from a queue config.

    Separate from ``main`` so the defaults can be asserted without opening the
    store — the condition defaults in particular, which carry meaning: one of
    them silently changing from "nobody looked" to "somebody looked" is what
    issue #305 is about, and it is invisible in any test that calls
    ``record_verdict`` directly.
    """
    parser = argparse.ArgumentParser(description="check_ip screening queue")
    sub = parser.add_subparsers(dest="command", required=True)

    p_next = sub.add_parser("next", help="serve the next batch of links to screen")
    p_next.add_argument("--limit", type=int, default=cfg["default_limit"])
    p_next.add_argument("--source", default=cfg["source"],
                        help="platform to screen; 'any' removes the filter")
    p_next.add_argument("--image", default=None, help="restrict to one illustration")
    p_next.add_argument("--include-screened", action="store_true",
                        help="also serve rows already screened, alongside the pending ones")
    p_next.add_argument("--unassessed", action="store_true",
                        help="serve only the re-screen backlog: rows screened with at least "
                             "one licence condition never looked at (the count `stats` "
                             "reports as not_fully_assessed). A condition answered "
                             "`unknown` counts as looked at, so screening a row removes "
                             "it from here. Narrows --include-screened")
    p_next.add_argument("--json", action="store_true", help="emit JSON instead of a table")

    p_verdict = sub.add_parser("verdict", help="record one proposed verdict")
    p_verdict.add_argument("--id", type=int, required=True)
    p_verdict.add_argument("--verdict", required=True, choices=list(db.VERDICTS))
    p_verdict.add_argument("--reason", default="")
    p_verdict.add_argument("--poster-url", default=None)
    p_verdict.add_argument("--screen-source", default="check-ip skill")
    # An omitted condition defaults to None — never assessed — and never to
    # `met`: the default has to be the state that claims nothing, or a worker
    # who forgets one silently certifies compliance it never checked. It is no
    # longer `unknown` either (issue #305): `unknown` now records that someone
    # looked, which would be a claim about a flag the caller never passed, and
    # would drain the row out of the re-screen queue on the strength of it.
    p_verdict.add_argument("--credit", default=None, choices=CONDITION_CHOICES,
                           help="BY — the post names, tags or links him "
                                "(omitted: never assessed)")
    p_verdict.add_argument("--noncommercial", default=None, choices=CONDITION_CHOICES,
                           help="NC — no promotional CTA and no paid/business context "
                                "(omitted: never assessed)")
    p_verdict.add_argument("--unmodified", default=None, choices=CONDITION_CHOICES,
                           help="ND — no crop, filter, added logo or text, no translation, "
                                "signature intact (omitted: never assessed)")
    p_verdict.add_argument("--outcome", default=None, choices=list(db.UNCLEAR_OUTCOMES),
                           help="required with --verdict unclear: 'ambiguous' if another look "
                                "could settle it, 'nothing_to_assess' if the post is gone or "
                                "carries no illustration")

    p_stats = sub.add_parser("stats", help="queue depth and progress")
    p_stats.add_argument("--source", default=cfg["source"])
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cfg = queue_config()
    args = build_parser(cfg).parse_args(argv)
    conn = db.connect()

    if args.command == "next":
        source = None if args.source in ("any", "all", "") else args.source
        batch = next_batch(conn, limit=args.limit, source=source, image=args.image,
                           include_screened=args.include_screened,
                           unassessed=args.unassessed,
                           exclude_posters=cfg["exclude_posters"])
        if args.json:
            print(json.dumps(batch, indent=2, ensure_ascii=False))
        elif not batch:
            # The two queues empty for different reasons, and saying "nothing
            # pending" when the re-screen backlog is drained reads as if the
            # flag had found nothing to serve in the first place.
            scope = "in the re-screen backlog" if args.unassessed else "pending"
            print(f"queue empty — nothing {scope} for this filter")
        else:
            for row in batch:
                repeat = (f" · poster has {row['poster_pending']} pending"
                          if row["poster_pending"] > 1 else "")
                print(f"[{row['id']}] {row['local_image']}  (image seen on LinkedIn "
                      f"{row['linkedin_count']}x{repeat})")
                print(f"    {row['found_link']}")
                print(f"    {row['match_type']} · posted {row['post_date'] or 'unknown'} "
                      f"· found {row['search_date']}")
                # Only served on the re-screen path, and shown rather than
                # hidden: the worker is replacing this opinion, so it should
                # be able to read what it is replacing (issue #300).
                if args.unassessed:
                    print(f"    previously {row['screen_verdict'] or 'unrecorded'}"
                          f" — {row['screen_reason'] or 'no reason recorded'}")
        return 0

    if args.command == "verdict":
        try:
            out = record_verdict(
                conn, args.id, verdict=args.verdict, reason=args.reason,
                poster_url=args.poster_url, screen_source=args.screen_source,
                credit_ok=args.credit, noncommercial_ok=args.noncommercial,
                unmodified_ok=args.unmodified,
                outcome=args.outcome,
            )
        except (ValueError, KeyError) as err:
            # Non-zero and loud: a rejected verdict must not read like a
            # recorded one to whatever is driving the CLI.
            print(f"❌ [{args.id}] not recorded — {err}")
            return 2
        tag = f" · severity {out['severity']}" if out["severity"] else ""
        if out["outcome"]:
            tag += f" · {out['outcome']}"
        elif not out["fully_assessed"]:
            tag += " · not fully assessed"
        # Says out loud that an earlier opinion was replaced and kept, so a
        # re-screening pass is visibly not destroying what it overwrites (#301).
        if out["superseded"]:
            tag += " · previous opinion kept"
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
