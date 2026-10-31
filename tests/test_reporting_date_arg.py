r"""``reporting_pipeline --date`` accepts YYYYMMDD or YYYY-MM-DD and refuses anything else.

An unparseable value used to fall back to today and a hyphenated one went through
unvalidated, so a typo in ``launch_reporting.bat`` ran the whole pipeline (fetch, upload,
Notion write) for the wrong day and exited 0.

Run: & .\.venv\Scripts\python.exe -m pytest tests/test_reporting_date_arg.py -q
"""

from __future__ import annotations

import argparse
import unittest
from unittest import mock

import reporting_pipeline as rp


class ResolveProcessingDateTests(unittest.TestCase):
    def test_both_accepted_forms_normalise_to_iso(self):
        self.assertEqual(rp._resolve_processing_date("20260929"), "2026-09-29")
        self.assertEqual(rp._resolve_processing_date("2026-09-29"), "2026-09-29")

    def test_no_date_means_today(self):
        self.assertRegex(rp._resolve_processing_date(None), r"^\d{4}-\d{2}-\d{2}$")

    def test_invalid_dates_raise_instead_of_becoming_today(self):
        for value in ("2026929", "tomorrow", "2026-13-01", "2026-9-29", "29-09-2026"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Invalid date format"):
                rp._resolve_processing_date(value)


class MainExitTests(unittest.TestCase):
    def _main(self, date):
        args = argparse.Namespace(debug=False, skip_api=False, skip_processing=False, skip_aggregation=False,
                                  skip_consolidation=False, skip_notion=False, skip_substack=False,
                                  date=date, yes=True)
        with mock.patch.object(rp, "parse_arguments", return_value=args), \
                mock.patch.object(rp, "configure_logger"), \
                mock.patch.object(rp, "run_pipeline", return_value=None) as pipeline:
            try:
                rp.main()
            except SystemExit as exc:
                return exc.code, pipeline
            return None, pipeline

    def test_invalid_date_exits_non_zero_before_running_anything(self):
        code, pipeline = self._main("2026-9x")
        self.assertEqual(code, 2)
        pipeline.assert_not_called()

    def test_valid_date_runs_the_pipeline(self):
        code, pipeline = self._main("20260929")
        self.assertIsNone(code)
        pipeline.assert_called_once()


if __name__ == "__main__":
    unittest.main()
