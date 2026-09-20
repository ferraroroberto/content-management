"""Read/write layer for the control-panel's check-IP tab.

The tab is UI only — every query and every write goes through here, matching
how ``newsletter/triage/review.py`` serves the triage tab. Keeping the SQL out
of the Streamlit module is what lets the store be tested without a browser.

The owner's five decision columns are written from exactly one place:
``apply_decisions`` below, called when the owner clicks Apply. Nothing saves
implicitly.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db  # noqa: E402

SOURCES = ("LinkedIn", "Twitter/X", "Instagram", "Facebook", "Pinterest")

# Re-exported so the tab can name it without reaching into db.
OPEN_WEB = db.OPEN_WEB

# What the editable table shows. Owner columns are editable; everything else,
# including the skill's proposal, is read-only context.
FRAME_COLUMNS = [
    "id", "local_image", "found_link", "title", "source", "match_type",
    "post_date", "search_date", "duplicate",
    "screen_verdict", "screen_reason", "poster_url", "screened_at",
    "screen_promotional", "screen_altered",
    "ok", "person", "chat", "report", "fixed",
]

# Derived, not stored: one expression shared with the queue so the tab and the
# skill agree on what "worst" means. Appended to the select, not a real column.
DERIVED_COLUMNS = {"severity": db.severity_sql()}

STATUS_FILTERS = {
    "needs a decision": "r.ok is null",
    "screened, awaiting my call": "r.ok is null and r.screened_at is not null",
    "worst only — no credit, promoted, image edited": f"r.ok is null and ({db.severity_sql()}) = 3",
    "proposed infringement, any severity": "r.ok is null and r.screen_verdict = 'infringement'",
    "flagged as infringement": "r.ok = 0",
    "marked acceptable": "r.ok = 1",
    "reported": "r.report is not null and r.report != '0'",
    "everything": "1 = 1",
}

# How the table is ordered. Severity-first is the point of the two flags: it
# puts the cases worth a message at the top instead of the most-reused image.
SORT_ORDERS = {
    "worst first": f"({db.severity_sql()}) desc, coalesce(i.linkedin_count, 0) desc, "
                   "r.post_date desc, r.id",
    "most reused first": "coalesce(i.linkedin_count, 0) desc, r.post_date desc, r.id",
    "newest post first": "r.post_date desc, r.id",
}


def store_exists() -> bool:
    """Whether the database file has been created yet.

    The tab calls this first so a fresh clone shows "run the migration"
    instead of an empty table that looks like a missing-data bug.
    """
    return db.db_path().exists()


def images(conn: sqlite3.Connection, *, source: Optional[str] = None) -> list[dict]:
    """Images that have at least one result the tab would show, most-reused first.

    Retired rows are excluded so the illustration picker cannot offer an image
    whose table then comes back empty.
    """
    clause, params = db.source_clause(source, "r.source")
    where = f"where {clause} and r.retired = 0" if clause else "where r.retired = 0"
    rows = conn.execute(
        f"""
        select i.filename,
               i.linkedin_count,
               i.total_links,
               i.last_processed_date,
               count(r.id) as shown
          from images i
          join results r on r.local_image = i.filename
          {where}
         group by i.filename
         order by i.linkedin_count desc, i.total_links desc
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def results_frame(
    conn: sqlite3.Connection,
    *,
    source: Optional[str] = None,
    image: Optional[str] = None,
    status: str = "needs a decision",
    canonical_only: bool = True,
    limit: int = 500,
    sort: str = "worst first",
) -> pd.DataFrame:
    """The table the tab renders.

    Capped by ``limit`` on purpose — the store holds 310k rows and no editable
    grid wants all of them. The filters, not the scrollbar, are how the owner
    narrows to what matters.

    Retired rows never appear, whatever the status filter says — the point of
    retiring them was to take them off this table (issue #292). A row the owner
    already annotated is never retired, so no past decision can disappear here.
    """
    where = ["r.found_link is not null", "r.retired = 0"]
    params: list = []
    if canonical_only:
        where.append("r.duplicate in (0, 2)")
    clause, clause_params = db.source_clause(source, "r.source")
    if clause:
        where.append(clause)
        params.extend(clause_params)
    if image:
        where.append("r.local_image = ?")
        params.append(image)
    where.append(STATUS_FILTERS.get(status, "1 = 1"))
    params.append(int(limit))

    selected = [f"r.{c}" for c in FRAME_COLUMNS]
    selected += [f"({expr}) as {name}" for name, expr in DERIVED_COLUMNS.items()]
    columns = FRAME_COLUMNS + list(DERIVED_COLUMNS)

    rows = conn.execute(
        f"""
        select {", ".join(selected)}
          from results r
          left join images i on i.filename = r.local_image
         where {" and ".join(where)}
         order by {SORT_ORDERS.get(sort, SORT_ORDERS["worst first"])}
         limit ?
        """,
        params,
    ).fetchall()
    frame = pd.DataFrame([dict(r) for r in rows], columns=columns)
    if frame.empty:
        return frame
    for flag in ("screen_promotional", "screen_altered"):
        frame[flag] = frame[flag].map({1: True, 0: False}).astype("object")
    # Streamlit's checkbox column wants a real bool; the store keeps 0/1/NULL
    # so "not yet decided" stays distinguishable from "decided: no".
    frame["fixed"] = frame["fixed"].map({1: True, 0: False}).astype("object")
    return frame


