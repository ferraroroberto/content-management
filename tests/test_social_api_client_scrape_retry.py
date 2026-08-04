"""Regression test: Playwright/native-sourced endpoints (issue #205) get the
same resilience RapidAPI endpoints already had -- a single transient failure
(cold browser profile, slow page load on the scheduled 06:00 run) shouldn't
permanently drop a day's data. ``_fetch_scrape_with_retries`` retries the
underlying fetch before ``get_api_data`` gives up on it.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import logging
import unittest
from unittest.mock import MagicMock, patch

from reporting.social_client import social_api_client as sac


class ScrapeRetryTests(unittest.TestCase):
    def setUp(self):
        sac.logger = MagicMock()
        sac.logger.level = logging.INFO

    def test_retries_until_success(self):
        fetch_fn = MagicMock(side_effect=[None, {"num_followers": 100}])
        with patch.object(sac, "interruptible_sleep", return_value=False) as sleep_mock:
            result = sac._fetch_scrape_with_retries(fetch_fn, "twitter_profile", "2026-08-04")
        self.assertEqual(result, {"num_followers": 100})
        self.assertEqual(fetch_fn.call_count, 2)
        sleep_mock.assert_called_once_with(sac.SCRAPE_RETRY_BACKOFF[0])

    def test_gives_up_after_all_attempts_exhausted(self):
        fetch_fn = MagicMock(return_value=None)
        with patch.object(sac, "interruptible_sleep", return_value=False) as sleep_mock:
            result = sac._fetch_scrape_with_retries(fetch_fn, "twitter_profile", "2026-08-04")
        self.assertIsNone(result)
        self.assertEqual(fetch_fn.call_count, len(sac.SCRAPE_RETRY_BACKOFF) + 1)
        self.assertEqual(sleep_mock.call_count, len(sac.SCRAPE_RETRY_BACKOFF))

    def test_user_skip_stops_retrying_early(self):
        fetch_fn = MagicMock(return_value=None)
        with patch.object(sac, "interruptible_sleep", return_value=True):
            result = sac._fetch_scrape_with_retries(fetch_fn, "twitter_profile", "2026-08-04")
        self.assertIsNone(result)
        self.assertEqual(fetch_fn.call_count, 1)

    def test_get_api_data_routes_playwright_source_through_retries(self):
        config = {"twitter_profile": {"source": "playwright"}}
        with patch.object(sac, "_fetch_via_playwright", return_value=None) as playwright_mock, \
             patch.object(sac, "interruptible_sleep", return_value=False):
            result = sac.get_api_data("twitter_profile", config, reference_date="2026-08-04")
        self.assertIsNone(result)
        self.assertEqual(playwright_mock.call_count, len(sac.SCRAPE_RETRY_BACKOFF) + 1)

    def test_get_api_data_routes_native_source_through_retries(self):
        config = {"substack_profile": {"source": "native"}}
        with patch.object(sac, "_fetch_via_native", return_value=None) as native_mock, \
             patch.object(sac, "interruptible_sleep", return_value=False):
            result = sac.get_api_data("substack_profile", config, reference_date="2026-08-04")
        self.assertIsNone(result)
        self.assertEqual(native_mock.call_count, len(sac.SCRAPE_RETRY_BACKOFF) + 1)


if __name__ == "__main__":
    unittest.main()
