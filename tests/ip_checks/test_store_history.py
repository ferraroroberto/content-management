"""check_ip superseded screening opinions.

See ``_store_fixtures`` for the guarantees the store tests pin.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db, screen  # noqa: E402
from tests.ip_checks._store_fixtures import StoreTestCase, _seed  # noqa: E402


class HistoryTests(StoreTestCase):
    """A re-screen keeps the opinion it replaces, atomically and without owner columns."""

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


if __name__ == "__main__":
    unittest.main()
