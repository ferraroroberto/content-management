"""One-shot Excel → SQLite migration for the illustration copyright check.

Reads the three workbooks the sibling repo's pipeline wrote and lands them in
``results/check_ip/check_ip.db``, then extracts every SerpAPI payload to a
sidecar file. Idempotent: running it twice imports the same rows over
themselves and changes nothing, which ``--report`` will show.

What it deliberately preserves:

* **Every owner annotation.** ``ok`` / ``person`` / ``chat`` / ``report`` /
  ``fixed`` are the ten months of manual triage this whole store exists to
  protect. They are imported verbatim and never overwritten on a re-run.
* **Rows that look like duplicates but are not.** The same link reached by the
  exact-match and the visual-match search, or re-found on a later date, is a
  separate finding — and eleven such groups carry *conflicting* ok values. The
  natural key includes match_type and search_date so none of them merge.
* **The truth about truncated payloads.** Excel capped a cell at 32,767
  characters, so 47% of the stored API responses are cut mid-JSON. Those are
  written to disk as ``.json.truncated`` and flagged ``raw_truncated = 1``
  rather than imported as if they were intact.

Usage::

    python -m check_ip.migrate --report           # reconcile only, write nothing
    python -m check_ip.migrate                    # import
    python -m check_ip.migrate --skip-raw         # import, leave the payloads alone
    python -m check_ip.migrate --retire-similar   # retire the Similar Match rows (#292)
    python -m check_ip.migrate --recompute-duplicates  # re-elect the canonical rows (#291)

The last two are the store's bulk-update lanes: no workbooks needed, no DELETE,
and each reports what it changed plus the annotation count either side of it.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db, process  # noqa: E402

logger = logging.getLogger("check_ip.migrate")

# Excel's hard limit on the characters in one cell. A stored payload exactly
# this long was cut off by the writer, not by the API.
EXCEL_CELL_CAP = 32767

METADATA_FILE = "metadata.xlsx"
RESULTS_FILE = "search_results_database.xlsx"
API_HISTORY_FILE = "api_history_database.xlsx"

_IMAGE_COLUMNS = [
    "filename", "imgur_url", "last_processed_date", "total_links",
    "exact_match_count", "similar_match_count", "linkedin_count",
    "instagram_count", "twitter_count", "facebook_count", "pinterest_count",
]

_RESULT_COLUMNS = [
    "local_image", "uploaded_url", "found_link", "title", "duplicate",
    "match_type", "source", "post_date", "search_date", "order",
    "ok", "person", "chat", "report", "fixed",
]

_API_COLUMNS = [
    "api_id", "search_type", "local_image", "imgur_url", "search_date",
    "api_status", "json_endpoint", "created_at", "processed_at",
    "total_time_taken", "engine", "url", "search_engine_query",
    "playground_link", "search_type_param",
]


def legacy_folder() -> Path:
    """Folder holding the three source workbooks."""
    folder = db.config().get("legacy_metadata_folder")
    if not folder:
        raise RuntimeError("check_ip.legacy_metadata_folder is not set in config.json")
    path = Path(folder)
    if not path.exists():
        raise FileNotFoundError(f"Legacy metadata folder not found: {path}")
    return path


def _clean(value):
    """Normalise a pandas cell to something sqlite3 accepts.

    NaN/NaT become NULL; everything else keeps its type. ``pd.isna`` raises on
    a non-scalar, which an Excel cell never is — the guard is there so a
    surprise value falls through as itself rather than crashing the import.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _int_or_none(value) -> Optional[int]:
    cleaned = _clean(value)
    if cleaned is None or cleaned == "":
        return None
    try:
        return int(float(cleaned))
    except (TypeError, ValueError):
        return None


def _text_or_none(value) -> Optional[str]:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    text = str(cleaned).strip()
    return text or None


# ---------------------------------------------------------------------------
# per-table import


