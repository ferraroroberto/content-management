"""A Threads coverage gap is judged against the Notion editorial row of the covered day.

``check_posts_coverage`` flags a platform with no consolidated post metrics. For
Threads the cause is either nothing queued for the covered day (an alert) or
content queued that never reached Threads (a failure: the weekly planning run
did not schedule it). The Notion lookup is mocked; no network, no live Notion.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from test_posts_coverage_substack_gap import _FakeConnection, _row_missing_only

import reporting_pipeline as rp

THREADS_COLUMNS = {"title_day": "day", "caption_text": "text TH"}
THREADS_CONFIG = {"editorial_db_id": "db", "editorial_columns": THREADS_COLUMNS}


class ThreadsCoverageGapTests(unittest.TestCase):
    def setUp(self):
        columns, row = _row_missing_only("threads")
        connection_patch = patch(
            "reporting.process.supabase_uploader.get_db_connection",
            return_value=_FakeConnection(columns, row),
        )
        connection_patch.start()
        self.addCleanup(connection_patch.stop)
        rp.logger = MagicMock()

    def _run(self, queued):
        with patch.object(rp, "_threads_had_editorial_content", return_value=queued) as lookup:
            failures = rp.PipelineFailures()
            rp.check_posts_coverage("2026-11-10", failures)
        return failures, lookup

    def test_nothing_queued_is_alert_not_failure(self):
        failures, lookup = self._run(False)

        lookup.assert_called_once_with("20261109")
        self.assertNotIn("threads", failures.missing_post_metrics)
        self.assertFalse(failures.any())
        self.assertTrue(
            any("expected, not a failure" in c.args[0] for c in rp.logger.warning.call_args_list),
            rp.logger.warning.call_args_list,
        )
        rp.logger.error.assert_not_called()

    def test_content_queued_but_no_post_is_a_failure(self):
        failures, _ = self._run(True)

        self.assertIn("threads", failures.missing_post_metrics)
        self.assertTrue(failures.any())
        self.assertTrue(
            any("no post metrics for threads" in c.args[0] for c in rp.logger.error.call_args_list),
            rp.logger.error.call_args_list,
        )

    def test_unverifiable_is_never_a_pass(self):
        failures, _ = self._run(None)

        self.assertIn("threads", failures.missing_post_metrics)
        self.assertTrue(failures.any())

    def test_skipped_source_endpoints_still_short_circuit_the_lookup(self):
        with patch.object(rp, "_threads_had_editorial_content") as lookup:
            failures = rp.PipelineFailures()
            failures.skipped_platforms.append("threads")
            rp.check_posts_coverage("2026-11-10", failures)

        lookup.assert_not_called()
        self.assertNotIn("threads", failures.missing_post_metrics)


class ThreadsEditorialLookupTests(unittest.TestCase):
    """``_threads_had_editorial_content`` reads the Notion row with Notion mocked."""

    def _lookup(self, *, row, text=None, client="client", read_error=None):
        patches = [
            patch("planning.threads.threads_session.load_threads_config", return_value=THREADS_CONFIG),
            patch("planning.threads.threads_session.load_notion_token", return_value="token"),
            patch("reporting.notion.editorial.init_notion_client", return_value=client),
            patch(
                "reporting.notion.editorial.get_row_by_day",
                side_effect=read_error,
                return_value=row,
            ),
            patch("reporting.notion.editorial.get_field", return_value=text),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        rp.logger = MagicMock()
        return rp._threads_had_editorial_content("20261109")

    def test_no_row_means_nothing_queued(self):
        self.assertIs(self._lookup(row=None), False)

    def test_blank_caption_means_nothing_queued(self):
        self.assertIs(self._lookup(row={"id": "r"}, text="   "), False)

    def test_caption_column_absent_from_row_is_unverified(self):
        self.assertIsNone(self._lookup(row={"id": "r"}, text=None))

    def test_caption_present_means_queued(self):
        self.assertIs(self._lookup(row={"id": "r"}, text="synthetic caption"), True)

    def test_no_notion_client_is_unverified(self):
        self.assertIsNone(self._lookup(row=None, client=None))

    def test_notion_error_is_unverified(self):
        self.assertIsNone(self._lookup(row=None, read_error=RuntimeError("unreachable")))


if __name__ == "__main__":
    unittest.main()
