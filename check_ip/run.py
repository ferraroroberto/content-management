"""Reverse-image search run — the pipeline that fills the store.

For every illustration past its re-search window: make sure it has a public
URL (upload to Imgur once), run a Google Lens search against that URL, and
record every match. Ported from the sibling repo's ``linkedin/check_ip/main.py``;
the orchestration is the same, the Excel layer is gone.

**Each image costs a SerpAPI call.** ``--dry-run`` reports exactly what a live
run would search and spends nothing; run it first.

Usage::

    python -m check_ip.run --dry-run           # what would be searched
    python -m check_ip.run --limit 10          # a live run over ten images
    python -m check_ip.run                     # every image that is due
    python -m check_ip.run --force             # ignore the re-search windows
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

from check_ip import db, process  # noqa: E402
from check_ip.imgur import ImgurClient  # noqa: E402
from check_ip.lens import LensClient  # noqa: E402

logger = logging.getLogger("check_ip.run")


def _due_images(conn: sqlite3.Connection, cfg: dict, paths: list[Path], *, force: bool) -> list[Path]:
    """Filter the images on disk down to the ones worth searching again."""
    thresholds = cfg.get("processing_thresholds", {})
    high_threshold = int(thresholds.get("linkedin_high_threshold", 30))
    high_days = int(thresholds.get("high_linkedin_days", 6))
    standard_days = int(thresholds.get("standard_days", 180))

    stored = {
        row["filename"]: row
        for row in conn.execute(
            "select filename, imgur_url, last_processed_date, linkedin_count from images"
        ).fetchall()
    }

    due = []
    for path in paths:
        row = stored.get(path.name)
        if row is None or force:
            due.append(path)
            continue
        if not process.is_recently_processed(
            row["last_processed_date"], row["linkedin_count"] or 0,
            high_threshold, high_days, standard_days,
        ):
            due.append(path)
    return due


def _existing_pairs(conn: sqlite3.Connection, filename: str) -> set[tuple[str, str]]:
    """(found_link, match_type) pairs already stored for one image."""
    rows = conn.execute(
        "select found_link, match_type from results where local_image = ?", (filename,)
    ).fetchall()
    return {(r["found_link"], r["match_type"]) for r in rows}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the illustration reverse-image search.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be searched; make no API calls and write nothing.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Cap the number of images searched (0 = every image that is due).")
    parser.add_argument("--force", action="store_true",
                        help="Search every image, ignoring the re-search windows.")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    cfg = db.config()
    images_folder = Path(cfg["images_folder"])
    if not images_folder.exists():
        logger.error("❌ images folder not found: %s", images_folder)
        return 1

    conn = db.connect()
    paths = process.collect_image_paths(images_folder, cfg.get("allowed_extensions", []))
    if not paths:
        logger.error("❌ no images in %s", images_folder)
        return 1

    due = _due_images(conn, cfg, paths, force=args.force)
    if args.limit:
        due = due[: args.limit]

    settings = cfg.get("search_settings", {})
    search_types = [t for t, on in (("exact_matches", settings.get("do_exact_search", True)),
                                    ("visual_matches", settings.get("do_similar_search", False)))
                    if on]
    # A retired search type still costs a SerpAPI call and would now yield
    # nothing, so it is dropped here rather than in build_rows — the cheapest
    # place to not spend the money is before the call (issue #292).
    retired = [t for t in search_types if t in process.RETIRED_MATCH_TYPES]
    for search_type in retired:
        logger.warning("⚠️ %s is enabled in config but '%s' results are retired (#292) — "
                       "skipping it; set search_settings.do_similar_search to false",
                       search_type, process.RETIRED_MATCH_TYPES[search_type])
    search_types = [t for t in search_types if t not in process.RETIRED_MATCH_TYPES]
    if not search_types:
        logger.error("❌ no ingestable search type is enabled in config — nothing to do")
        return 1

    calls = len(due) * len(search_types)
    logger.info("📂 %s images on disk · %s due%s", len(paths), len(due),
                " (forced)" if args.force else "")
    logger.info("🔍 search types: %s", ", ".join(search_types))
    logger.info("💳 a live run would make %s SerpAPI call(s)", calls)

    if args.dry_run:
        logger.info("🧪 DRY RUN — nothing searched, nothing written")
        for path in due[:20]:
            logger.info("   would search: %s", path.name)
        if len(due) > 20:
            logger.info("   … and %s more", len(due) - 20)
        return 0

    if not due:
        logger.info("✅ nothing due — every image is inside its re-search window")
        return 0

    creds = db.credentials()
    missing = [k for k, v in creds.items() if not v]
    if missing:
        logger.error("❌ missing credential(s): %s — set them in config.json's "
                     "check_ip.api_keys or in the environment", ", ".join(missing))
        return 1

    lens = LensClient(creds["serpapi_key"], cfg.get("search_endpoint", "https://serpapi.com/search"))
    if not lens.validate():
        return 1
    imgur = ImgurClient(creds["imgur_client_id"], creds["imgur_access_token"])
    imgur.validate()  # advisory — an existing URL means most runs never upload

    searched = uploaded = added = skipped = failed = 0

    for path in due:
        filename = path.name
        row = conn.execute("select imgur_url from images where filename = ?", (filename,)).fetchone()
        image_url = row["imgur_url"] if row else None

        if not image_url:
            logger.info("🚀 %s — uploading (not hosted yet)", filename)
            image_url = imgur.upload(path)
            if not image_url:
                logger.error("❌ skipping %s — upload failed", filename)
                failed += 1
                continue
            uploaded += 1
        else:
            logger.info("♻️  %s — reusing hosted copy", filename)

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            """
            insert into images (filename, imgur_url, last_processed_date)
            values (?, ?, ?)
            on conflict(filename) do update set
                imgur_url           = excluded.imgur_url,
                last_processed_date = excluded.last_processed_date
            """,
            (filename, image_url, stamp),
        )
        conn.commit()

        existing = _existing_pairs(conn, filename)
        for search_type in search_types:
            data = lens.search(conn, image_url, filename, search_type)
            if data is None:
                failed += 1
                continue
            searched += 1
            rows, new, dupes = process.build_rows(data, search_type, filename, image_url,
                                                  stamp, existing)
            db.upsert_results(conn, rows)
            added += new
            skipped += dupes
            logger.info("   %s: +%s new, %s already known", search_type, new, dupes)

    logger.info("🔁 recomputing duplicate flags…")
    dupes = db.mark_duplicates(conn)
    db.refresh_image_counts(conn)
    # Rows inserted by this run are keyed on the way in; this catches any left
    # NULL by a store that predates the column.
    db.refresh_poster_keys(conn)

    logger.info("📊 SUMMARY")
    logger.info("   images searched   %s", len(due))
    logger.info("   searches made     %s", searched)
    logger.info("   uploads           %s", uploaded)
    logger.info("   new links         %s", added)
    logger.info("   already known     %s", skipped)
    logger.info("   failures          %s", failed)
    logger.info("   canonical rows    %s", dupes.get(0, 0) + dupes.get(2, 0))
    logger.info("✅ run complete" if not failed else "⚠️ run complete with %s failure(s)" % failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
