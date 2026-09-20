"""Regression tests for the check_ip store, queue and review layer (issue #286).

The load-bearing guarantee here is the one in the middle: the screening pass
proposes, it never decides. ``check_ip.screen.record_verdict`` must not be able
to touch ``ok`` / ``person`` / ``chat`` / ``report`` / ``fixed`` — those are ten
months of manual triage and the skill runs unattended against them.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db, process, review, screen  # noqa: E402


def _seed(conn, rows):
    """Insert result rows, returning their ids in order."""
    ids = []
    for row in rows:
        cur = conn.execute(
            """
            insert into results
                (local_image, uploaded_url, found_link, title, duplicate, match_type,
                 source, post_date, search_date, "order", ok, person, chat, report, fixed,
                 poster_key)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("local_image", "img.png"), row.get("uploaded_url", "https://i/x.png"),
                row["found_link"], row.get("title", ""), row.get("duplicate", 0),
                row.get("match_type", "Exact Match"), row.get("source", "LinkedIn"),
                row.get("post_date"), row.get("search_date", "2026-01-01 00:00:00"),
                row.get("order", 1), row.get("ok"), row.get("person"), row.get("chat"),
                row.get("report"), row.get("fixed"),
                # Derived here exactly as upsert_results derives it in production,
                # so the queue's ranking is exercised the way it really runs.
                db.poster_key_for(row["found_link"]),
            ),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


class CheckIpStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self._tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    # ── the guarantee the skill depends on ────────────────────────────────

    def test_record_verdict_never_touches_owner_columns(self):
        """A verdict may be written over a fully-annotated row without changing it."""
        (row_id,) = _seed(self.conn, [{
            "found_link": "https://example.test/a",
            "ok": 0, "person": "contact-ref", "chat": "thread-ref",
            "report": "1", "fixed": 1,
        }])
        before = dict(self.conn.execute(
            "select ok, person, chat, report, fixed from results where id = ?", (row_id,)
        ).fetchone())

        # Worst-case write: an infringement carrying both aggravators, which
        # touches every screening column there is.
        screen.record_verdict(self.conn, row_id, verdict="infringement",
                              reason="no mention anywhere", poster_url="https://example.test/p",
                              promotional=True, altered=True)

        after = dict(self.conn.execute(
            "select ok, person, chat, report, fixed from results where id = ?", (row_id,)
        ).fetchone())
        self.assertEqual(before, after, "record_verdict altered an owner column")

        written = dict(self.conn.execute(
            "select screen_verdict, screen_reason, poster_url, screened_at, screen_source, "
            "screen_promotional, screen_altered from results where id = ?", (row_id,)
        ).fetchone())
        self.assertEqual(written["screen_verdict"], "infringement")
        self.assertEqual(written["screen_reason"], "no mention anywhere")
        self.assertEqual(written["poster_url"], "https://example.test/p")
        self.assertIsNotNone(written["screened_at"])
        self.assertEqual(written["screen_promotional"], 1)
        self.assertEqual(written["screen_altered"], 1)

    def test_record_verdict_rejects_an_unknown_verdict(self):
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/b"}])
        with self.assertRaises(ValueError):
            screen.record_verdict(self.conn, row_id, verdict="probably-bad")
        self.assertIsNone(self.conn.execute(
            "select screen_verdict from results where id = ?", (row_id,)
        ).fetchone()["screen_verdict"])

    # ── the credit policy: severity and its aggravators ───────────────────

    def test_severity_ranks_aggravators(self):
        """0 nothing to act on · 1 no mention · 2 one aggravator · 3 all three."""
        self.assertEqual(db.severity("acceptable"), 0)
        self.assertEqual(db.severity("unclear"), 0, "an unresolved row is not a mild finding")
        self.assertEqual(db.severity("infringement"), 1)
        self.assertEqual(db.severity("infringement", promotional=True), 2)
        self.assertEqual(db.severity("infringement", altered=True), 2)
        self.assertEqual(db.severity("infringement", promotional=True, altered=True), 3)

    def test_severity_sql_agrees_with_python(self):
        """The ordering expression and severity() must never drift apart.

        There are two implementations of the same rule — one for sorting in
        SQL, one for reporting in Python — so the test compares them across
        every combination rather than trusting either alone.
        """
        cases = [
            ("acceptable", False, False), ("unclear", False, False),
            ("infringement", False, False), ("infringement", True, False),
            ("infringement", False, True), ("infringement", True, True),
        ]
        for verdict, promo, altered in cases:
            (row_id,) = _seed(self.conn, [{"found_link": f"https://e.test/{verdict}{promo}{altered}"}])
            screen.record_verdict(self.conn, row_id, verdict=verdict,
                                  promotional=promo, altered=altered)
            from_sql = self.conn.execute(
                f"select ({db.severity_sql('results')}) as s from results where id = ?", (row_id,)
            ).fetchone()["s"]
            with self.subTest(verdict=verdict, promotional=promo, altered=altered):
                self.assertEqual(from_sql, db.severity(verdict, promo, altered))

    def test_aggravators_are_cleared_on_a_non_infringement_verdict(self):
        """Flags only mean something next to an infringement.

        Left set on an `acceptable` row, the stored severity would disagree
        with severity(), and the tab would sort a cleared row above real ones.

        Nothing stops a worker passing `--promotional` next to `acceptable`,
        so the flags are asserted here rather than left to default — otherwise
        this test passes whether or not the clearing exists.
        """
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/flip"}])
        for verdict in ("acceptable", "unclear"):
            screen.record_verdict(self.conn, row_id, verdict=verdict,
                                  reason="owner is tagged in the caption after all",
                                  promotional=True, altered=True)
            row = self.conn.execute(
                "select screen_promotional, screen_altered from results where id = ?", (row_id,)
            ).fetchone()
            with self.subTest(verdict=verdict):
                self.assertEqual((row["screen_promotional"], row["screen_altered"]), (0, 0))
                self.assertEqual(db.severity(verdict, True, True), 0)

    # ── poster identity, which drives the queue ranking ───────────────────

    def test_poster_key_extraction(self):
        cases = {
            "https://www.linkedin.com/posts/someslug_a-headline-activity-7448108730174390272-5iOu":
                "someslug",
            "https://www.linkedin.com/posts/Mixed.Case_x-activity-1-a": "mixed.case",
            "https://www.linkedin.com/in/someprofile": "someprofile",
            "https://www.linkedin.com/company/somecompany/": "somecompany",
            # No slug in the path at all — must stay None rather than become "activity".
            "https://www.linkedin.com/posts/activity-7385170299475972096-1YPV": None,
            "https://example.test/not-linkedin": None,
            "": None,
            None: None,
        }
        for link, expected in cases.items():
            with self.subTest(link=link):
                self.assertEqual(db.poster_key_for(link), expected)

    def test_queue_ranks_repeat_offenders_ahead_of_one_offs(self):
        """A poster holding several pending rows outranks a more-reused image.

        This is the point of the ranking: one conversation settles several
        findings, so it beats the single hit on the most-copied illustration.
        """
        self.conn.executemany(
            "insert into images (filename, linkedin_count) values (?, ?)",
            [("viral.png", 900), ("rare.png", 2)],
        )
        self.conn.commit()
        _seed(self.conn, [
            {"found_link": "https://www.linkedin.com/posts/oneoff_x-activity-1-a",
             "local_image": "viral.png"},
            {"found_link": "https://www.linkedin.com/posts/serial_x-activity-2-a",
             "local_image": "rare.png"},
            {"found_link": "https://www.linkedin.com/posts/serial_y-activity-3-a",
             "local_image": "rare.png"},
            {"found_link": "https://www.linkedin.com/posts/serial_z-activity-4-a",
             "local_image": "rare.png"},
        ])
        served = screen.next_batch(self.conn, limit=10, source="LinkedIn")
        self.assertEqual(served[0]["local_image"], "rare.png")
        self.assertEqual(served[0]["poster_pending"], 3)
        self.assertEqual(served[-1]["poster_pending"], 1)

    def test_queue_excludes_the_owners_own_posts(self):
        """The owner's own account must never reach the screening queue.

        It is by far the largest poster in the real store — 491 pending rows
        against 78 for the biggest genuine reuser — so without this the
        ranking serves his own posts first and the first fifty batches are
        spent screening them.
        """
        _seed(self.conn, [
            {"found_link": "https://www.linkedin.com/posts/ferraroroberto_a-activity-1-a"},
            {"found_link": "https://www.linkedin.com/posts/ferraroroberto_b-activity-2-a"},
            {"found_link": "https://www.linkedin.com/posts/somebodyelse_c-activity-3-a"},
        ])
        served = screen.next_batch(self.conn, limit=10, source="LinkedIn",
                                   exclude_posters=["ferraroroberto"])
        self.assertEqual([r["found_link"].split("/posts/")[1].split("_")[0] for r in served],
                         ["somebodyelse"])

        # Without the exclusion the owner's two rows outrank the single one.
        unfiltered = screen.next_batch(self.conn, limit=10, source="LinkedIn")
        self.assertEqual(len(unfiltered), 3)
        self.assertEqual(unfiltered[0]["poster_pending"], 2)

    def test_refresh_poster_keys_backfills_without_touching_anything_else(self):
        (row_id,) = _seed(self.conn, [{
            "found_link": "https://www.linkedin.com/posts/backfill_x-activity-9-a",
            "ok": 1, "person": "contact-ref",
        }])
        self.conn.execute("update results set poster_key = null where id = ?", (row_id,))
        self.conn.commit()

        self.assertEqual(db.refresh_poster_keys(self.conn), 1)

        row = self.conn.execute(
            "select poster_key, ok, person from results where id = ?", (row_id,)
        ).fetchone()
        self.assertEqual(row["poster_key"], "backfill")
        self.assertEqual((row["ok"], row["person"]), (1, "contact-ref"))

    def test_ensure_schema_adds_columns_to_a_store_that_predates_them(self):
        """`create table if not exists` is a no-op on an existing table.

        A store migrated before these columns existed would silently never get
        them, so ensure_schema has an additive alter pass. Rebuild that older
        shape and prove the pass fills it in without disturbing the row.
        """
        older = Path(self._tmp.name) / "older.db"
        # The v1 shape: every base column, none of the three added later. The
        # indexes in schema.sql span both sets, which is what makes the order
        # of the alter pass load-bearing.
        with closing(sqlite3.connect(older)) as raw:
            raw.execute(
                "create table results ("
                " id integer primary key, local_image text, uploaded_url text,"
                " found_link text not null, title text, duplicate integer default 0,"
                " match_type text, source text, post_date text, search_date text,"
                ' "order" integer, ok integer, person text, chat text, report text,'
                " fixed integer, screen_verdict text, screen_reason text,"
                " screened_at text, screen_source text, poster_url text)"
            )
            raw.execute("insert into results (local_image, found_link, ok) values (?, ?, ?)",
                        ("img.png", "https://example.test/old", 0))
            raw.commit()

        with closing(db.connect(older)) as conn:
            columns = {r["name"] for r in conn.execute("pragma table_info(results)")}
            for added, _decl in db.ADDED_RESULT_COLUMNS:
                self.assertIn(added, columns, f"ensure_schema did not add {added}")
            row = conn.execute("select ok, found_link from results").fetchone()
            self.assertEqual(row["ok"], 0, "the additive pass disturbed an owner column")

    # ── the queue ─────────────────────────────────────────────────────────

    def test_queue_skips_decided_screened_and_secondary_rows(self):
        ids = _seed(self.conn, [
            {"found_link": "https://example.test/pending"},
            {"found_link": "https://example.test/decided", "ok": 1},
            {"found_link": "https://example.test/secondary", "duplicate": 1},
            {"found_link": "https://example.test/other-platform", "source": "Instagram"},
        ])
        screen.record_verdict(self.conn, ids[0], verdict="unclear")
        served = screen.next_batch(self.conn, limit=50, source="LinkedIn")
        self.assertEqual(served, [], "an already-screened row was served again")

        served = screen.next_batch(self.conn, limit=50, source="LinkedIn",
                                   include_screened=True)
        self.assertEqual([r["id"] for r in served], [ids[0]],
                         "expected only the pending canonical LinkedIn row")

    def test_open_web_filter_selects_unattributed_rows_only(self):
        """`(open web)` must be a NULL test, not "no filter"."""
        _seed(self.conn, [
            {"found_link": "https://example.test/li", "source": "LinkedIn"},
            {"found_link": "https://example.test/blog", "source": None},
        ])
        served = screen.next_batch(self.conn, limit=10, source=db.OPEN_WEB)
        self.assertEqual([r["found_link"] for r in served], ["https://example.test/blog"])

        frame = review.results_frame(self.conn, source=db.OPEN_WEB)
        self.assertEqual(list(frame["found_link"]), ["https://example.test/blog"])

        self.assertEqual(screen.stats(self.conn, source=db.OPEN_WEB)["canonical"], 1)
        self.assertEqual(screen.stats(self.conn, source=None)["canonical"], 2,
                         "None must still mean every platform")

    # ── the retired match type (issue #292) ───────────────────────────────

    def test_queue_serves_only_exact_matches(self):
        """A `Similar Match` is never screened, retired or not.

        Both guards are asserted separately: the explicit `match_type` test is
        what protects a store the retirement migration has not reached yet, so
        a test that only ever saw retired rows would pass with it deleted.
        """
        exact, similar_retired, similar_live = _seed(self.conn, [
            {"found_link": "https://example.test/exact", "match_type": "Exact Match"},
            {"found_link": "https://example.test/retired", "match_type": "Similar Match"},
            {"found_link": "https://example.test/live", "match_type": "Similar Match"},
        ])
        self.conn.execute("update results set retired = 1 where id = ?", (similar_retired,))
        self.conn.commit()

        served = screen.next_batch(self.conn, limit=50, source="LinkedIn")
        self.assertEqual([r["id"] for r in served], [exact],
                         "the queue served a Similar Match row")

        # …and not even with the re-check flag, which is the only other way in.
        served = screen.next_batch(self.conn, limit=50, source="LinkedIn",
                                   include_screened=True)
        self.assertEqual([r["id"] for r in served], [exact])
        self.assertNotIn(similar_live, [r["id"] for r in served],
                         "an un-retired Similar Match row reached the queue")

    def test_retire_similar_matches_flags_without_deleting(self):
        """The retirement is reversible and leaves every annotated row alone."""
        exact, plain, annotated = _seed(self.conn, [
            {"found_link": "https://example.test/exact", "match_type": "Exact Match"},
            {"found_link": "https://example.test/plain", "match_type": "Similar Match"},
            {"found_link": "https://example.test/judged", "match_type": "Similar Match",
             "ok": 1, "person": "contact-ref"},
        ])

        result = db.retire_similar_matches(self.conn)
        self.assertEqual(result, {"retired": 1, "already_retired": 0, "kept_annotated": 1})

        rows = {r["id"]: r for r in self.conn.execute(
            "select id, retired, ok, person, match_type from results")}
        self.assertEqual(len(rows), 3, "retirement deleted a row")
        self.assertEqual(rows[plain]["retired"], 1)
        self.assertEqual(rows[exact]["retired"], 0, "an Exact Match row was retired")
        self.assertEqual(rows[annotated]["retired"], 0,
                         "a row carrying an owner decision was hidden from the tab")
        self.assertEqual((rows[annotated]["ok"], rows[annotated]["person"]), (1, "contact-ref"))

        # Idempotent: a second pass finds nothing new to do.
        self.assertEqual(db.retire_similar_matches(self.conn),
                         {"retired": 0, "already_retired": 1, "kept_annotated": 1})

    def test_stats_reports_retired_rows_outside_the_workable_totals(self):
        _seed(self.conn, [
            {"found_link": "https://example.test/exact", "match_type": "Exact Match"},
            {"found_link": "https://example.test/similar", "match_type": "Similar Match"},
        ])
        before = screen.stats(self.conn, source="LinkedIn")
        self.assertEqual((before["canonical"], before["retired"]), (2, 0))

        db.retire_similar_matches(self.conn)
        after = screen.stats(self.conn, source="LinkedIn")
        self.assertEqual(after["canonical"], 1, "a retired row still counts as workable")
        self.assertEqual(after["retired"], 1)
        self.assertEqual(after["pending"], 1)

    def test_the_tab_does_not_list_retired_rows(self):
        _seed(self.conn, [
            {"found_link": "https://example.test/exact", "match_type": "Exact Match"},
            {"found_link": "https://example.test/similar", "match_type": "Similar Match"},
        ])
        db.retire_similar_matches(self.conn)

        frame = review.results_frame(self.conn, source="LinkedIn", status="everything")
        self.assertEqual(list(frame["found_link"]), ["https://example.test/exact"])
        self.assertEqual(review.overview(self.conn)["retired"], 1)
        self.assertEqual(review.overview(self.conn)["results"], 2,
                         "the search history must not shrink")

    def test_queue_ranks_most_reused_image_first(self):
        self.conn.executemany(
            "insert into images (filename, linkedin_count) values (?, ?)",
            [("rare.png", 2), ("viral.png", 900)],
        )
        self.conn.commit()
        _seed(self.conn, [
            {"found_link": "https://example.test/1", "local_image": "rare.png"},
            {"found_link": "https://example.test/2", "local_image": "viral.png"},
        ])
        served = screen.next_batch(self.conn, limit=10, source="LinkedIn")
        self.assertEqual(served[0]["local_image"], "viral.png")

    # ── duplicate marking ─────────────────────────────────────────────────

    def test_mark_duplicates_flags_oldest_as_primary(self):
        _seed(self.conn, [
            {"found_link": "https://example.test/shared", "local_image": "a.png",
             "search_date": "2026-03-01 00:00:00"},
            {"found_link": "https://example.test/shared", "local_image": "b.png",
             "search_date": "2026-01-01 00:00:00"},
            {"found_link": "https://example.test/shared", "local_image": "c.png",
             "search_date": "2026-02-01 00:00:00"},
            {"found_link": "https://example.test/alone", "local_image": "d.png"},
        ])
        counts = db.mark_duplicates(self.conn)
        self.assertEqual(counts.get(2), 1, "exactly one primary per repeated URL")
        self.assertEqual(counts.get(1), 2)
        self.assertEqual(counts.get(0), 1, "a URL seen once stays unique")

        primary = self.conn.execute(
            "select local_image from results where duplicate = 2"
        ).fetchone()["local_image"]
        self.assertEqual(primary, "b.png", "the oldest sighting should be the primary")

    def test_same_link_from_both_searches_is_two_rows(self):
        """The row identity still spans match_type, so historic rows round-trip.

        `Similar Match` is no longer ingested (#292), but 139k of them are in
        the store paired with an exact-match row on the same URL. Narrowing the
        uniqueness constraint would collapse those pairs.
        """
        _seed(self.conn, [
            {"found_link": "https://example.test/x", "match_type": "Exact Match"},
            {"found_link": "https://example.test/x", "match_type": "Similar Match"},
        ])
        self.assertEqual(
            self.conn.execute("select count(*) as n from results").fetchone()["n"], 2)

    # ── owner writes ──────────────────────────────────────────────────────

    def test_apply_decisions_writes_only_edited_rows(self):
        ids = _seed(self.conn, [
            {"found_link": "https://example.test/1"},
            {"found_link": "https://example.test/2", "ok": 1},
        ])
        result = review.apply_decisions(self.conn, [
            {"id": ids[0], "ok": 0, "person": "someone", "chat": None, "report": None,
             "fixed": None},
            {"id": ids[1], "ok": 1, "person": None, "chat": None, "report": None,
             "fixed": None},
        ])
        self.assertEqual(result, {"changed": 1, "flagged": 1},
                         "an unchanged row should not count as a write")
        self.assertEqual(self.conn.execute(
            "select ok from results where id = ?", (ids[0],)).fetchone()["ok"], 0)

    def test_apply_decisions_round_trips_a_checkbox(self):
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/1"}])
        review.apply_decisions(self.conn, [
            {"id": row_id, "ok": 0, "person": None, "chat": None, "report": None, "fixed": True},
        ])
        self.assertEqual(self.conn.execute(
            "select fixed from results where id = ?", (row_id,)).fetchone()["fixed"], 1)


