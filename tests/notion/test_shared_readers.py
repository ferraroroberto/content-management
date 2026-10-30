"""The shared day parser and page-title reader every caller now goes through.

``config.dates.parse_day`` is the one ``YYYYMMDD`` / ``YYYY-MM-DD`` parser;
``reporting.notion._client.page_title`` is the one first-title-property reader.
Their callers used to carry hand-written copies with different failure and
multi-segment behaviour, so the contract is pinned here.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.notion.test_shared_readers -v
"""

from __future__ import annotations

import unittest
from datetime import date

from config.dates import parse_day
from planning.substack.substack_session import normalize_day
from reporting.notion._client import page_title
from reporting.scrape_client.base import normalize_target_date


def _title_page(*segments: str) -> dict:
    return {
        "properties": {
            "note": {"type": "rich_text", "rich_text": [{"plain_text": "ignored"}]},
            "day": {"type": "title", "title": [{"plain_text": s} for s in segments]},
        }
    }


class ParseDayTests(unittest.TestCase):
    def test_both_spellings_parse_to_the_same_date(self) -> None:
        self.assertEqual(parse_day("20260105"), date(2026, 1, 5))
        self.assertEqual(parse_day(" 2026-01-05 "), date(2026, 1, 5))

    def test_unparseable_values_raise_value_error(self) -> None:
        for bad in ("", "tomorrow", "2026-13-01", "20261301", "2026-1-5x"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                parse_day(bad)

    def test_callers_share_the_failure_contract(self) -> None:
        with self.assertRaises(ValueError):
            normalize_target_date("2026-13-01")
        with self.assertRaises(ValueError):
            normalize_day("nonsense")

    def test_callers_keep_their_output_formats(self) -> None:
        self.assertEqual(normalize_target_date("20260105"), "2026-01-05")
        self.assertEqual(normalize_day("2026-01-05"), "20260105")


class PageTitleTests(unittest.TestCase):
    def test_multi_segment_title_is_joined_and_stripped(self) -> None:
        self.assertEqual(page_title(_title_page(" 2026", "0105 ")), "20260105")

    def test_page_without_a_title_property_is_empty(self) -> None:
        self.assertEqual(page_title({"properties": {}}), "")
        self.assertEqual(page_title({}), "")


if __name__ == "__main__":
    unittest.main()
