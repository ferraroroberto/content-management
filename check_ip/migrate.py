"""Bulk-update lane for the illustration copyright check's SQLite store.

The Excel import and the one-shot lanes that followed it (retire the
``Similar Match`` rows, map the credit-only screening onto the three licence
conditions, back-fill the pre-re-screen opinions) are finished and gone; git
history holds them. What is left is the one lane that stays useful:

Usage::

    python -m check_ip.migrate --recompute-duplicates  # re-elect the canonical rows (#291)

It needs no workbooks, never deletes, and reports what it changed plus the
annotation count either side of it.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db  # noqa: E402

logger = logging.getLogger("check_ip.migrate")


def _duplicate_shape(conn: sqlite3.Connection) -> dict:
    """How the duplicate flag currently lies across the store.

    ``canonical`` is what the tab counts, ``servable`` what the queue can
    reach, and the two ``*_secondary`` numbers are the ones worth watching: a
    screened or annotated row flagged ``duplicate = 1`` is work that still
    exists but no longer shows anywhere.
    """
    annotated = " or ".join(f"{c} is not null" for c in db.OWNER_COLUMNS)
    row = conn.execute(
        f"""
        select
            sum(case when duplicate in (0, 2) then 1 else 0 end)                as canonical,
            sum(case when duplicate in (0, 2) and retired = 0 then 1 else 0 end) as servable,
            sum(case when duplicate = 1 and screened_at is not null then 1 else 0 end)
                                                                                as screened_secondary,
            sum(case when duplicate = 1 and ({annotated}) then 1 else 0 end)    as annotated_secondary
          from results
        """
    ).fetchone()
    return {k: row[k] or 0 for k in
            ("canonical", "servable", "screened_secondary", "annotated_secondary")}


def _recompute_duplicates(conn: sqlite3.Connection) -> int:
    """Re-run the duplicate marking and prove it cost no decision (issue #291).

    Nothing but the ``duplicate`` column is written — ``found_link`` stays the
    URL that was actually found and opened, and every verdict stays on the row
    that earned it. The guard is the same one the retirement lane ends on: the
    annotation count and the row count are taken either side and a change is a
    failure, not a warning.
    """
    before_counts = db.counts(conn)
    before = _duplicate_shape(conn)

    logger.info("🔁 recomputing duplicate flags — grouping on the canonical link (#291)")
    dupes = db.mark_duplicates(conn)

    after_counts = db.counts(conn)
    after = _duplicate_shape(conn)

    logger.info("   %-20s unique=%s primary=%s secondary=%s", "flags",
                dupes.get(0, 0), dupes.get(2, 0), dupes.get(1, 0))
    for label, key in (("canonical rows", "canonical"), ("servable (not retired)", "servable"),
                       ("screened secondary", "screened_secondary"),
                       ("annotated secondary", "annotated_secondary")):
        logger.info("   %-20s %s → %s (%+d)", label, before[key], after[key],
                    after[key] - before[key])
    logger.info("   %-20s %s → %s", "annotations",
                before_counts["annotated"], after_counts["annotated"])
    logger.info("   %-20s %s → %s", "screened",
                before_counts["screened"], after_counts["screened"])
    logger.info("   %-20s %s → %s", "rows", before_counts["results"], after_counts["results"])

    if (after_counts["annotated"] != before_counts["annotated"]
            or after_counts["screened"] != before_counts["screened"]
            or after_counts["results"] != before_counts["results"]):
        logger.error("❌ the store changed shape — refusing to call this a success")
        return 1

    db.set_meta(conn, "canonical_rows", str(after["canonical"]))
    db.set_meta(conn, "recomputed_duplicates_at",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    conn.commit()
    logger.info("✅ %s canonical rows folded away — only the duplicate flag was written",
                max(before["canonical"] - after["canonical"], 0))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bulk-update lanes for the check_ip SQLite store.")
    parser.add_argument("--recompute-duplicates", action="store_true", required=True,
                        help="Re-elect the canonical row of every group on the canonical link "
                             "(issue #291) and stop. Writes only `duplicate`.")
    parser.parse_args(argv)

    # Reconfigure before basicConfig grabs sys.stdout — the log lines carry
    # emoji and this runs under capture on Windows (CLAUDE.md gotcha).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    conn = db.connect()
    logger.info("💾 store: %s", db.db_path())
    return _recompute_duplicates(conn)


if __name__ == "__main__":
    raise SystemExit(main())