def apply_decisions(conn: sqlite3.Connection, rows: Iterable[dict]) -> dict:
    """Persist the owner's edits. The only writer of the five owner columns.

    Takes the edited rows straight from the data editor. A row whose owner
    fields all match what is already stored is skipped, so an Apply with no
    edits is a genuine no-op rather than 500 pointless writes.
    """
    changed = 0
    flagged = 0
    for row in rows:
        row_id = row.get("id")
        if row_id is None:
            continue
        current = conn.execute(
            "select ok, person, chat, report, fixed from results where id = ?", (row_id,)
        ).fetchone()
        if current is None:
            continue

        ok = _as_int(row.get("ok"))
        fixed = _as_int(row.get("fixed"))
        person = _as_text(row.get("person"))
        chat = _as_text(row.get("chat"))
        report = _as_text(row.get("report"))

        if (ok, person, chat, report, fixed) == (
            current["ok"], current["person"], current["chat"], current["report"], current["fixed"]
        ):
            continue

        conn.execute(
            "update results set ok = ?, person = ?, chat = ?, report = ?, fixed = ? where id = ?",
            (ok, person, chat, report, fixed, row_id),
        )
        changed += 1
        if ok == 0:
            flagged += 1
    conn.commit()
    return {"changed": changed, "flagged": flagged}


def _as_int(value) -> Optional[int]:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_text(value) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def overview(conn: sqlite3.Connection) -> dict:
    """Headline numbers for the top of the tab.

    ``results`` counts everything ever found, retired rows included — that is
    the search history and it did not shrink. ``canonical`` counts what is
    still judgeable, and ``retired`` says how much the difference is, so the
    header adds up instead of quietly losing 115k rows.
    """
    row = conn.execute(
        """
        select
            (select count(*) from images)  as images,
            (select count(*) from results) as results,
            (select count(*) from results where duplicate in (0, 2) and retired = 0) as canonical,
            (select count(*) from results where retired = 1)         as retired,
            (select count(*) from results where ok is not null)      as decided,
            (select count(*) from results where ok = 0)              as infringements,
            (select count(*) from results where screened_at is not null) as screened,
            (select max(last_processed_date) from images)            as last_search
        """
    ).fetchone()
    return dict(row)


def source_breakdown(conn: sqlite3.Connection) -> pd.DataFrame:
    """Canonical, non-retired rows and outstanding decisions per platform."""
    rows = conn.execute(
        """
        select coalesce(source, ?) as source,
               count(*)                                            as canonical,
               sum(case when ok is null then 1 else 0 end)          as pending,
               sum(case when ok = 0 then 1 else 0 end)              as infringements,
               sum(case when screened_at is not null then 1 else 0 end) as screened
          from results
         where duplicate in (0, 2) and retired = 0
         group by coalesce(source, ?)
         order by canonical desc
        """,
        (db.OPEN_WEB, db.OPEN_WEB),
    ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])
