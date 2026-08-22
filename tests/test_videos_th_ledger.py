"""The tag-along idempotency ledger (issue #239).

Threads is the one scheduled video platform with no ``link TH(v)`` column, so
the sentinel every other platform uses for idempotency has nowhere to live and
TH re-ran on every invocation. On a clean run that never showed, because the
row's WIP-Vd was unticked the moment all four platforms succeeded. On a
*recovery* run it left no good option: re-running to fix a failed leg re-posted
the Threads video, and ``--skip-th`` avoided that only by leaving WIP-Vd
checked forever.

These tests pin the three behaviours that close the gap, and — more importantly
— the one that must NOT change: a TH leg with no ledger entry still runs. The
failure direction matters here. A double post to a live account is strictly
worse than a stale checkbox, so every "did this already happen?" question must
answer "no" when it cannot tell.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning.videos import schedule_videos_posts as svp  # noqa: E402
from planning.videos.videos_ledger import TagAlongLedger  # noqa: E402


class _LedgerTestCase(unittest.TestCase):
    """Every ledger lives in its own temp dir — never the real results/videos/."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "videos" / "tag_along_scheduled.json"

    def ledger(self) -> TagAlongLedger:
        return TagAlongLedger(self.path)


class LedgerStorageTests(_LedgerTestCase):

    def test_absent_ledger_reads_as_nothing_scheduled(self):
        """The first-ever run must behave exactly as it does today."""
        self.assertFalse(self.path.exists())
        self.assertFalse(self.ledger().is_scheduled("th", "20260825"))

    def test_record_then_read_back_in_a_fresh_process(self):
        self.assertTrue(self.ledger().record("th", "20260825", detail="TH:LIVE"))
        fresh = self.ledger()  # a separate instance == a separate run
        self.assertTrue(fresh.is_scheduled("th", "20260825"))
        self.assertIsNotNone(fresh.recorded_at("th", "20260825"))

    def test_record_is_scoped_to_its_platform_and_day(self):
        self.ledger().record("th", "20260825")
        fresh = self.ledger()
        self.assertFalse(fresh.is_scheduled("th", "20260901"))
        self.assertFalse(fresh.is_scheduled("ig", "20260825"))

    def test_recording_a_second_day_keeps_the_first(self):
        self.ledger().record("th", "20260825")
        self.ledger().record("th", "20260901")
        fresh = self.ledger()
        self.assertTrue(fresh.is_scheduled("th", "20260825"))
        self.assertTrue(fresh.is_scheduled("th", "20260901"))

    def test_a_concurrent_writer_is_not_clobbered(self):
        """``record`` merges onto disk, not onto the snapshot it was built with.

        A videos run spans minutes across four browser sessions — long enough
        for a second invocation to write between construction and record.
        """
        stale = self.ledger()               # snapshot taken now, before...
        self.ledger().record("th", "20260901")   # ...another run records
        stale.record("th", "20260825")
        fresh = self.ledger()
        self.assertTrue(fresh.is_scheduled("th", "20260901"),
                        "the concurrent run's entry was clobbered")
        self.assertTrue(fresh.is_scheduled("th", "20260825"))

    def test_a_corrupt_ledger_degrades_to_nothing_scheduled(self):
        """Never raise: an unreadable ledger falls back to today's behaviour."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json at all", encoding="utf-8")
        with self.assertLogs("videos_schedule", level="ERROR"):
            self.assertFalse(self.ledger().is_scheduled("th", "20260825"))

    def test_a_ledger_of_the_wrong_shape_degrades_the_same_way(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        with self.assertLogs("videos_schedule", level="ERROR"):
            self.assertFalse(self.ledger().is_scheduled("th", "20260825"))

    def test_a_failed_write_is_reported_not_raised(self):
        """The post is already live by then — a bookkeeping miss must not abort."""
        led = self.ledger()
        with mock.patch.object(TagAlongLedger, "_write", side_effect=OSError("disk full")):
            with self.assertLogs("videos_schedule", level="WARNING"):
                self.assertFalse(led.record("th", "20260825"))


def _row(day_title: str = "20260825", *, th_in_scope: bool = True) -> svp._RowState:
    """A minimal _RowState shaped like a real video row on the TH leg."""
    state = svp._RowState(
        page_id="page-" + day_title,
        day=date(int(day_title[:4]), int(day_title[4:6]), int(day_title[6:])),
        payload=None,
        link_status={"li": None, "ig": None, "tw": None, "th": None, "sb": None},
        in_scope={"li": True, "ig": True, "tw": True, "th": th_in_scope},
    )
    return state


class EligibilityTests(_LedgerTestCase):
    """`_rows_for_platform` — does the TH leg run?"""

    def test_th_runs_when_the_ledger_has_no_entry(self):
        """The behaviour that must NOT change: unknown means run it."""
        row = _row()
        eligible = svp._rows_for_platform([row], "th", force=False, ledger=self.ledger())
        self.assertEqual(len(eligible), 1)
        self.assertNotIn("th", row.driver_status)

    def test_th_is_skipped_once_the_ledger_records_it(self):
        self.ledger().record("th", "20260825", detail="TH:LIVE")
        row = _row()
        eligible = svp._rows_for_platform([row], "th", force=False, ledger=self.ledger())
        self.assertEqual(eligible, [])
        self.assertEqual(row.driver_status["th"], "SKIP")
        self.assertIn("tag-along ledger", row.driver_detail["th"])

    def test_force_reschedules_a_recorded_row(self):
        """The deliberate escape hatch — the only way back to a re-post."""
        self.ledger().record("th", "20260825")
        row = _row()
        eligible = svp._rows_for_platform([row], "th", force=True, ledger=self.ledger())
        self.assertEqual(len(eligible), 1)

    def test_a_recorded_day_does_not_skip_a_different_day(self):
        self.ledger().record("th", "20260825")
        row = _row("20260901")
        eligible = svp._rows_for_platform([row], "th", force=False, ledger=self.ledger())
        self.assertEqual(len(eligible), 1)

    def test_the_ledger_does_not_gate_a_platform_that_has_a_link_column(self):
        """Only tag-along platforms consult it; LI/IG/TW keep their sentinel."""
        self.ledger().record("li", "20260825")
        row = _row()
        eligible = svp._rows_for_platform([row], "li", force=False, ledger=self.ledger())
        self.assertEqual(len(eligible), 1)


class UntickDecisionTests(_LedgerTestCase):
    """`_maybe_untick_wip` — does the row's WIP-Vd checkbox clear?"""

    def _decide(self, state, ledger):
        """Run the decision with the Notion write stubbed; returns the stub too."""
        cols = {"wip_checkbox": "Work in Progress Video"}
        with mock.patch.object(svp, "set_field") as set_field:
            action, reason = svp._maybe_untick_wip(
                mock.MagicMock(), cols, state, False, ledger,
            )
        return action, reason, set_field

    def _completed_row(self, *, th_status: str, sb_link: str = "https://sb.example/x"):
        state = _row()
        state.link_status["sb"] = sb_link
        for p in ("li", "ig", "tw"):
            state.driver_status[p] = "LIVE"
        state.driver_status["th"] = th_status
        return state

    def test_recovery_run_unticks_when_the_ledger_covers_the_skipped_th_leg(self):
        """The whole point of #239: no re-post AND no stranded checkbox."""
        self.ledger().record("th", "20260825", detail="TH:LIVE")
        state = self._completed_row(th_status="SKIP")
        action, reason, set_field = self._decide(state, self.ledger())
        self.assertEqual(action, "unticked", reason)
        self.assertEqual(set_field.call_args.args[3], False)  # checkbox cleared

    def test_a_skipped_th_leg_with_no_ledger_entry_still_blocks_the_untick(self):
        """The conservative direction — an unproven leg keeps WIP-Vd checked."""
        state = self._completed_row(th_status="SKIP")
        action, reason, set_field = self._decide(state, self.ledger())
        self.assertEqual(action, "kept-checked")
        self.assertIn("TH=SKIP", reason)

    def test_a_live_th_leg_still_unticks_without_any_ledger_entry(self):
        """Unchanged: a LIVE result this run was always sufficient."""
        state = self._completed_row(th_status="LIVE")
        action, reason, set_field = self._decide(state, self.ledger())
        self.assertEqual(action, "unticked", reason)

    def test_the_ledger_backed_untick_says_so_in_the_log(self):
        """A platform that visibly did nothing this run must not untick silently."""
        self.ledger().record("th", "20260825", detail="TH:LIVE")
        state = self._completed_row(th_status="SKIP")
        with self.assertLogs("videos_schedule", level="INFO") as logs:
            self._decide(state, self.ledger())
        self.assertTrue(any("tag-along ledger" in line for line in logs.output),
                        f"no ledger breadcrumb in {logs.output}")

    def test_a_failed_th_leg_is_never_rescued_by_a_ledger_entry(self):
        """A stale entry must not paper over a leg that failed *this* run."""
        self.ledger().record("th", "20260825")
        state = self._completed_row(th_status="FAIL")
        action, reason, set_field = self._decide(state, self.ledger())
        self.assertEqual(action, "kept-checked")
        self.assertIn("TH=FAIL", reason)

    def test_the_ledger_does_not_rescue_a_skipped_platform_that_has_a_link_column(self):
        self.ledger().record("li", "20260825")
        state = self._completed_row(th_status="LIVE")
        state.driver_status["li"] = "SKIP"      # skipped by flag, link empty
        action, reason, set_field = self._decide(state, self.ledger())
        self.assertEqual(action, "kept-checked")
        self.assertIn("LI=SKIP", reason)


if __name__ == "__main__":
    unittest.main()
