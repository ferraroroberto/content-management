"""The stored triage run status: the declared states, the table that reads them and the refused moves.

``tools/newsletter/triage/run_status.py`` is the only module that names ``running`` / ``done`` /
``failed``; ``db.mark_run`` settles a run through it. No network: the store is the in-memory fake.
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.triage.test_triage_db import FakeSupabase  # noqa: E402
from tools.newsletter.triage import db  # noqa: E402
from tools.newsletter.triage.run_status import (  # noqa: E402
    IllegalRunTransition,
    RunStatus,
    derive_run_status,
    settled_status,
)

DERIVE_TABLE = (
    ("running", RunStatus.RUNNING),
    ("done", RunStatus.DONE),
    ("failed", RunStatus.FAILED),
    (" Done ", RunStatus.DONE),
    (RunStatus.FAILED, RunStatus.FAILED),
    ("errored", RunStatus.UNKNOWN),
    ("", RunStatus.UNKNOWN),
    (None, RunStatus.UNKNOWN),
    (7, RunStatus.UNKNOWN),
)


class DeriveTableTests(unittest.TestCase):
    def test_every_stored_value(self) -> None:
        for stored, expected in DERIVE_TABLE:
            with self.subTest(stored=stored):
                self.assertIs(derive_run_status(stored), expected)

    def test_stored_values_are_the_schema_values(self) -> None:
        self.assertEqual({s.value for s in RunStatus if s is not RunStatus.UNKNOWN}, {"running", "done", "failed"})


class MarkRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeSupabase()
        db.set_client(self.fake)
        self.run_id = db.register_run(date(2026, 8, 8), date(2026, 8, 15), source="cache", model="m", criteria_version="1")

    def tearDown(self) -> None:
        db.set_client(None)

    def test_a_new_run_is_running(self) -> None:
        self.assertIs(derive_run_status(db.get_run(self.run_id)["status"]), RunStatus.RUNNING)

    def test_a_running_run_settles_as_done_or_failed(self) -> None:
        for target in (RunStatus.DONE, RunStatus.FAILED):
            with self.subTest(target=str(target)):
                run_id = db.register_run(date(2026, 8, 8), date(2026, 8, 15), source="cache", model="m",
                                         criteria_version="1")
                db.mark_run(run_id, status=target, stats={"k": 1})
                self.assertEqual(db.get_run(run_id)["status"], target.value)

    # state-machine-proof: triage-run-status
    def test_a_run_cannot_settle_as_anything_but_done_or_failed(self) -> None:
        """The illegal transition: settling a run as running, unknown or a value the schema never held."""
        for target in ("running", "unknown", "errored", "", None):
            with self.subTest(target=target):
                with self.assertRaises(IllegalRunTransition):
                    settled_status(target)
                with self.assertRaises(IllegalRunTransition):
                    db.mark_run(self.run_id, status=target)  # type: ignore[arg-type]
                self.assertEqual(db.get_run(self.run_id)["status"], "running")

    # state-machine-proof: triage-run-status
    def test_a_settled_run_never_moves_again(self) -> None:
        """The illegal transition: done to failed (or back) overwriting the stored outcome."""
        db.mark_run(self.run_id, status=RunStatus.DONE, stats={"selected": 2}, report_path="r.md")
        with self.assertLogs("newsletter_triage.db", level="WARNING"):
            db.mark_run(self.run_id, status=RunStatus.FAILED, stats={"error": "late"})
        run = db.get_run(self.run_id)
        self.assertEqual((run["status"], run["stats"], run["report_path"]), ("done", {"selected": 2}, "r.md"))


if __name__ == "__main__":
    unittest.main()
