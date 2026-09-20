"""Regression tests for the check_ip store, queue and review layer (issue #286).

The load-bearing guarantee here is the one in the middle: the screening pass
proposes, it never decides. ``check_ip.screen.record_verdict`` must not be able
to touch ``ok`` / ``person`` / ``chat`` / ``report`` / ``fixed`` — those are ten
months of manual triage and the skill runs unattended against them.

The second one, added with issue #295, is that a licence condition nobody
assessed stays ``NULL`` and is never counted, sorted or rendered as if it had
passed. One ``coalesce(col, 1)`` would undo the whole model silently, so the
tests assert it in Python, in SQL, in the queue stats and in the tab's frame.

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

        # Worst-case write: all three conditions violated, which touches every
        # screening column there is.
        screen.record_verdict(self.conn, row_id, verdict="infringement",
                              reason="no mention anywhere", poster_url="https://example.test/p",
                              credit_ok=0, noncommercial_ok=0, unmodified_ok=0)

        after = dict(self.conn.execute(
            "select ok, person, chat, report, fixed from results where id = ?", (row_id,)
        ).fetchone())
        self.assertEqual(before, after, "record_verdict altered an owner column")

        written = dict(self.conn.execute(
            "select screen_verdict, screen_reason, poster_url, screened_at, screen_source, "
            "screen_credit_ok, screen_noncommercial_ok, screen_unmodified_ok "
            "from results where id = ?", (row_id,)
        ).fetchone())
        self.assertEqual(written["screen_verdict"], "infringement")
        self.assertEqual(written["screen_reason"], "no mention anywhere")
        self.assertEqual(written["poster_url"], "https://example.test/p")
        self.assertIsNotNone(written["screened_at"])
        self.assertEqual((written["screen_credit_ok"], written["screen_noncommercial_ok"],
                          written["screen_unmodified_ok"]), (0, 0, 0))

    def test_record_verdict_rejects_an_unknown_verdict(self):
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/b"}])
        with self.assertRaises(ValueError):
            screen.record_verdict(self.conn, row_id, verdict="probably-bad")
        self.assertIsNone(self.conn.execute(
            "select screen_verdict from results where id = ?", (row_id,)
        ).fetchone()["screen_verdict"])

    # ── the three licence conditions (issue #295) ─────────────────────────

    def test_severity_counts_violated_conditions(self):
        """Severity is how many of BY / NC / ND were found violated, 0–3."""
        self.assertEqual(db.severity(1, 1, 1), 0)
        self.assertEqual(db.severity(0, 1, 1), 1)
        self.assertEqual(db.severity(1, 0, 1), 1, "commercial use alone is a violation")
        self.assertEqual(db.severity(1, 1, 0), 1, "a derivative alone is a violation")
        self.assertEqual(db.severity(0, 0, 1), 2)
        self.assertEqual(db.severity(0, 0, 0), 3, "severity 3 must be reachable")

    def test_an_unassessed_condition_is_never_a_pass(self):
        """The core of issue #295: NULL is its own state, not a satisfied one.

        A single `coalesce(col, 1)` anywhere would make an unchecked condition
        indistinguishable from a met one, which is exactly how a credited but
        plainly commercial post came to be stored as compliant. Asserted on
        severity, on the assessed predicate, on the derived verdict and on the
        SQL renderings of all three.
        """
        # Nothing violated, but nothing established either.
        self.assertEqual(db.severity(None, None, None), 0)
        self.assertFalse(db.fully_assessed(None, None, None))
        self.assertEqual(db.verdict_for(None, None, None), "unclear")

        # The shape the migration leaves behind: credit met, the rest unknown.
        self.assertEqual(db.severity(1, None, None), 0)
        self.assertFalse(db.fully_assessed(1, None, None),
                         "a row with two unknowns must not read as assessed")
        self.assertEqual(db.verdict_for(1, None, None), "unclear",
                         "credit alone must not derive an 'acceptable' verdict")

        # Only all three met is acceptable.
        self.assertTrue(db.fully_assessed(1, 1, 1))
        self.assertEqual(db.verdict_for(1, 1, 1), "acceptable")

        # …and the same in SQL, where a coalesce would be easiest to slip in.
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/unassessed"}])
        screen.record_verdict(self.conn, row_id, verdict="unclear",
                              outcome=db.OUTCOME_AMBIGUOUS, credit_ok=1)
        row = self.conn.execute(
            f"""select ({db.severity_sql('results')}) as sev,
                       case when {db.assessed_sql('results')} then 1 else 0 end as assessed,
                       ({db.verdict_sql('results')}) as derived
                  from results where id = ?""", (row_id,)
        ).fetchone()
        self.assertEqual((row["sev"], row["assessed"], row["derived"]), (0, 0, "unclear"))

        # The tab counts it as not-fully-assessed rather than as compliant.
        stats = screen.stats(self.conn, source="LinkedIn")
        self.assertEqual(stats["not_fully_assessed"], 1)
        self.assertEqual(stats["proposed_acceptable"], 0,
                         "an unassessed row was counted as acceptable")
        self.assertEqual(review.overview(self.conn)["not_fully_assessed"], 1)
        frame = review.results_frame(self.conn, source="LinkedIn",
                                     status="not fully assessed — needs a re-screen")
        self.assertEqual(list(frame["id"]), [row_id])
        self.assertEqual(list(frame["screen_noncommercial_ok"]),
                         [review.CONDITION_LABELS[None]],
                         "an unassessed condition must render as words, not a blank cell")
        self.assertEqual(list(frame["assessed"]), [False])

    def test_severity_and_verdict_sql_agree_with_python(self):
        """Two renderings of one rule, compared across all 27 combinations.

        There is a Python implementation for reporting and an SQL one for
        sorting and counting; only comparing them everywhere proves neither
        drifted. Written straight to the store rather than through
        record_verdict, so combinations record_verdict would reject are
        covered too.
        """
        states = (1, 0, None)
        for credit in states:
            for noncommercial in states:
                for unmodified in states:
                    (row_id,) = _seed(self.conn, [
                        {"found_link": f"https://e.test/{credit}-{noncommercial}-{unmodified}"}])
                    self.conn.execute(
                        "update results set screen_credit_ok = ?, screen_noncommercial_ok = ?, "
                        "screen_unmodified_ok = ? where id = ?",
                        (credit, noncommercial, unmodified, row_id))
                    self.conn.commit()
                    row = self.conn.execute(
                        f"""select ({db.severity_sql('results')}) as sev,
                                   case when {db.assessed_sql('results')} then 1 else 0 end as ass,
                                   ({db.verdict_sql('results')}) as verdict
                              from results where id = ?""", (row_id,)
                    ).fetchone()
                    with self.subTest(credit=credit, noncommercial=noncommercial,
                                      unmodified=unmodified):
                        self.assertEqual(row["sev"], db.severity(credit, noncommercial, unmodified))
                        self.assertEqual(bool(row["ass"]),
                                         db.fully_assessed(credit, noncommercial, unmodified))
                        self.assertEqual(row["verdict"],
                                         db.verdict_for(credit, noncommercial, unmodified))

    def test_a_credited_post_can_still_be_an_infringement(self):
        """The two cases issue #295 names, which the old model could not hold.

        Under `credit gate + aggravators` both of these scored `acceptable`,
        severity 0, and never reached the review tab.
        """
        commercial, cropped = _seed(self.conn, [
            {"found_link": "https://example.test/commercial"},
            {"found_link": "https://example.test/cropped"},
        ])
        out = screen.record_verdict(
            self.conn, commercial, verdict="infringement",
            reason="credited in the caption, but the post sells the poster's workshop",
            credit_ok=1, noncommercial_ok=0, unmodified_ok=1)
        self.assertEqual((out["verdict"], out["severity"]), ("infringement", 1))

        out = screen.record_verdict(
            self.conn, cropped, verdict="infringement",
            reason="credited, but the image is cropped and carries added text",
            credit_ok=1, noncommercial_ok=1, unmodified_ok=0)
        self.assertEqual((out["verdict"], out["severity"]), ("infringement", 1))

        stored = {r["id"]: dict(r) for r in self.conn.execute(
            f"select id, screen_verdict, ({db.severity_sql('results')}) as sev, "
            f"screen_credit_ok from results")}
        for row_id in (commercial, cropped):
            with self.subTest(row=row_id):
                self.assertEqual(stored[row_id]["screen_verdict"], "infringement")
                self.assertEqual(stored[row_id]["sev"], 1)
                self.assertEqual(stored[row_id]["screen_credit_ok"], 1,
                                 "the met credit condition was lost")

        self.assertEqual(screen.stats(self.conn, source="LinkedIn")["proposed_infringement"], 2)

    def test_record_verdict_keeps_conditions_on_a_non_infringement_verdict(self):
        """Nothing is cleared any more — that clearing was the bug (#295).

        The old code blanked both aggravators whenever the verdict was not
        `infringement`, so a met condition and a violated one both became 0.
        """
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/keep"}])
        screen.record_verdict(self.conn, row_id, verdict="acceptable",
                              reason="tagged in the caption, personal post, image untouched",
                              credit_ok=1, noncommercial_ok=1, unmodified_ok=1)
        row = self.conn.execute(
            "select screen_credit_ok, screen_noncommercial_ok, screen_unmodified_ok "
            "from results where id = ?", (row_id,)).fetchone()
        self.assertEqual(tuple(row), (1, 1, 1), "record_verdict cleared the conditions")

        # …and an unclear row keeps the one condition that *was* established.
        screen.record_verdict(self.conn, row_id, verdict="unclear",
                              outcome=db.OUTCOME_AMBIGUOUS, credit_ok=1)
        row = self.conn.execute(
            "select screen_credit_ok, screen_noncommercial_ok, screen_unmodified_ok "
            "from results where id = ?", (row_id,)).fetchone()
        self.assertEqual(tuple(row), (1, None, None))

    def test_record_verdict_rejects_an_inconsistent_combination(self):
        """A verdict that contradicts the conditions is refused, not normalised."""
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/inconsistent"}])
        cases = [
            # acceptable while a condition is violated
            dict(verdict="acceptable", credit_ok=1, noncommercial_ok=0, unmodified_ok=1),
            # acceptable while a condition was never assessed
            dict(verdict="acceptable", credit_ok=1),
            # infringement with nothing actually violated
            dict(verdict="infringement", credit_ok=1, noncommercial_ok=1, unmodified_ok=1),
            # infringement asserted without naming a single condition
            dict(verdict="infringement"),
            # unclear while a condition is violated — that is an infringement
            dict(verdict="unclear", outcome=db.OUTCOME_AMBIGUOUS, credit_ok=0),
        ]
        for case in cases:
            with self.subTest(**case):
                with self.assertRaises(ValueError):
                    screen.record_verdict(self.conn, row_id, **case)
        self.assertIsNone(self.conn.execute(
            "select screened_at from results where id = ?", (row_id,)
        ).fetchone()["screened_at"], "a rejected verdict was still written")

    def test_condition_state_rejects_a_value_it_cannot_read(self):
        """A typo must raise, never fall through as 'not assessed'."""
        for good, expected in ((None, None), (True, 1), (False, 0), (1, 1), (0, 0),
                               ("met", 1), ("violated", 0), ("unknown", None)):
            with self.subTest(value=good):
                self.assertEqual(db.condition_state(good), expected)
        for bad in ("maybe", "yep", 2, -1, "1.0"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    db.condition_state(bad)

    # ── the two kinds of 'unclear' (issue #295 §4) ────────────────────────

    def test_unclear_needs_an_outcome_and_the_tab_can_exclude_the_permanent_one(self):
        """'Could not tell' and 'nothing to assess' are unrelated states."""
        ambiguous, permanent = _seed(self.conn, [
            {"found_link": "https://example.test/ambiguous"},
            {"found_link": "https://example.test/gone"},
        ])
        with self.assertRaises(ValueError):
            screen.record_verdict(self.conn, ambiguous, verdict="unclear")
        with self.assertRaises(ValueError):
            screen.record_verdict(self.conn, ambiguous, verdict="acceptable",
                                  credit_ok=1, noncommercial_ok=1, unmodified_ok=1,
                                  outcome=db.OUTCOME_AMBIGUOUS)

        screen.record_verdict(self.conn, ambiguous, verdict="unclear",
                              outcome=db.OUTCOME_AMBIGUOUS,
                              reason="caption truncated behind a login wall")
        screen.record_verdict(self.conn, permanent, verdict="unclear",
                              outcome=db.OUTCOME_NOTHING_TO_ASSESS,
                              reason="the post no longer exists")

        rescreen = review.results_frame(self.conn, source="LinkedIn",
                                        status="not fully assessed — needs a re-screen")
        self.assertEqual(list(rescreen["id"]), [ambiguous],
                         "a permanently unassessable row sat in the re-screen pile")
        worth_a_look = review.results_frame(self.conn, source="LinkedIn",
                                            status="unclear — another look may settle it")
        self.assertEqual(list(worth_a_look["id"]), [ambiguous])
        dead = review.results_frame(self.conn, source="LinkedIn",
                                    status="unclear — nothing left to assess")
        self.assertEqual(list(dead["id"]), [permanent])

        stats = screen.stats(self.conn, source="LinkedIn")
        self.assertEqual((stats["not_fully_assessed"], stats["nothing_to_assess"]), (1, 1))
        overview = review.overview(self.conn)
        self.assertEqual((overview["not_fully_assessed"], overview["nothing_to_assess"]), (1, 1))

    # ── the migration lane (issue #295 §5) ────────────────────────────────

    def test_backfill_maps_the_credit_only_screening_without_inventing_facts(self):
        """`migrate --assess-conditions`, on rows shaped like the live store's.

        The mapping is deliberately lossy in one direction only: a flag that
        fired becomes a violation, a flag that did not fire becomes NULL,
        because "not flagged promotional" never meant "checked and clean".
        """
        plain, promo, altered, acceptable, unclear, unscreened = _seed(self.conn, [
            {"found_link": "https://example.test/plain"},
            {"found_link": "https://example.test/promo"},
            {"found_link": "https://example.test/altered", "ok": 0, "person": "contact-ref"},
            {"found_link": "https://example.test/acceptable"},
            {"found_link": "https://example.test/unclear"},
            {"found_link": "https://example.test/untouched"},
        ])
        # The pre-#295 shape, written directly: record_verdict no longer
        # produces it, and the point is to migrate stores that already hold it.
        for row_id, verdict, promotional, edited in (
            (plain, "infringement", 0, 0),
            (promo, "infringement", 1, 0),
            (altered, "infringement", 0, 1),
            (acceptable, "acceptable", 0, 0),
            (unclear, "unclear", 0, 0),
        ):
            self.conn.execute(
                "update results set screen_verdict = ?, screen_promotional = ?, "
                "screen_altered = ?, screened_at = '2026-01-02 03:04:05', "
                "screen_source = 'check-ip skill' where id = ?",
                (verdict, promotional, edited, row_id))
        self.conn.commit()

        before = db.counts(self.conn)
        result = db.backfill_licence_conditions(self.conn)
        after = db.counts(self.conn)

        self.assertEqual(result["mapped"], 5)
        self.assertEqual((after["annotated"], after["results"], after["screened"]),
                         (before["annotated"], before["results"], before["screened"]),
                         "the migration changed the shape of the store")

        mapped = {r["id"]: dict(r) for r in self.conn.execute(
            "select id, screen_verdict, screen_outcome, screen_credit_ok, "
            "screen_noncommercial_ok, screen_unmodified_ok from results")}

        # An infringement was a failed credit check, and nothing else was asked.
        self.assertEqual((mapped[plain]["screen_credit_ok"],
                          mapped[plain]["screen_noncommercial_ok"],
                          mapped[plain]["screen_unmodified_ok"]), (0, None, None))
        self.assertEqual(mapped[plain]["screen_verdict"], "infringement")

        # A flag that fired is the one thing that *was* established.
        self.assertEqual(mapped[promo]["screen_noncommercial_ok"], 0)
        self.assertIsNone(mapped[promo]["screen_unmodified_ok"])
        self.assertEqual(mapped[altered]["screen_unmodified_ok"], 0)
        self.assertIsNone(mapped[altered]["screen_noncommercial_ok"])

        # The deliberate, visible consequence: an `acceptable` row becomes
        # "credit met, the other two unknown", not "compliant".
        self.assertEqual(mapped[acceptable]["screen_credit_ok"], 1)
        self.assertIsNone(mapped[acceptable]["screen_noncommercial_ok"])
        self.assertIsNone(mapped[acceptable]["screen_unmodified_ok"])
        self.assertEqual(mapped[acceptable]["screen_verdict"], "unclear",
                         "a credit-only pass still reads as acceptable")

        # An unclear row established nothing at all. It is marked `ambiguous`
        # rather than permanent: the legacy bucket mixed both kinds and the
        # store cannot say which, so the row stays in the re-screen pile.
        self.assertEqual((mapped[unclear]["screen_credit_ok"],
                          mapped[unclear]["screen_noncommercial_ok"],
                          mapped[unclear]["screen_unmodified_ok"]), (None, None, None))
        for row_id in (acceptable, unclear):
            with self.subTest(row=row_id):
                self.assertEqual(mapped[row_id]["screen_outcome"], db.OUTCOME_AMBIGUOUS)
        self.assertIsNone(mapped[plain]["screen_outcome"],
                          "an infringement is not a kind of unclear")
        self.assertEqual(
            screen.stats(self.conn, source="LinkedIn")["nothing_to_assess"], 0,
            "the mapping invented a permanently-unassessable row")

        # A never-screened row is not touched.
        self.assertIsNone(mapped[unscreened]["screen_verdict"])
        self.assertEqual(result["not_fully_assessed"], 5)

        # The owner's annotation on the altered row survived untouched.
        self.assertEqual(dict(self.conn.execute(
            "select ok, person from results where id = ?", (altered,)).fetchone()),
            {"ok": 0, "person": "contact-ref"})

        # Idempotent: a second pass finds nothing left to map and changes nothing.
        again = db.backfill_licence_conditions(self.conn)
        self.assertEqual((again["mapped"], again["already_mapped"]), (0, 5))
        self.assertEqual(
            {r["id"]: dict(r) for r in self.conn.execute(
                "select id, screen_verdict, screen_outcome, screen_credit_ok, "
                "screen_noncommercial_ok, screen_unmodified_ok from results")},
            mapped, "a second pass rewrote rows it had already mapped")

    def test_backfill_leaves_rows_screened_under_the_new_criteria_alone(self):
        """A fully-assessed row must not be re-derived from the frozen flags."""
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/new"}])
        screen.record_verdict(self.conn, row_id, verdict="acceptable",
                              credit_ok=1, noncommercial_ok=1, unmodified_ok=1)
        result = db.backfill_licence_conditions(self.conn)
        self.assertEqual((result["mapped"], result["already_mapped"]), (0, 1))
        row = self.conn.execute(
            "select screen_verdict, screen_credit_ok, screen_noncommercial_ok, "
            "screen_unmodified_ok from results where id = ?", (row_id,)).fetchone()
        self.assertEqual(tuple(row), ("acceptable", 1, 1, 1))

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
        db.retire_similar_matches(self.conn)
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

    # ── superseded screening opinions (issue #301) ────────────────────────
    #
    # Every reason below is invented. The real ones name real accounts and real
    # posts, this repo is public, and a fixture is the easiest place for one to
    # leak into git.

    def test_re_screening_preserves_the_opinion_it_replaces(self):
        """The whole of issue #301: the old answer stays retrievable.

        Verdict, outcome, reason, the three conditions, the timestamp and the
        source all have to survive — the reason in particular is the evidence
        behind a potential accusation, not a disposable intermediate.
        """
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/a"}])
        screen.record_verdict(
            self.conn, row_id, verdict="unclear", outcome=db.OUTCOME_AMBIGUOUS,
            reason="first pass: could not tell from the visible caption",
            poster_url="https://example.test/p1", screen_source="credit-only pass",
            credit_ok=1)
        first = dict(self.conn.execute(
            "select screen_verdict, screen_outcome, screen_reason, screened_at, "
            "screen_source, poster_url, screen_credit_ok, screen_noncommercial_ok, "
            "screen_unmodified_ok from results where id = ?", (row_id,)).fetchone())

        out = screen.record_verdict(
            self.conn, row_id, verdict="infringement",
            reason="second pass: all three conditions checked against the page",
            screen_source="three-condition re-screen",
            credit_ok=0, noncommercial_ok=0, unmodified_ok=1)

        history = self.conn.execute(
            "select * from screen_history where result_id = ?", (row_id,)).fetchall()
        self.assertEqual(len(history), 1, "exactly one superseded opinion expected")
        kept = dict(history[0])
        for column, value in first.items():
            self.assertEqual(kept[column], value,
                             f"screen_history lost {column} from the superseded opinion")
        self.assertIsNotNone(kept["recorded_at"])
        self.assertTrue(out["superseded"],
                        "a re-screen must report that it superseded an opinion")

        # And the current opinion is the new one, on results, exactly once.
        current = self.conn.execute(
            "select screen_verdict, screen_reason from results where id = ?",
            (row_id,)).fetchone()
        self.assertEqual(current["screen_verdict"], "infringement")
        self.assertEqual(current["screen_reason"],
                         "second pass: all three conditions checked against the page")

    def test_a_first_screening_supersedes_nothing(self):
        """No history row for a row that had no previous opinion to replace."""
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/b"}])
        out = screen.record_verdict(self.conn, row_id, verdict="infringement",
                                    reason="invented reason", credit_ok=0,
                                    noncommercial_ok=1, unmodified_ok=1)
        self.assertFalse(out["superseded"])
        self.assertEqual(self.conn.execute(
            "select count(*) n from screen_history").fetchone()["n"], 0,
            "an empty opinion was preserved as if it were an observation")

    def test_the_history_write_is_atomic_with_the_verdict(self):
        """A failed verdict write must leave neither the update nor the copy.

        The trigger makes the UPDATE abort for a real SQLite reason, after the
        history insert has already run in the same transaction — which is the
        only ordering where a non-atomic implementation would leak a history
        row for a verdict that was never recorded.
        """
        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/c"}])
        screen.record_verdict(self.conn, row_id, verdict="infringement",
                              reason="the opinion that must not be duplicated",
                              credit_ok=0, noncommercial_ok=1, unmodified_ok=1)
        before = dict(self.conn.execute(
            "select screen_verdict, screen_reason from results where id = ?",
            (row_id,)).fetchone())

        self.conn.execute(
            "create trigger fail_update before update on results "
            "begin select raise(abort, 'no writes today'); end")
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                screen.record_verdict(self.conn, row_id, verdict="acceptable",
                                      reason="a verdict that never lands",
                                      credit_ok=1, noncommercial_ok=1, unmodified_ok=1)
        finally:
            self.conn.execute("drop trigger fail_update")

        self.assertEqual(self.conn.execute(
            "select count(*) n from screen_history").fetchone()["n"], 0,
            "a history row survived a verdict write that was rolled back")
        after = dict(self.conn.execute(
            "select screen_verdict, screen_reason from results where id = ?",
            (row_id,)).fetchone())
        self.assertEqual(before, after, "the failed write changed results anyway")

    def test_screen_history_holds_no_owner_column(self):
        """The owner's five decisions are not the skill's to keep history of.

        Asserted against the live table definition rather than the source, so
        adding one of them to schema.sql fails here rather than shipping.
        """
        columns = {r["name"] for r in self.conn.execute("pragma table_info(screen_history)")}
        self.assertTrue(columns, "screen_history is missing from the schema")
        self.assertEqual(columns & set(db.OWNER_COLUMNS), set(),
                         "screen_history carries an owner decision column")
        # And it does preserve every screening column, or a re-screen would
        # silently drop whichever one was left out.
        self.assertEqual(set(db.SCREEN_HISTORY_COLUMNS) - columns, set())

    def test_backfilling_history_writes_no_row_twice_and_leaves_results_alone(self):
        """The back-fill lane: idempotent, and `results` is not touched at all.

        Also covers the convergence that matters after it — a later re-screen
        of a back-filled row records no second copy of the opinion already in
        the table, because it is the same (row, screening timestamp).
        """
        ids = _seed(self.conn, [
            {"found_link": "https://example.test/d"},
            {"found_link": "https://example.test/e"},
        ])
        export = [
            {"id": ids[0], "screen_verdict": "unclear", "screen_outcome": "ambiguous",
             "screen_reason": "invented observation one", "screened_at": "2026-01-01 00:00:00",
             "screen_source": "credit-only pass", "screen_credit_ok": 1},
            {"id": ids[1], "screen_verdict": "infringement", "screen_outcome": None,
             "screen_reason": "invented observation two", "screened_at": "2026-01-02 00:00:00",
             "screen_source": "credit-only pass", "screen_credit_ok": 0},
            {"id": max(ids) + 9999, "screen_verdict": "unclear",
             "screen_reason": "row that is not in the store",
             "screened_at": "2026-01-03 00:00:00"},
        ]
        fingerprint = self.conn.execute(
            "select count(*) n, coalesce(sum(coalesce(ok, 0)), 0) s from results").fetchone()

        first = db.backfill_screen_history(self.conn, export)
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(first["unknown_ids"], [max(ids) + 9999],
                         "an id absent from the store must be reported, not invented")

        second = db.backfill_screen_history(self.conn, export)
        self.assertEqual(second["inserted"], 0, "the back-fill is not idempotent")
        self.assertEqual(second["already_present"], 2)
        self.assertEqual(self.conn.execute(
            "select count(*) n from screen_history").fetchone()["n"], 2)

        after = self.conn.execute(
            "select count(*) n, coalesce(sum(coalesce(ok, 0)), 0) s from results").fetchone()
        self.assertEqual(tuple(fingerprint), tuple(after),
                         "the back-fill wrote to results")

        # Now re-screen a back-filled row whose current state is the one the
        # export recorded: the opinion is already preserved, so nothing is added.
        self.conn.execute(
            "update results set screen_verdict = 'unclear', screen_outcome = 'ambiguous', "
            "screen_reason = 'invented observation one', screened_at = '2026-01-01 00:00:00', "
            "screen_source = 'credit-only pass', screen_credit_ok = 1 where id = ?", (ids[0],))
        self.conn.commit()
        out = screen.record_verdict(self.conn, ids[0], verdict="infringement",
                                    reason="invented re-screen observation",
                                    screen_source="three-condition re-screen",
                                    credit_ok=0, noncommercial_ok=1, unmodified_ok=1)
        self.assertEqual(self.conn.execute(
            "select count(*) n from screen_history where result_id = ?",
            (ids[0],)).fetchone()["n"], 1,
            "the re-screen duplicated an opinion the back-fill had already kept")
        # Still a supersession, even though this call wrote no history row: the
        # question is whether the replaced opinion is preserved, not whether
        # this particular write is what preserved it.
        self.assertTrue(out["superseded"],
                        "a re-screen of a back-filled row reported nothing superseded")


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
