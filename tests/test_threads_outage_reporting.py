"""Regression coverage for complete Threads source outages (issue #268)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import reporting_pipeline as rp


class ThreadsOutageReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        rp.logger = MagicMock()
        self.config = {
            "threads_profile": {"api_url": "https://example.test/profile"},
            "threads_posts": {"api_url": "https://example.test/posts"},
            "linkedin_profile": {"api_url": "https://example.test/linkedin"},
        }

    def test_complete_threads_outage_is_one_skipped_platform_alert(self) -> None:
        failures = rp.PipelineFailures()
        with patch.object(rp, "check_file_exists_for_date", return_value=(False, "unused")):
            rp.check_endpoint_coverage(self.config, "2026-09-05", failures)

        self.assertEqual(
            failures.skipped_platforms,
            ["threads (profile + posts; upstream unavailable)"],
        )
        self.assertEqual(failures.missing_endpoints, ["linkedin_profile"])
        self.assertTrue(failures.any())
        message = rp._build_alert_message(failures, "2026-09-05")
        self.assertIn("Skipped platforms (upstream unavailable):", message)
        self.assertNotIn("threads_profile", message)
        self.assertNotIn("threads_posts", message)

    def test_threads_metrics_are_not_a_second_failure_after_complete_outage(self) -> None:
        failures = rp.PipelineFailures()
        failures.skipped_platforms.append("threads (profile + posts; upstream unavailable)")
        rows = [("date",), ("post_id_threads_no_video",), ("post_id_threads_video",)]
        cursor = MagicMock(description=rows)
        cursor.fetchone.return_value = ("2026-09-05", None, None)
        cursor.__enter__.return_value = cursor
        connection = MagicMock()
        connection.cursor.return_value = cursor

        with patch("reporting.process.supabase_uploader.get_db_connection", return_value=connection):
            rp.check_posts_coverage("2026-09-05", failures)

        self.assertNotIn("threads", failures.missing_post_metrics)

    def test_one_missing_threads_endpoint_remains_a_hard_coverage_failure(self) -> None:
        failures = rp.PipelineFailures()
        exists = {
            "threads_profile": (False, "unused"),
            "threads_posts": (True, "unused"),
            "linkedin_profile": (True, "unused"),
        }
        with patch.object(rp, "check_file_exists_for_date", side_effect=lambda key, *_: exists[key]):
            rp.check_endpoint_coverage(self.config, "2026-09-05", failures)

        self.assertEqual(failures.skipped_platforms, [])
        self.assertEqual(failures.missing_endpoints, ["threads_profile"])


if __name__ == "__main__":
    unittest.main()
