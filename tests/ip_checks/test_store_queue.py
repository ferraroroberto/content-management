"""check_ip screening queue: poster ranking, the re-screen backlog and unknown answers.

See ``_store_fixtures`` for the guarantees the store tests pin.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db, review, screen  # noqa: E402
from tests.ip_checks._store_fixtures import StoreTestCase, _seed  # noqa: E402


class QueueTests(StoreTestCase):
    """What ``screen next`` serves, and in which order."""

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
        screen.record_verdict(self.conn, ids[0], verdict="unclear",
                              outcome=db.OUTCOME_AMBIGUOUS)
        served = screen.next_batch(self.conn, limit=50, source="LinkedIn")
        self.assertEqual(served, [], "an already-screened row was served again")

        served = screen.next_batch(self.conn, limit=50, source="LinkedIn",
                                   include_screened=True)
        self.assertEqual([r["id"] for r in served], [ids[0]],
                         "expected only the pending canonical LinkedIn row")

    # ── the re-screen backlog (issue #300) ────────────────────────────────

    def _screen(self, row_id, *, credit=None, noncommercial=None,
                unmodified=None, outcome=None, reason=""):
        """Record one verdict, deriving the verdict the conditions imply.

        The three conditions are what these tests are about; re-deriving the
        verdict here keeps each case a statement about conditions rather than
        a hand-kept mapping that could drift from `db.verdict_for`.

        An unnamed condition defaults to ``None`` — never assessed — mirroring
        the CLI, where omitting a flag is the only way to say "nobody looked".
        It was ``"unknown"`` until issue #305 gave that word its own value; the
        cases below mean "this condition was never looked at", so they follow
        the CLI rather than the word.
        """
        verdict = db.verdict_for(credit, noncommercial, unmodified)
        if verdict == "unclear" and outcome is None:
            outcome = db.OUTCOME_AMBIGUOUS
        return screen.record_verdict(
            self.conn, row_id, verdict=verdict, reason=reason, outcome=outcome,
            credit_ok=credit, noncommercial_ok=noncommercial, unmodified_ok=unmodified)

    def test_unassessed_serves_exactly_the_not_fully_assessed_backlog(self):
        """The flag's count must equal what `stats` calls `not_fully_assessed`.

        That equality is the acceptance criterion of issue #300: the tab
        counts a backlog the CLI could not select, and `--include-screened`
        did not select it either — it only dropped the "never screened"
        predicate, leaving 257 rows competing with 7k unscreened ones under
        the same ranking.

        Each row below is one way of *not* belonging to the backlog, so a
        predicate that is too broad or too narrow fails on a specific row
        rather than on an aggregate.
        """
        pending, partial, full, permanent = _seed(self.conn, [
            {"found_link": "https://example.test/never-screened"},
            {"found_link": "https://example.test/credit-only"},
            {"found_link": "https://example.test/all-three"},
            {"found_link": "https://example.test/post-is-gone"},
        ])
        # Judged under the old credit-only question: two conditions never looked at.
        self._screen(partial, credit="met", reason="credit seen; the other two not checked")
        self._screen(full, credit="met", noncommercial="met", unmodified="met")
        self._screen(permanent, outcome=db.OUTCOME_NOTHING_TO_ASSESS,
                     reason="the post no longer exists")

        served = screen.next_batch(self.conn, limit=50, source="LinkedIn", unassessed=True)
        self.assertEqual([r["id"] for r in served], [partial],
                         "the flag must serve the partially-assessed row and only it")
        self.assertNotIn(pending, [r["id"] for r in served],
                         "a never-screened row is the *other* queue, not this one")
        self.assertNotIn(permanent, [r["id"] for r in served],
                         "a nothing_to_assess row can never become assessed — "
                         "serving it leaves a queue that never drains")

        self.assertEqual(len(served),
                         screen.stats(self.conn, source="LinkedIn")["not_fully_assessed"],
                         "the flag's count must match the number the tab reports")

    def test_unassessed_serves_the_prior_screening_opinion_and_no_owner_column(self):
        """A worker replacing an opinion may read it; a first screening may not."""
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/prior"}])
        self._screen(row_id, credit="violated", reason="no attribution anywhere on the page")

        (served,) = screen.next_batch(self.conn, limit=10, source="LinkedIn", unassessed=True)
        self.assertEqual(served["screen_verdict"], "infringement")
        self.assertEqual(served["screen_reason"], "no attribution anywhere on the page")
        for owner_column in ("ok", "person", "chat", "report", "fixed"):
            self.assertNotIn(owner_column, served,
                             "the queue must never hand a worker an owner decision")

        # The normal serve stays unprimed: no prior opinion, on any path.
        (fresh,) = screen.next_batch(self.conn, limit=10, source="LinkedIn",
                                     include_screened=True)
        self.assertNotIn("screen_verdict", fresh,
                         "a normal serve must not be primed by a past verdict")

    def test_unassessed_keeps_every_other_queue_predicate(self):
        """Source, exclusions, retired, duplicates and match type are unchanged.

        Criterion 3 of issue #300, proven rather than inspected: each seeded
        row is partially assessed, so the *only* thing that can keep it out of
        the serve is the predicate it is named for.
        """
        eligible, decided, secondary, other_source, retired, similar, own = _seed(self.conn, [
            {"found_link": "https://www.linkedin.com/posts/somebodyelse_a-activity-1-a"},
            {"found_link": "https://example.test/decided", "ok": 1},
            {"found_link": "https://example.test/secondary", "duplicate": 1},
            {"found_link": "https://example.test/elsewhere", "source": "Instagram"},
            {"found_link": "https://example.test/retired"},
            {"found_link": "https://example.test/lookalike", "match_type": "Similar Match"},
            {"found_link": "https://www.linkedin.com/posts/ferraroroberto_b-activity-2-a"},
        ])
        for row_id in (eligible, decided, secondary, other_source, retired, similar, own):
            self._screen(row_id, credit="met", reason="only the credit was ever checked")
        self.conn.execute("update results set retired = 1 where id = ?", (retired,))
        self.conn.commit()

        served = screen.next_batch(self.conn, limit=50, source="LinkedIn", unassessed=True,
                                   exclude_posters=["ferraroroberto"])
        self.assertEqual([r["id"] for r in served], [eligible],
                         "the re-screen serve let through a row a normal serve excludes")

    def test_unassessed_ranks_repeat_offenders_first_like_any_other_serve(self):
        """Same ordering as a normal serve: poster load, then reuse, then date."""
        self.conn.executemany(
            "insert into images (filename, linkedin_count) values (?, ?)",
            [("viral.png", 900), ("rare.png", 2)],
        )
        self.conn.commit()
        ids = _seed(self.conn, [
            {"found_link": "https://www.linkedin.com/posts/oneoff_x-activity-1-a",
             "local_image": "viral.png"},
            {"found_link": "https://www.linkedin.com/posts/serial_x-activity-2-a",
             "local_image": "rare.png"},
            {"found_link": "https://www.linkedin.com/posts/serial_y-activity-3-a",
             "local_image": "rare.png"},
            {"found_link": "https://www.linkedin.com/posts/serial_z-activity-4-a",
             "local_image": "rare.png"},
        ])
        for row_id in ids:
            self._screen(row_id, credit="met", reason="only the credit was ever checked")

        served = screen.next_batch(self.conn, limit=10, source="LinkedIn", unassessed=True)
        self.assertEqual(len(served), 4)
        self.assertEqual(served[0]["local_image"], "rare.png",
                         "poster load must outrank image reuse here too")
        self.assertEqual(served[0]["poster_pending"], 3)
        self.assertEqual(served[-1]["poster_pending"], 1)

    def test_draining_the_backlog_leaves_the_flag_serving_nothing(self):
        """Re-screening every row against all three conditions empties the queue."""
        ids = _seed(self.conn, [
            {"found_link": "https://example.test/one"},
            {"found_link": "https://example.test/two"},
        ])
        for row_id in ids:
            self._screen(row_id, credit="met", reason="only the credit was ever checked")
        self.assertEqual(
            len(screen.next_batch(self.conn, limit=50, source="LinkedIn", unassessed=True)), 2)

        for row_id in ids:
            self._screen(row_id, credit="met", noncommercial="violated", unmodified="met",
                         reason="credited, but the post sells a course")
        self.assertEqual(
            screen.next_batch(self.conn, limit=50, source="LinkedIn", unassessed=True), [],
            "a fully re-screened backlog must serve nothing")
        self.assertEqual(screen.stats(self.conn, source="LinkedIn")["not_fully_assessed"], 0)

    # ── an unknown answer is not "never assessed" (issue #305) ────────────

    def test_screening_a_row_with_an_unknown_condition_removes_it_from_the_queue(self):
        """The acceptance criterion issue #305 exists for.

        Two live batches were served the identical ten rows: the second worker
        re-opened every post, re-judged it, recorded its verdicts and left the
        queue exactly as it found it — roughly 37 minutes of browsing against
        the owner's real account, for rows that had just been done. It happened
        because an honest `unknown` on a condition stored the same NULL as a
        row nobody had ever looked at, and `--unassessed` selects on NULL.

        Deliberately written against the public API only — `record_verdict`
        then `next_batch` — so that against pre-#305 code it fails on the queue
        still holding the row, which is the actual defect, rather than on a
        helper that does not exist yet.
        """
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/rescreened"}])
        # Judged under the old credit-only question: in the backlog.
        self._screen(row_id, credit="met", reason="credit seen; the other two not checked")
        self.assertEqual(
            [r["id"] for r in screen.next_batch(self.conn, limit=50, source="LinkedIn",
                                                unassessed=True)],
            [row_id], "the partially-assessed row should start in the re-screen queue")

        # A worker re-screens it. It can establish credit and that the image is
        # untouched, but the post itself says nothing about commercial use —
        # the common case, and the honest answer is `unknown`.
        screen.record_verdict(
            self.conn, row_id, verdict="unclear", outcome=db.OUTCOME_AMBIGUOUS,
            credit_ok="met", noncommercial_ok="unknown", unmodified_ok="met",
            reason="credited in the caption, image untouched, nothing says what the "
                   "account is for")

        self.assertEqual(
            screen.next_batch(self.conn, limit=50, source="LinkedIn", unassessed=True), [],
            "a row screened with an `unknown` condition was served to the queue again — "
            "this is the bug that cost two identical live batches")
        self.assertEqual(screen.stats(self.conn, source="LinkedIn")["not_fully_assessed"], 0)

        # …and it did not buy that by turning an unestablished condition into a
        # pass: the row is still not compliant, and severity did not move.
        row = self.conn.execute(
            f"""select screen_verdict, screen_noncommercial_ok as nc,
                       ({db.severity_sql('results')}) as sev,
                       case when ({db.met_sql('results')}) then 1 else 0 end as met
                  from results where id = ?""", (row_id,)).fetchone()
        self.assertEqual(row["screen_verdict"], "unclear",
                         "nothing was violated, but nothing was established either")
        self.assertEqual(row["nc"], db.CONDITION_INDETERMINATE)
        self.assertEqual((row["sev"], row["met"]), (0, 0))

    def test_the_two_unresolved_states_are_counted_and_filtered_apart(self):
        """`stats`, the tab's overview and its filters keep them separate.

        They ask for different things — a never-assessed condition wants
        another screening pass, an unestablishable one wants the owner's own
        judgement — so one counter covering both is what let the backlog look
        permanent. They must also never double-count a row.
        """
        never, indeterminate, compliant = _seed(self.conn, [
            {"found_link": "https://example.test/nobody-looked"},
            {"found_link": "https://example.test/looked-cannot-tell"},
            {"found_link": "https://example.test/clean"},
        ])
        self._screen(never, credit="met", reason="only the credit was ever checked")
        self._screen(indeterminate, credit="met", noncommercial="unknown", unmodified="met",
                     reason="credited, image untouched, the account gives no sign either way")
        self._screen(compliant, credit="met", noncommercial="met", unmodified="met",
                     reason="tagged in a personal post, image untouched")

        stats = screen.stats(self.conn, source="LinkedIn")
        self.assertEqual((stats["not_fully_assessed"], stats["indeterminate"]), (1, 1),
                         "the two states must be reported separately, never as one pile")
        self.assertEqual(stats["proposed_acceptable"], 1,
                         "only the row with all three conditions met is acceptable")

        overview = review.overview(self.conn)
        self.assertEqual((overview["not_fully_assessed"], overview["indeterminate"]), (1, 1))

        rescreen = review.results_frame(self.conn, source="LinkedIn",
                                        status="not fully assessed — needs a re-screen")
        self.assertEqual(list(rescreen["id"]), [never],
                         "a row whose conditions were all answered sat in the re-screen pile")
        mine = review.results_frame(self.conn, source="LinkedIn",
                                    status="could not be established — my call")
        self.assertEqual(list(mine["id"]), [indeterminate])
        clean = review.results_frame(self.conn, source="LinkedIn",
                                     status="fully assessed and compliant")
        self.assertEqual(list(clean["id"]), [compliant],
                         "an unestablished condition was listed as compliant")

        # The grid renders the new state as words of its own — never blank,
        # never the same words as a pass or as nobody-looked.
        frame = review.results_frame(self.conn, source="LinkedIn", status="everything")
        labels = dict(zip(frame["id"], frame["screen_noncommercial_ok"]))
        self.assertEqual(labels[indeterminate], review.CONDITION_LABELS[2])
        self.assertEqual(labels[never], review.CONDITION_LABELS[None])
        self.assertEqual(labels[compliant], review.CONDITION_LABELS[1])
        self.assertEqual(len({labels[indeterminate], labels[never], labels[compliant]}), 3)

    def test_an_unestablished_condition_is_never_a_pass(self):
        """#295's guarantee, carried over to the state #305 split out of NULL.

        The risk the new value introduces is the one #295 was filed for: a `2`
        counts as assessed, so any predicate that reads "assessed" as "cleared"
        now certifies compliance nobody established. Asserted in Python, in
        SQL, in the queue stats and in the tab's compliant filter.
        """
        self.assertEqual(db.severity(1, 2, 1), 0, "an unestablished condition is not a violation")
        self.assertTrue(db.fully_assessed(1, 2, 1), "it was looked at")
        self.assertFalse(db.all_conditions_met(1, 2, 1), "…but it was not established")
        self.assertEqual(db.verdict_for(1, 2, 1), "unclear")
        self.assertEqual(db.verdict_for(2, 2, 2), "unclear")
        self.assertEqual(db.verdict_for(0, 2, 1), "infringement",
                         "a violation still outranks everything else")
        self.assertTrue(db.indeterminate(1, 2, 1))
        self.assertFalse(db.indeterminate(1, 2, None),
                         "a row still carrying a NULL belongs to the re-screen queue")
        self.assertFalse(db.indeterminate(1, 1, 1))

        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/not-a-pass"}])
        self._screen(row_id, credit="unknown", noncommercial="unknown", unmodified="unknown",
                     reason="the page would not load past the login wall")
        stats = screen.stats(self.conn, source="LinkedIn")
        self.assertEqual(stats["proposed_acceptable"], 0,
                         "a row nothing was established about was counted as compliant")
        self.assertEqual((stats["not_fully_assessed"], stats["indeterminate"]), (0, 1))
        compliant = review.results_frame(self.conn, source="LinkedIn",
                                         status="fully assessed and compliant")
        self.assertTrue(compliant.empty,
                        "a row where every condition came back unestablished was "
                        "listed as compliant")

    def test_an_omitted_condition_is_still_stored_as_never_assessed(self):
        """Omitting a flag must not inherit `unknown`'s new meaning.

        The CLI used to default each condition to the word `unknown`. Now that
        the word records "someone looked", that default would claim a look
        nobody took and drain the row out of the re-screen queue on the
        strength of it — so the default had to become the absence of an answer.
        """
        parser = screen.build_parser({"source": "LinkedIn", "default_limit": 10,
                                      "exclude_posters": []})
        args = parser.parse_args(["verdict", "--id", "1", "--verdict", "unclear",
                                  "--outcome", "ambiguous"])
        self.assertEqual((args.credit, args.noncommercial, args.unmodified), (None, None, None),
                         "an omitted condition must default to never-assessed, not to the "
                         "word `unknown` — which now records that somebody looked")
        self.assertEqual(db.condition_state(args.noncommercial), None)

        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/omitted"}])
        screen.record_verdict(self.conn, row_id, verdict="unclear",
                              outcome=db.OUTCOME_AMBIGUOUS, credit_ok="met",
                              reason="credit seen; the other two never looked at")
        row = self.conn.execute(
            "select screen_noncommercial_ok, screen_unmodified_ok from results where id = ?",
            (row_id,)).fetchone()
        self.assertEqual(tuple(row), (None, None),
                         "an omitted condition must stay NULL — nobody looked at it")
        self.assertEqual(
            [r["id"] for r in screen.next_batch(self.conn, limit=50, source="LinkedIn",
                                                unassessed=True)],
            [row_id], "a row with conditions nobody looked at must stay in the queue")

    def test_include_screened_keeps_its_previous_meaning(self):
        """The new flag is separate; the old one still serves pending + screened."""
        pending, partial = _seed(self.conn, [
            {"found_link": "https://example.test/pending"},
            {"found_link": "https://example.test/partial"},
        ])
        self._screen(partial, credit="met", reason="only the credit was ever checked")

        self.assertEqual(
            sorted(r["id"] for r in screen.next_batch(self.conn, limit=50, source="LinkedIn",
                                                      include_screened=True)),
            sorted([pending, partial]),
            "--include-screened must still serve the screened row alongside the pending one")
        self.assertEqual(
            [r["id"] for r in screen.next_batch(self.conn, limit=50, source="LinkedIn")],
            [pending], "the default serve must still be unscreened rows only")
        # Passed together, the narrower flag wins rather than contradicting.
        self.assertEqual(
            [r["id"] for r in screen.next_batch(self.conn, limit=50, source="LinkedIn",
                                                include_screened=True, unassessed=True)],
            [partial])

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


if __name__ == "__main__":
    unittest.main()