def import_images(conn: sqlite3.Connection, folder: Path) -> dict:
    path = folder / METADATA_FILE
    frame = pd.read_excel(path)
    rows = []
    for _, row in frame.iterrows():
        filename = _text_or_none(row.get("filename"))
        if not filename:
            continue
        rows.append(
            (
                filename,
                _text_or_none(row.get("imgur_url")),
                _text_or_none(row.get("last_processed_date")),
                _int_or_none(row.get("total_links")) or 0,
                _int_or_none(row.get("exact_match_count")) or 0,
                _int_or_none(row.get("similar_match_count")) or 0,
                _int_or_none(row.get("linkedin_count")) or 0,
                _int_or_none(row.get("instagram_count")) or 0,
                _int_or_none(row.get("twitter_count")) or 0,
                _int_or_none(row.get("facebook_count")) or 0,
                _int_or_none(row.get("pinterest_count")) or 0,
            )
        )
    conn.executemany(
        f"""
        insert into images ({", ".join(_IMAGE_COLUMNS)})
        values ({", ".join("?" for _ in _IMAGE_COLUMNS)})
        on conflict(filename) do update set
            imgur_url           = excluded.imgur_url,
            last_processed_date = excluded.last_processed_date,
            total_links         = excluded.total_links,
            exact_match_count   = excluded.exact_match_count,
            similar_match_count = excluded.similar_match_count,
            linkedin_count      = excluded.linkedin_count,
            instagram_count     = excluded.instagram_count,
            twitter_count       = excluded.twitter_count,
            facebook_count      = excluded.facebook_count,
            pinterest_count     = excluded.pinterest_count
        """,
        rows,
    )
    conn.commit()
    return {"source_rows": len(frame), "imported": len(rows), "skipped_no_filename": len(frame) - len(rows)}


def import_results(conn: sqlite3.Connection, folder: Path) -> dict:
    path = folder / RESULTS_FILE
    frame = pd.read_excel(path)

    stats = {"source_rows": len(frame), "imported": 0, "skipped_no_link": 0, "annotations": 0}
    rows = []
    for _, row in frame.iterrows():
        link = _text_or_none(row.get("found_link"))
        image = _text_or_none(row.get("local_image"))
        if not link or not image:
            stats["skipped_no_link"] += 1
            continue
        owner = (
            _int_or_none(row.get("ok")),
            _text_or_none(row.get("person")),
            _text_or_none(row.get("chat")),
            _text_or_none(row.get("report")),
            _int_or_none(row.get("fixed")),
        )
        if any(v is not None for v in owner):
            stats["annotations"] += 1
        rows.append(
            (
                image,
                _text_or_none(row.get("uploaded_url")),
                link,
                _text_or_none(row.get("title")),
                _int_or_none(row.get("duplicate")) or 0,
                _text_or_none(row.get("match_type")),
                _text_or_none(row.get("source")),
                _text_or_none(row.get("post_date")),
                _text_or_none(row.get("search_date")),
                _int_or_none(row.get("order")),
                *owner,
            )
        )

    # The owner columns use `coalesce(excluded, existing)` so a re-run can add a
    # newly-annotated row without ever blanking one already in the store.
    conn.executemany(
        """
        insert into results
            (local_image, uploaded_url, found_link, title, duplicate, match_type,
             source, post_date, search_date, "order", ok, person, chat, report, fixed)
        values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(local_image, found_link, match_type, search_date) do update set
            uploaded_url = excluded.uploaded_url,
            title        = excluded.title,
            duplicate    = excluded.duplicate,
            source       = excluded.source,
            post_date    = excluded.post_date,
            "order"      = excluded."order",
            ok           = coalesce(excluded.ok,     results.ok),
            person       = coalesce(excluded.person, results.person),
            chat         = coalesce(excluded.chat,   results.chat),
            report       = coalesce(excluded.report, results.report),
            fixed        = coalesce(excluded.fixed,  results.fixed)
        """,
        rows,
    )
    conn.commit()
    stats["imported"] = len(rows)
    return stats


