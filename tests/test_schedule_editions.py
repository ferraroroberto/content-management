"""The future-edition scheduler's arithmetic must be exactly right (issue #230).

``newsletter/schedule_editions.py`` creates the empty future rows that ②
Archive files articles against. Two of its properties are load-bearing and
neither is visible until something downstream breaks:

* **The number never duplicates.** It continues from the highest edition that
  exists *anywhere* in the table, not off whichever row happens to be newest —
  so a table whose numbering and dating disagree still yields a fresh number.
* **The number is zero-padded to three digits.** ``normalize_newsletter_number``
  (``newsletter/build_newsletter.py``) rejects anything else, so an unpadded
  ``N34`` would sail through the write and only fail later, at ④ Build.

Everything pinned here is pure — no Notion, no network, no clock.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from datetime import date

from newsletter.build_newsletter import normalize_newsletter_number
from newsletter.schedule_editions import (
    DEFAULT_TARGET,
    BufferState,
    format_edition_number,
    parse_edition_number,
    plan_editions,
    summarize_editions,
)


def _row(number: str | None, day: str | None) -> dict:
    """A Notion page shaped like a newsletter DB row."""
    props: dict = {}
    if number is not None:
        props["number"] = {"title": [{"plain_text": number}]}
    props["Date"] = {"date": ({"start": day} if day is not None else None)}
    return {"properties": props}


class ParseAndFormatTests(unittest.TestCase):
    def test_parse_accepts_both_spellings(self):
        self.assertEqual(parse_edition_number("N233"), 233)
        self.assertEqual(parse_edition_number("233"), 233)
        self.assertEqual(parse_edition_number("  n057 "), 57)

    def test_parse_rejects_junk(self):
        for raw in ("", "   ", "draft", "N", "N23a", None):
            self.assertIsNone(parse_edition_number(raw), raw)

    def test_format_zero_pads_to_three_digits(self):
        self.assertEqual(format_edition_number(57), "N057")
        self.assertEqual(format_edition_number(233), "N233")

    def test_formatted_numbers_survive_the_build_step(self):
        # The whole reason for the padding: ④ Build parses these back.
        for value in (7, 57, 233, 999):
            self.assertEqual(
                normalize_newsletter_number(format_edition_number(value)),
                format_edition_number(value),
            )


class PlanEditionsTests(unittest.TestCase):
    def test_sequence_continues_by_number_and_week(self):
        plan = plan_editions(latest_number=233, latest_date=date(2026, 9, 26), count=3)
        self.assertEqual(plan, [
            ("N234", date(2026, 10, 3)),
            ("N235", date(2026, 10, 10)),
            ("N236", date(2026, 10, 17)),
        ])

    def test_weekday_is_inherited_from_the_latest_row(self):
        # 2026-09-26 is a Saturday; every generated date must be one too.
        plan = plan_editions(latest_number=233, latest_date=date(2026, 9, 26), count=8)
        self.assertTrue(all(d.weekday() == 5 for _, d in plan), plan)

    def test_non_positive_count_is_a_no_op(self):
        for count in (0, -1):
            self.assertEqual(
                plan_editions(latest_number=233, latest_date=date(2026, 9, 26),
                              count=count),
                [],
            )

    def test_crossing_a_month_and_year_boundary(self):
        plan = plan_editions(latest_number=299, latest_date=date(2026, 12, 26), count=2)
        self.assertEqual(plan, [
            ("N300", date(2027, 1, 2)),
            ("N301", date(2027, 1, 9)),
        ])


class SummarizeEditionsTests(unittest.TestCase):
    TODAY = date(2026, 8, 23)

    def test_counts_future_editions_the_way_pick_newsletter_does(self):
        rows = [
            _row("N230", "2026-08-01"),   # past
            _row("N231", "2026-08-23"),   # today — on_or_after, so future
            _row("N232", "2026-08-30"),   # future
        ]
        state = summarize_editions(rows, today=self.TODAY)
        self.assertEqual(state.future_count, 2)
        self.assertEqual(state.total, 3)
        self.assertEqual(state.latest_number, 232)
        self.assertEqual(state.latest_date, date(2026, 8, 30))
        self.assertEqual(state.latest_label, "N232")

    def test_max_number_is_independent_of_max_date(self):
        # A row numbered high but dated low must still bump the sequence, or
        # the next write would re-use N235 and duplicate it.
        rows = [_row("N235", "2026-01-03"), _row("N231", "2026-09-05")]
        state = summarize_editions(rows, today=self.TODAY)
        self.assertEqual(state.latest_number, 235)
        self.assertEqual(state.latest_date, date(2026, 9, 5))
        self.assertEqual(plan_editions(latest_number=state.latest_number,
                                       latest_date=state.latest_date, count=1),
                         [("N236", date(2026, 9, 12))])

    def test_datetime_valued_dates_are_truncated_to_the_day(self):
        rows = [_row("N231", "2026-08-30T00:00:00.000+02:00")]
        state = summarize_editions(rows, today=self.TODAY)
        self.assertEqual(state.latest_date, date(2026, 8, 30))

    def test_rows_missing_number_or_date_do_not_break_the_fold(self):
        rows = [
            _row("N230", "2026-08-01"),
            _row(None, "2026-09-06"),      # dated but unnumbered
            _row("N231", None),            # numbered but undated
            _row(None, None),              # neither — skipped entirely
            _row("draft", "2026-09-13"),   # unparsable number
        ]
        state = summarize_editions(rows, today=self.TODAY)
        self.assertEqual(state.total, 4)
        self.assertEqual(state.latest_number, 231)
        self.assertEqual(state.latest_date, date(2026, 9, 13))
        self.assertEqual(state.future_count, 2)

    def test_returns_none_when_there_is_no_sequence_to_continue(self):
        self.assertIsNone(summarize_editions([], today=self.TODAY))
        self.assertIsNone(summarize_editions([_row(None, "2026-09-06")], today=self.TODAY))
        self.assertIsNone(summarize_editions([_row("N231", None)], today=self.TODAY))


class ShortfallTests(unittest.TestCase):
    def _state(self, future: int) -> BufferState:
        return BufferState(latest_number=233, latest_date=date(2026, 9, 26),
                           future_count=future, total=233)

    def test_shortfall_tops_up_to_the_target(self):
        self.assertEqual(self._state(6).shortfall(DEFAULT_TARGET), 2)
        self.assertEqual(self._state(0).shortfall(DEFAULT_TARGET), DEFAULT_TARGET)

    def test_a_full_buffer_asks_for_nothing(self):
        self.assertEqual(self._state(DEFAULT_TARGET).shortfall(DEFAULT_TARGET), 0)
        self.assertEqual(self._state(DEFAULT_TARGET + 5).shortfall(DEFAULT_TARGET), 0)


if __name__ == "__main__":
    unittest.main()