class CheckIpProcessTests(unittest.TestCase):
    """The ported pure helpers — behaviour must match the sibling repo's."""

    def test_extract_linkedin_post_date(self):
        # Synthetic id built as (epoch_ms << 22) for 2023-06-15T12:00Z — exercises
        # the real arithmetic without embedding a third party's post id in a
        # public repo.
        url = "https://www.linkedin.com/feed/update/urn:li:activity:7075079494041600000/"
        self.assertEqual(process.extract_post_date(url), "2023-06-15 12:00:00 UTC")

    def test_extract_twitter_post_date(self):
        # Synthetic snowflake for 2022-06-15T12:00Z: (epoch_ms - twitter_epoch) << 22.
        url = "https://x.com/someone/status/1537042233553846272"
        self.assertEqual(process.extract_post_date(url), "2022-06-15 12:00:00 UTC")

    def test_extract_post_date_returns_none_off_platform(self):
        self.assertIsNone(process.extract_post_date("https://example.test/blog/post"))

    def test_extract_post_date_survives_a_junk_id(self):
        self.assertIsNone(process.extract_post_date("https://x.com/a/status/999999999999999999999999"))

    def test_identify_source(self):
        cases = {
            "https://www.linkedin.com/posts/someone_thing-activity-7392771581359513600-wLzA": "LinkedIn",
            "https://x.com/someone/status/1985821858432684125": "Twitter/X",
            "https://www.instagram.com/someone/p/Ab1Cd2Ef3Gh/": "Instagram",
            "https://example.test/gallery": None,
        }
        for url, expected in cases.items():
            self.assertEqual(process.identify_source(url), expected, url)

    def test_build_rows_treats_match_type_as_part_of_identity(self):
        payload = {"exact_matches": [{"link": "https://example.test/a", "title": "A"}]}
        existing: set = set()
        rows, added, skipped = process.build_rows(payload, "exact_matches", "i.png",
                                                  "https://i/x.png", "2026-01-01 00:00:00", existing)
        self.assertEqual((added, skipped), (1, 0))
        self.assertEqual(rows[0]["match_type"], "Exact Match")

        # Re-running the same search is a duplicate.
        payload = {"exact_matches": [{"link": "https://example.test/a", "title": "A"}]}
        _, added, skipped = process.build_rows(payload, "exact_matches", "i.png",
                                               "https://i/x.png", "2026-01-01 00:00:00", existing)
        self.assertEqual((added, skipped), (0, 1))

    def test_build_rows_no_longer_ingests_the_visual_match_section(self):
        """A new search stores no `Similar Match` row (issue #292).

        Asserted on a payload Lens really would return — a populated
        `visual_matches` list — so an empty result proves the section is
        dropped, not that the fixture had nothing in it.
        """
        payload = {"visual_matches": [
            {"link": "https://example.test/lookalike", "title": "someone else's chart"},
            {"link": "https://example.test/lookalike-2", "title": "another one"},
        ]}
        rows, added, skipped = process.build_rows(payload, "visual_matches", "i.png",
                                                  "https://i/x.png", "2026-01-01 00:00:00", set())
        self.assertEqual((rows, added, skipped), ([], 0, 0))
        self.assertNotIn("visual_matches", process.MATCH_TYPES)
        self.assertEqual(process.RETIRED_MATCH_TYPES["visual_matches"], "Similar Match",
                         "the retired section must stay named, not be deleted")

    def test_is_recently_processed_uses_the_high_linkedin_window(self):
        from datetime import datetime, timedelta
        recent = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        # 10 days ago: inside the 180-day standard window…
        self.assertTrue(process.is_recently_processed(recent, 0, 30, 6, 180))
        # …but past the 6-day window a heavily-reused image gets.
        self.assertFalse(process.is_recently_processed(recent, 99, 30, 6, 180))

    def test_is_recently_processed_treats_an_unparseable_date_as_never(self):
        self.assertFalse(process.is_recently_processed("not a date", 0, 30, 6, 180))
        self.assertFalse(process.is_recently_processed(None, 0, 30, 6, 180))


if __name__ == "__main__":
    unittest.main()