def import_api_history(conn: sqlite3.Connection, folder: Path, *, write_raw: bool = True) -> dict:
    path = folder / API_HISTORY_FILE
    frame = pd.read_excel(path)

    stats = {
        "source_rows": len(frame), "imported": 0,
        "raw_written": 0, "raw_truncated": 0, "raw_missing": 0,
    }
    rows = []
    for _, row in frame.iterrows():
        api_id = _text_or_none(row.get("api_id"))
        payload = row.get("raw_json")
        payload = "" if payload is None or (isinstance(payload, float) and pd.isna(payload)) else str(payload)

        truncated = len(payload) >= EXCEL_CELL_CAP
        raw_path = None
        if not payload:
            stats["raw_missing"] += 1
        elif write_raw and api_id:
            target = db.raw_sidecar(api_id, truncated=truncated)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload, encoding="utf-8")
            raw_path = str(target.relative_to(db.store_dir())).replace("\\", "/")
            stats["raw_written"] += 1
        if truncated:
            stats["raw_truncated"] += 1

        rows.append(
            (
                api_id,
                _text_or_none(row.get("search_type")),
                _text_or_none(row.get("local_image")),
                _text_or_none(row.get("imgur_url")),
                _text_or_none(row.get("search_date")),
                _text_or_none(row.get("api_status")),
                _text_or_none(row.get("json_endpoint")),
                _text_or_none(row.get("created_at")),
                _text_or_none(row.get("processed_at")),
                _clean(row.get("total_time_taken")),
                _text_or_none(row.get("engine")),
                _text_or_none(row.get("url")),
                _text_or_none(row.get("search_engine_query")),
                _text_or_none(row.get("playground_link")),
                _text_or_none(row.get("search_type_param")),
                raw_path,
                1 if truncated else 0,
            )
        )

    columns = _API_COLUMNS + ["raw_path", "raw_truncated"]
    conn.executemany(
        f"""
        insert into api_history ({", ".join(columns)})
        values ({", ".join("?" for _ in columns)})
        on conflict(api_id) do update set
            raw_path      = coalesce(excluded.raw_path, api_history.raw_path),
            raw_truncated = excluded.raw_truncated
        """,
        rows,
    )
    conn.commit()
    stats["imported"] = len(rows)
    return stats


# ---------------------------------------------------------------------------
# entry point


def reconcile(conn: sqlite3.Connection, folder: Path) -> dict:
    """Compare what is in the store against what the workbooks hold."""
    stored = db.counts(conn)
    source = {}
    for label, filename, column in (
        ("images", METADATA_FILE, "filename"),
        ("results", RESULTS_FILE, "found_link"),
        ("api_history", API_HISTORY_FILE, "api_id"),
    ):
        path = folder / filename
        if not path.exists():
            source[label] = None
            continue
        frame = pd.read_excel(path, usecols=[column])
        source[label] = len(frame)
    return {"stored": stored, "source": source}


