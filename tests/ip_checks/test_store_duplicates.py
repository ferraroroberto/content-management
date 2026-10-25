"""check_ip retired matches, duplicate marking, canonical links and owner writes.

See ``_store_fixtures`` for the guarantees the store tests pin.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db, review, screen  # noqa: E402
from tests.ip_checks._store_fixtures import StoreTestCase, _retire_similar, _seed  # noqa: E402


class DuplicateTests(StoreTestCase):
    """Which rows count, which one is primary, and what the owner writes."""

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

    def test_stats_reports_retired_rows_outside_the_workable_totals(self):
        _seed(self.conn, [
            {"found_link": "https://example.test/exact", "match_type": "Exact Match"},
            {"found_link": "https://example.test/similar", "match_type": "Similar Match"},
        ])
        before = screen.stats(self.conn, source="LinkedIn")
        self.assertEqual((before["canonical"], before["retired"]), (2, 0))

        _retire_similar(self.conn)
        after = screen.stats(self.conn, source="LinkedIn")
        self.assertEqual(after["canonical"], 1, "a retired row still counts as workable")
        self.assertEqual(after["retired"], 1)
        self.assertEqual(after["pending"], 1)

    def test_the_tab_does_not_list_retired_rows(self):
        _seed(self.conn, [
            {"found_link": "https://example.test/exact", "match_type": "Exact Match"},
            {"found_link": "https://example.test/similar", "match_type": "Similar Match"},
        ])
        _retire_similar(self.conn)

        frame = review.results_frame(self.conn, source="LinkedIn", status="everything")
        self.assertEqual(list(frame["found_link"]), ["https://example.test/exact"])
        self.assertEqual(review.overview(self.conn)["retired"], 1)
        self.assertEqual(review.overview(self.conn)["results"], 2,
                         "the search history must not shrink")

    def test_overview_counts_only_what_the_queue_can_serve(self):
        """The header and the queue line must not drift apart (the retired and
        secondary-duplicate rows are screened too, but no re-screen reaches them)."""
        live, retired, dup, retired_ind, dup_dead = _seed(self.conn, [
            {"found_link": f"https://example.test/{name}"}
            for name in ("live", "retired", "dup", "retired-ind", "dup-dead")
        ])
        for row_id in (live, retired, dup):
            screen.record_verdict(self.conn, row_id, verdict="unclear",
                                  outcome=db.OUTCOME_AMBIGUOUS, credit_ok=1)
        screen.record_verdict(self.conn, retired_ind, verdict="unclear", outcome=db.OUTCOME_AMBIGUOUS,
                              credit_ok=1, noncommercial_ok=2, unmodified_ok=1)
        screen.record_verdict(self.conn, dup_dead, verdict="unclear", outcome=db.OUTCOME_NOTHING_TO_ASSESS)
        self.conn.execute("update results set retired = 1 where id in (?, ?)", (retired, retired_ind))
        self.conn.execute("update results set duplicate = 1 where id in (?, ?)", (dup, dup_dead))
        self.conn.commit()

        queue = screen.stats(self.conn)
        overview = review.overview(self.conn)
        self.assertEqual((queue["screened"], queue["not_fully_assessed"],
                          queue["indeterminate"], queue["nothing_to_assess"]), (1, 1, 0, 0))
        for key in ("screened", "not_fully_assessed", "indeterminate", "nothing_to_assess"):
            self.assertEqual(overview[key], queue[key], key)
        served = screen.next_batch(self.conn, limit=10, unassessed=True)
        self.assertEqual([r["id"] for r in served], [live])

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

    # ── the canonical link: one post, many mirrors (issue #291) ───────────

    def test_canonical_link_folds_mirrors_of_one_page(self):
        """Locale host, locale query, fragment, trailing slash and case.

        The shapes are the ones the live store really holds: LinkedIn serves a
        post under a per-country host, X under ``?lang=``.
        """
        post = "https://www.linkedin.com/posts/someslug_a-headline-activity-1-abcd"
        for mirror in (
            "https://tn.linkedin.com/posts/someslug_a-headline-activity-1-abcd",
            "https://my.linkedin.com/posts/someslug_a-headline-activity-1-abcd/",
            "https://linkedin.com/posts/someslug_a-headline-activity-1-abcd",
            "https://www.linkedin.com/posts/someslug_a-headline-activity-1-abcd?trk=public_post",
            "https://rs.linkedin.com/posts/someslug_a-headline-activity-1-abcd?originalSubdomain=rs",
            "https://www.linkedin.com/posts/someslug_a-headline-activity-1-ABCD#comments",
            "  https://GR.LinkedIn.com/posts/someslug_a-headline-activity-1-abcd  ",
        ):
            with self.subTest(mirror=mirror):
                self.assertEqual(db.canonical_link_for(mirror), db.canonical_link_for(post))

        x_post = "https://x.com/someone/status/1537042233553846272"
        for mirror in (
            "https://x.com/SomeOne/status/1537042233553846272?lang=ar-x-fm",
            "https://x.com/someone/status/1537042233553846272?lang=bg",
        ):
            with self.subTest(mirror=mirror):
                self.assertEqual(db.canonical_link_for(mirror), db.canonical_link_for(x_post))

    def test_canonical_link_keeps_genuinely_distinct_pages_apart(self):
        """The query identifies the page on half the web — it is not dropped.

        Measured against the live store before the denylist was written:
        dropping the query wholesale merged 3,913 YouTube videos and 2,324
        Facebook photos into one row each, which would have hidden real
        findings behind the duplicate flag.
        """
        distinct = [
            ("https://www.youtube.com/watch?v=aaaaaaaaaaa",
             "https://www.youtube.com/watch?v=bbbbbbbbbbb"),
            ("https://www.facebook.com/photo.php?fbid=111&set=a.1",
             "https://www.facebook.com/photo.php?fbid=222&set=a.1"),
            ("https://stock.adobe.com/search?k=pencil",
             "https://stock.adobe.com/search?k=eraser"),
            # A different LinkedIn site, not a locale mirror of the same post.
            ("https://business.linkedin.com/talent-solutions",
             "https://www.linkedin.com/talent-solutions"),
            ("https://example.test/a", "https://example.test/b"),
        ]
        for left, right in distinct:
            with self.subTest(left=left):
                self.assertNotEqual(db.canonical_link_for(left), db.canonical_link_for(right))

        # Parameter order must not split a group, and the locale parameter
        # alongside a real one drops on its own.
        self.assertEqual(db.canonical_link_for("https://www.facebook.com/photo.php?fbid=1&set=a.2"),
                         db.canonical_link_for("https://www.facebook.com/photo.php?set=a.2&fbid=1"))
        self.assertEqual(
            db.canonical_link_for("https://www.facebook.com/photo.php?fbid=1&locale=es_ES"),
            db.canonical_link_for("https://www.facebook.com/photo.php?fbid=1"))

        for blank in (None, "", "   "):
            with self.subTest(blank=blank):
                self.assertIsNone(db.canonical_link_for(blank))

    def test_queue_serves_a_post_found_under_two_locale_hosts_once(self):
        """The symptom in issue #291: one post, five page loads, five verdicts."""
        slug = "someslug_a-headline-activity-1-abcd"
        _seed(self.conn, [
            {"found_link": f"https://www.linkedin.com/posts/{slug}", "local_image": "a.png"},
            {"found_link": f"https://tn.linkedin.com/posts/{slug}", "local_image": "a.png"},
            {"found_link": f"https://rs.linkedin.com/posts/{slug}?trk=public_post",
             "local_image": "a.png"},
            {"found_link": "https://www.linkedin.com/posts/other_x-activity-2-efgh",
             "local_image": "a.png"},
        ])
        db.mark_duplicates(self.conn)

        served = screen.next_batch(self.conn, limit=50, source="LinkedIn")
        keys = [db.canonical_link_for(r["found_link"]) for r in served]
        self.assertEqual(len(keys), len(set(keys)), "the queue served the same post twice")
        self.assertEqual(len(served), 2, "expected one row per post, plus the unrelated post")

    def test_mark_duplicates_does_not_rewrite_found_link(self):
        """The stored URL stays the one that was actually found and opened."""
        stored = "https://TN.linkedin.com/posts/someslug_a-activity-1-abcd/?trk=public_post"
        (row_id,) = _seed(self.conn, [{"found_link": stored}])
        db.mark_duplicates(self.conn)
        self.assertEqual(self.conn.execute(
            "select found_link from results where id = ?", (row_id,)).fetchone()["found_link"],
            stored)

    def test_mark_duplicates_elects_the_screened_mirror_as_the_primary(self):
        """A judged post must not come back through the queue as a mirror.

        The newer mirror is the one carrying the verdict, so date order alone
        would hand the primary slot to the unscreened row — the post returns to
        the queue and the recorded verdict sits on a row nobody serves.
        """
        slug = "someslug_a-headline-activity-1-abcd"
        older, screened = _seed(self.conn, [
            {"found_link": f"https://www.linkedin.com/posts/{slug}",
             "search_date": "2026-01-01 00:00:00"},
            {"found_link": f"https://tn.linkedin.com/posts/{slug}",
             "search_date": "2026-03-01 00:00:00"},
        ])
        screen.record_verdict(self.conn, screened, verdict="acceptable", reason="tagged me",
                              credit_ok=1, noncommercial_ok=1, unmodified_ok=1)
        db.mark_duplicates(self.conn)

        flags = {r["id"]: r["duplicate"] for r in self.conn.execute(
            "select id, duplicate from results")}
        self.assertEqual(flags[screened], 2, "the screened row lost the primary slot")
        self.assertEqual(flags[older], 1)
        self.assertEqual(screen.next_batch(self.conn, limit=50, source="LinkedIn"), [],
                         "an already-screened post was re-queued as its mirror")
        self.assertEqual(self.conn.execute(
            "select screen_verdict from results where id = ?", (screened,)
        ).fetchone()["screen_verdict"], "acceptable", "the verdict did not survive the pass")

    def test_mark_duplicates_keeps_a_live_row_canonical_over_a_retired_mirror(self):
        """A retired mirror must not take the primary slot and hide the post.

        The retired row is filtered out by ``retired = 0`` and its live twin by
        ``duplicate = 1``, so electing the retired one would drop the post out
        of the queue and the tab altogether (issue #292 meets issue #291).
        """
        slug = "someslug_a-headline-activity-1-abcd"
        retired, live = _seed(self.conn, [
            {"found_link": f"https://tn.linkedin.com/posts/{slug}",
             "match_type": "Similar Match", "search_date": "2026-01-01 00:00:00"},
            {"found_link": f"https://www.linkedin.com/posts/{slug}",
             "match_type": "Exact Match", "search_date": "2026-03-01 00:00:00"},
        ])
        _retire_similar(self.conn)
        db.mark_duplicates(self.conn)

        flags = {r["id"]: r["duplicate"] for r in self.conn.execute(
            "select id, duplicate from results")}
        self.assertEqual(flags[live], 2, "a retired mirror outranked the servable row")
        self.assertEqual(flags[retired], 1)
        self.assertEqual([r["id"] for r in screen.next_batch(self.conn, limit=50,
                                                             source="LinkedIn")], [live])

    def test_mark_duplicates_keeps_an_annotated_row_on_the_tab(self):
        """An owner decision must not be folded behind a mirror he never saw."""
        slug = "someslug_a-headline-activity-1-abcd"
        plain, annotated = _seed(self.conn, [
            {"found_link": f"https://www.linkedin.com/posts/{slug}",
             "search_date": "2026-01-01 00:00:00"},
            {"found_link": f"https://my.linkedin.com/posts/{slug}",
             "search_date": "2026-03-01 00:00:00", "ok": 0, "person": "contact-ref"},
        ])
        db.mark_duplicates(self.conn)

        flags = {r["id"]: r["duplicate"] for r in self.conn.execute(
            "select id, duplicate from results")}
        self.assertEqual(flags[annotated], 2, "the annotated row dropped off the tab")
        self.assertEqual(flags[plain], 1)
        frame = review.results_frame(self.conn, source="LinkedIn", status="everything")
        self.assertEqual(list(frame["id"]), [annotated])

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


if __name__ == "__main__":
    unittest.main()
