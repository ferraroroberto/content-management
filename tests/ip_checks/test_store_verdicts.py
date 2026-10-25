"""check_ip screening verdicts and the three licence conditions.

See ``_store_fixtures`` for the guarantees the store tests pin.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db, review, screen  # noqa: E402
from tests.ip_checks._store_fixtures import StoreTestCase, _seed  # noqa: E402


class VerdictTests(StoreTestCase):
    """Verdict writes, BY / NC / ND conditions and the two kinds of 'unclear'."""

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
        SQL renderings of severity and the assessed predicate.
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
                       case when {db.assessed_sql('results')} then 1 else 0 end as assessed
                  from results where id = ?""", (row_id,)
        ).fetchone()
        self.assertEqual((row["sev"], row["assessed"]), (0, 0))

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

    def test_severity_and_sql_predicates_agree_with_python(self):
        """Four SQL predicates, each compared against all 64 combinations.

        There is a Python implementation for reporting and an SQL one for
        sorting and counting; only comparing them everywhere proves neither
        drifted. Written straight to the store rather than through
        record_verdict, so combinations record_verdict would reject are
        covered too.

        Four states since issue #305, so 64 rather than 27 — and the sweep now
        covers the two new predicates as well, because ``met_sql`` standing in
        for ``assessed_sql`` is exactly the substitution that would quietly
        turn a "could not establish" into a pass.
        """
        states = (1, 0, 2, None)
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
                                   case when ({db.met_sql('results')}) then 1 else 0 end as met,
                                   case when ({db.indeterminate_sql('results')})
                                        then 1 else 0 end as indet
                              from results where id = ?""", (row_id,)
                    ).fetchone()
                    conditions = (credit, noncommercial, unmodified)
                    with self.subTest(credit=credit, noncommercial=noncommercial,
                                      unmodified=unmodified):
                        self.assertEqual(row["sev"], db.severity(*conditions))
                        self.assertEqual(bool(row["ass"]), db.fully_assessed(*conditions))
                        self.assertEqual(bool(row["met"]), db.all_conditions_met(*conditions))
                        self.assertEqual(bool(row["indet"]), db.indeterminate(*conditions))
                        # The two states that are not answers to "did it pass"
                        # must never reach the compliant branch, in either
                        # rendering (issues #295, #305).
                        if None in conditions or 2 in conditions:
                            self.assertNotEqual(db.verdict_for(*conditions), "acceptable")
                            self.assertFalse(row["met"])
                        # Severity is the count of 0s and nothing else, so #305
                        # cannot have moved any existing row's severity.
                        self.assertEqual(row["sev"], sum(1 for c in conditions if c == 0))

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
                               ("met", 1), ("violated", 0), (2, 2), ("unknown", 2),
                               ("indeterminate", 2), ("unassessed", None),
                               ("not assessed", None), ("", None)):
            with self.subTest(value=good):
                self.assertEqual(db.condition_state(good), expected)
        for bad in ("maybe", "yep", 3, -1, "1.0"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    db.condition_state(bad)

    def test_unknown_and_never_assessed_are_different_stored_values(self):
        """Acceptance criterion 1 of issue #305, at the storage layer.

        "A worker looked and could not establish this" and "nobody has ever
        looked" were the same NULL, which is what made the re-screen queue
        unable to tell a re-judged row from an untouched one. They must be two
        values, and the word a worker says must reach the store unflattened.
        """
        self.assertEqual(db.condition_state("unknown"), db.CONDITION_INDETERMINATE)
        self.assertIsNone(db.condition_state(None))
        self.assertNotEqual(db.CONDITION_INDETERMINATE, db.CONDITION_MET,
                            "an indeterminate answer must never share a value with a pass")

        (row_id,) = _seed(self.conn, [{"found_link": "https://example.test/two-states"}])
        screen.record_verdict(self.conn, row_id, verdict="unclear",
                              outcome=db.OUTCOME_AMBIGUOUS,
                              credit_ok="met", noncommercial_ok="unknown",
                              reason="credited; the account gives no sign either way")
        row = self.conn.execute(
            "select screen_credit_ok, screen_noncommercial_ok, screen_unmodified_ok "
            "from results where id = ?", (row_id,)).fetchone()
        self.assertEqual(tuple(row), (1, db.CONDITION_INDETERMINATE, None),
                         "the answered condition and the unanswered one collapsed together")

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


if __name__ == "__main__":
    unittest.main()