def _retire_similar(conn: sqlite3.Connection) -> int:
    """Run the ``Similar Match`` retirement and prove it cost no annotation.

    The same guard the full import ends on: the owner's decisions are the one
    thing in this store that cannot be regenerated, so the count is taken
    before and after and a change is a failure, not a warning.
    """
    before = db.counts(conn)
    result = db.retire_similar_matches(conn)
    after = db.counts(conn)

    logger.info("🗃️  retiring '%s' rows — %s", process.SIMILAR_MATCH, db.RETIRED_REASON)
    logger.info("   %-16s %s", "retired now", result["retired"])
    logger.info("   %-16s %s", "already retired", result["already_retired"])
    logger.info("   %-16s %s (left visible — the owner ruled on them)",
                "kept annotated", result["kept_annotated"])
    logger.info("   %-16s %s → %s", "annotations", before["annotated"], after["annotated"])
    logger.info("   %-16s %s → %s", "rows", before["results"], after["results"])

    if after["annotated"] != before["annotated"] or after["results"] != before["results"]:
        logger.error("❌ the store changed shape — refusing to call this a success")
        return 1

    db.set_meta(conn, "retired_similar_match_rows",
                str(result["retired"] + result["already_retired"]))
    db.set_meta(conn, "retired_similar_match_at",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    conn.commit()
    logger.info("✅ retirement complete — nothing deleted, clearing `retired` undoes it")
    return 0


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
    parser = argparse.ArgumentParser(description="Migrate the check_ip Excel store into SQLite.")
    parser.add_argument("--report", action="store_true",
                        help="Reconcile the store against the workbooks and write nothing.")
    parser.add_argument("--skip-raw", action="store_true",
                        help="Import api_history metadata without extracting the payload sidecars.")
    parser.add_argument("--retire-similar", action="store_true",
                        help="Retire the stored 'Similar Match' rows (issue #292) and stop. "
                             "Sets a flag; deletes nothing. Needs no workbooks.")
    parser.add_argument("--recompute-duplicates", action="store_true",
                        help="Re-elect the canonical row of every group on the canonical link "
                             "(issue #291) and stop. Writes only `duplicate`. Needs no workbooks.")
    args = parser.parse_args(argv)

    # Reconfigure before basicConfig grabs sys.stdout — the log lines carry
    # emoji and this runs under capture on Windows (CLAUDE.md gotcha).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    # Stands on its own: it touches only the store, so it must not demand the
    # legacy workbooks a machine that already migrated no longer has.
    if args.retire_similar or args.recompute_duplicates:
        conn = db.connect()
        logger.info("💾 store: %s", db.db_path())
        if args.retire_similar:
            return _retire_similar(conn)
        return _recompute_duplicates(conn)

    folder = legacy_folder()
    conn = db.connect()
    logger.info("📂 source workbooks: %s", folder)
    logger.info("💾 store: %s", db.db_path())

    if args.report:
        rec = reconcile(conn, folder)
        logger.info("📊 reconciliation (no writes)")
        for table in ("images", "results", "api_history"):
            logger.info("   %-12s stored=%-8s source=%s", table,
                        rec["stored"][table], rec["source"][table])
        logger.info("   %-12s stored=%s", "annotated", rec["stored"]["annotated"])
        logger.info("   %-12s stored=%s", "screened", rec["stored"]["screened"])
        logger.info("   %-12s stored=%s (%s)", "retired", rec["stored"]["retired"],
                    db.RETIRED_REASON)
        return 0

    before = db.counts(conn)

    logger.info("🖼️  importing images…")
    img = import_images(conn, folder)
    logger.info("   %s of %s rows (%s skipped)", img["imported"], img["source_rows"],
                img["skipped_no_filename"])

    logger.info("🔗 importing search results…")
    res = import_results(conn, folder)
    logger.info("   %s of %s rows (%s skipped, no link) · %s carry owner annotations",
                res["imported"], res["source_rows"], res["skipped_no_link"], res["annotations"])

    logger.info("📜 importing api history…")
    api = import_api_history(conn, folder, write_raw=not args.skip_raw)
    logger.info("   %s of %s rows · %s payloads written · %s truncated by Excel · %s empty",
                api["imported"], api["source_rows"], api["raw_written"],
                api["raw_truncated"], api["raw_missing"])

    logger.info("🔢 refreshing per-image counts…")
    db.refresh_image_counts(conn)

    logger.info("👤 deriving poster keys…")
    logger.info("   %s rows keyed", db.refresh_poster_keys(conn))

    # The workbooks still hold the retired category, so an import re-lands
    # those rows un-flagged. Retiring here is what keeps a re-run idempotent
    # in behaviour and not just in row count.
    if _retire_similar(conn) != 0:
        return 1

    # After the retirement, never before it: the election prefers a live row
    # over a retired one, and a row re-landed from the workbooks is not flagged
    # yet. Marking first would let a freshly-imported Similar Match mirror take
    # the primary slot and leave its live twin unservable (issue #291).
    if _recompute_duplicates(conn) != 0:
        return 1

    after = db.counts(conn)
    db.set_meta(conn, "last_migration_source", str(folder))
    db.set_meta(conn, "last_migration_results", str(after["results"]))
    conn.commit()

    logger.info("📊 SUMMARY")
    for key in ("images", "results", "api_history", "annotated", "screened", "retired"):
        delta = after[key] - before[key]
        logger.info("   %-12s %-8s (%+d)", key, after[key], delta)

    if after["annotated"] < before["annotated"]:
        logger.error("❌ annotation count dropped — refusing to call this a success")
        return 1

    logger.info("✅ migration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
