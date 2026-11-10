"""The planning row outcome is declared once and every reader agrees on it.

Pins ``planning.row_outcome`` (the six outcomes, the leg derivation and the two
predicates) and the readers that call it: the summary table, the verdict, the
failure flag and the result JSON.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import argparse
import itertools
import unittest
from datetime import date, datetime
from pathlib import Path

from planning._failure import classify
from planning.row_outcome import (
    RowStatus,
    derive_row_status,
    has_scheduled,
    is_failure,
    parse,
)
from planning.videos.schedule_videos_posts import _aggregate_row_status, _RowState
from planning_pipeline import (
    PlatformResult,
    _build_result_json,
    _build_summary_md,
    _platform_failed,
)

LIVE, DRY, SKIP = RowStatus.LIVE, RowStatus.DRY, RowStatus.SKIP
FAIL, PARTIAL, LOGIN = RowStatus.FAIL, RowStatus.PARTIAL, RowStatus.LOGIN_REQUIRED

# (legs, dry_run, row status): one row per reachable leg combination.
DERIVE_TABLE = [
    ((), False, SKIP),
    ((SKIP, SKIP, SKIP, SKIP), False, SKIP),
    ((SKIP, SKIP, SKIP, SKIP), True, SKIP),
    ((LIVE, LIVE, LIVE, LIVE), False, LIVE),
    ((LIVE, SKIP, SKIP, SKIP), False, LIVE),
    ((LIVE, LIVE), False, LIVE),
    ((DRY, DRY, DRY, DRY), True, DRY),
    ((DRY, SKIP, SKIP, SKIP), True, DRY),
    ((DRY, DRY), True, DRY),
    ((LIVE, FAIL, SKIP, SKIP), False, PARTIAL),
    ((LIVE, FAIL), False, PARTIAL),
    ((DRY, FAIL, SKIP, SKIP), True, PARTIAL),
    ((DRY, FAIL), True, PARTIAL),
    ((FAIL, FAIL, SKIP, SKIP), False, FAIL),
    ((FAIL, FAIL, SKIP, SKIP), True, FAIL),
    ((FAIL, FAIL), False, FAIL),
    ((FAIL, SKIP, SKIP, SKIP), False, FAIL),
    ((FAIL, LIVE), True, FAIL),
    ((DRY, FAIL), False, FAIL),
    ((LOGIN, LIVE, SKIP, SKIP), False, LOGIN),
    ((LOGIN, FAIL, DRY, SKIP), True, LOGIN),
    ((LOGIN, SKIP, SKIP, SKIP), False, LOGIN),
]

# status -> (is_failure, has_scheduled)
FLAGS_TABLE = {
    LIVE: (False, True),
    DRY: (False, False),
    SKIP: (False, False),
    FAIL: (True, False),
    PARTIAL: (True, True),
    LOGIN: (True, False),
}


class RowStatusTests(unittest.TestCase):
    def test_string_values_are_the_ones_the_result_json_carries(self):
        self.assertEqual(
            [s.value for s in RowStatus],
            ["LIVE", "DRY", "SKIP", "FAIL", "PARTIAL", "LOGIN-REQUIRED"],
        )

    def test_a_status_compares_equal_to_its_plain_string(self):
        self.assertEqual(RowStatus.LOGIN_REQUIRED, "LOGIN-REQUIRED")
        self.assertEqual(f"{RowStatus.PARTIAL}", "PARTIAL")

    def test_parse_names_each_status_and_refuses_the_rest(self):
        for status in RowStatus:
            self.assertIs(parse(status.value), status)
        self.assertIs(parse(" live "), LIVE)
        for unknown in ("OTHER", "", "DONE", None, 3):
            self.assertIsNone(parse(unknown), repr(unknown))


class DeriveTableTests(unittest.TestCase):
    def test_every_reachable_leg_combination(self):
        for legs, dry_run, expected in DERIVE_TABLE:
            with self.subTest(legs=[str(leg) for leg in legs], dry_run=dry_run):
                self.assertIs(derive_row_status(legs, dry_run), expected)

    def test_flags_for_every_status(self):
        self.assertEqual(set(FLAGS_TABLE), set(RowStatus))
        for status, (failure, scheduled) in FLAGS_TABLE.items():
            with self.subTest(status=str(status)):
                self.assertIs(is_failure(status), failure)
                self.assertIs(has_scheduled(status), scheduled)

    def test_unknown_string_is_a_failure_and_never_scheduled(self):
        for raw in ("OTHER", "", None, "done"):
            with self.subTest(raw=raw):
                self.assertTrue(is_failure(raw))
                self.assertFalse(has_scheduled(raw))

    # state-machine-proof: planning-row-outcome
    def test_a_failed_leg_never_reads_as_a_clean_row(self):
        """The illegal transition: a row with a failed leg ending LIVE, DRY or SKIP."""
        leg_choices = (LIVE, DRY, SKIP, FAIL)
        for dry_run in (False, True):
            for legs in itertools.product(leg_choices, repeat=3):
                if FAIL not in legs:
                    continue
                with self.subTest(legs=[str(leg) for leg in legs], dry_run=dry_run):
                    self.assertIn(
                        derive_row_status(legs, dry_run), (PARTIAL, FAIL),
                    )

    # state-machine-proof: planning-row-outcome
    def test_an_unrecognised_leg_never_makes_a_row_live(self):
        """An unreadable leg cannot be the one that completed: with no real LIVE leg the row is FAIL."""
        self.assertIs(derive_row_status([None, "done"], dry_run=False), FAIL)
        self.assertIs(derive_row_status([None, FAIL], dry_run=False), FAIL)


class VideosAggregateUsesTheDeriveTests(unittest.TestCase):
    def _state(self, **driver_status: str) -> _RowState:
        state = _RowState(
            page_id="p", day=date(2026, 9, 29), payload=None, link_status={}, in_scope={},
        )
        state.driver_status = dict(driver_status)
        return state

    def test_status_and_detail(self):
        status, detail = _aggregate_row_status(
            self._state(li="LIVE", ig="FAIL", tw="SKIP"), dry_run=False,
        )
        self.assertEqual((status, detail), ("PARTIAL", "LI:LIVE, IG:FAIL, TW:SKIP, TH:SKIP"))
        self.assertIs(type(status), str)


class InstagramLegsTests(unittest.TestCase):
    """The Instagram driver passes its story and post legs through the same derive."""

    def test_story_live_post_failed_is_partial_not_fail(self):
        self.assertIs(derive_row_status([LIVE, FAIL], dry_run=False), PARTIAL)

    def test_failed_leg_in_a_dry_run_is_not_a_clean_dry(self):
        self.assertIsNot(derive_row_status([DRY, FAIL], dry_run=True), DRY)


MIXED_ROWS = [
    {"day": "Mon", "status": "PARTIAL", "detail": "LI:LIVE, IG:FAIL, TW:SKIP, TH:SKIP"},
    {"day": "Tue", "status": "SKIP", "detail": "LI:SKIP, IG:SKIP, TW:SKIP, TH:SKIP"},
]


class ReadersAgreeTests(unittest.TestCase):
    """The probe from the issue: one PARTIAL row and one SKIP row."""

    def setUp(self):
        self.result = PlatformResult(name="Videos", rows=list(MIXED_ROWS))
        self.args = argparse.Namespace(live=True, dry_run=False)
        self.when = datetime(2026, 1, 1, 12, 0, 0)

    def test_summary_table_has_a_bucket_for_partial_and_skip(self):
        self.assertEqual(
            self.result.status_counts,
            {"LIVE": 0, "DRY": 0, "SKIP": 1, "FAIL": 0, "PARTIAL": 1,
             "LOGIN-REQUIRED": 0, "OTHER": 0},
        )

    def test_unknown_status_lands_in_other(self):
        r = PlatformResult(name="X", rows=[{"day": "Mon", "status": "WEIRD", "detail": ""}])
        self.assertEqual(r.status_counts["OTHER"], 1)
        self.assertTrue(_platform_failed(r))

    def test_failure_flag_and_verdict_agree_it_is_mixed(self):
        self.assertTrue(_platform_failed(self.result))
        md = _build_summary_md(self.args, [self.result], self.when, self.when)
        self.assertIn("Mixed result", md)
        self.assertNotIn("Run finished with failures", md)

    def test_failed_rows_without_a_live_post_read_as_failures(self):
        r = PlatformResult(name="Videos", rows=[{"day": "Mon", "status": "FAIL", "detail": "x"}])
        md = _build_summary_md(self.args, [r], self.when, self.when)
        self.assertIn("Run finished with failures", md)


GOLDEN_ROWS = [
    {"day": "d1", "status": "LIVE", "detail": "post:LIVE"},
    {"day": "d2", "status": "DRY", "detail": "post:DRY-OK"},
    {"day": "d3", "status": "SKIP", "detail": "LI:SKIP"},
    {"day": "d4", "status": "FAIL", "detail": "x timeout (screenshot a.png)"},
    {"day": "d5", "status": "PARTIAL", "detail": "LI:LIVE, IG:FAIL"},
    {"day": "d6", "status": "LOGIN-REQUIRED", "detail": "logged out"},
]
# status, failure_kind, screenshot per row, as the result JSON carried them before
# the outcome was declared in one module.
GOLDEN_JSON_ROWS = [
    ("LIVE", "none", ""),
    ("DRY", "none", ""),
    ("SKIP", "none", ""),
    ("FAIL", "ui-drift", "a.png"),
    ("PARTIAL", "other", ""),
    ("LOGIN-REQUIRED", "login-required", ""),
]


class ResultJsonGoldenTests(unittest.TestCase):
    def test_result_json_keeps_its_status_strings_and_shape(self):
        when = datetime(2026, 1, 1, 12, 0, 0)
        args = argparse.Namespace(live=True, dry_run=False)
        record = _build_result_json(
            args, [PlatformResult(name="Videos", rows=GOLDEN_ROWS, duration_s=3.0)],
            when, when, 11, Path("summary.md"),
        )
        self.assertEqual(record["verdict"], "failures")
        rows = record["platforms"][0]["rows"]
        self.assertEqual(
            [(r["status"], r["failure_kind"], r["screenshot"]) for r in rows],
            GOLDEN_JSON_ROWS,
        )
        self.assertEqual(
            [set(r) for r in rows],
            [{"platform", "day", "status", "detail", "screenshot", "failure_kind"}] * 6,
        )
        for row in rows:
            self.assertIs(type(row["status"]), str)

    def test_classify_keeps_reading_a_partial_row_from_its_detail(self):
        self.assertEqual(classify("PARTIAL", "story:FAIL(locator timed out)"), "ui-drift")
        self.assertEqual(classify("PARTIAL", "LI:LIVE, IG:FAIL"), "other")
        self.assertEqual(classify("LOGIN-REQUIRED", ""), "login-required")
        self.assertEqual(classify("SKIP", "anything"), "none")


if __name__ == "__main__":
    unittest.main()
