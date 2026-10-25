r"""Regression tests for issue #260: the Instagram scraper's newest post lost
``posted_at`` silently because ``fetch_posts`` read ``<time datetime>``
immediately after a fixed 1 s sleep instead of waiting for the element, and
swallowed the failure in a bare ``except Exception: pass``.

``reporting/scrape_client/instagram.py`` now exposes the permalink read as a
pure ``_read_permalink_posted_at(page, timeout_ms)`` that waits explicitly for
the element and reports *why* it failed, plus ``_scrape_permalink`` which
retries the permalink once and logs a WARNING whenever ``posted_at`` still
comes back ``None``. The race is intermittent by nature, so the proof here is
not "ran once and it worked" — it's a fake page with a virtual clock that
renders the date *after* the old fixed-sleep window, showing the new code
waits it out, plus explicit coverage of the never-appears / empty / malformed
failure paths and their distinct logged reasons.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import logging
import unittest
from unittest import mock
from unittest.mock import MagicMock

from reporting.scrape_client import instagram as ig

DATETIME_ATTR = "2026-08-30T12:00:00.000Z"


class _FakeTimeLocator:
    def __init__(self, page: "_FakePermalinkPage") -> None:
        self._page = page

    @property
    def first(self) -> "_FakeTimeLocator":
        return self

    def get_attribute(self, name: str) -> str | None:
        assert name == "datetime"
        return self._page.datetime_attr


class _FakeVideoLocator:
    def __init__(self, present: bool, *, raises: bool = False) -> None:
        self._present = present
        self._raises = raises

    def count(self) -> int:
        if self._raises:
            raise RuntimeError("locator did not resolve")
        return 1 if self._present else 0


class _FakePermalinkPage:
    """Minimal Playwright ``Page`` with a virtual clock in milliseconds.

    ``appears_at_ms=None`` means the ``time[datetime]`` element never renders
    within any timeout — the "stuck" case. Otherwise ``wait_for_selector``
    only returns once the virtual clock has reached ``appears_at_ms``,
    mirroring the real render delay that raced the old fixed 1 s sleep.
    """

    def __init__(
        self,
        appears_at_ms: float | None,
        *,
        datetime_attr: str | None = DATETIME_ATTR,
        has_video: bool = False,
        video_raises: bool = False,
        goto_fails: bool = False,
    ) -> None:
        self.now = 0.0
        self.appears_at_ms = appears_at_ms
        self.datetime_attr = datetime_attr
        self.has_video = has_video
        self.video_raises = video_raises
        self.goto_fails = goto_fails
        self.goto_calls = 0
        self.wait_calls = 0

    def goto(self, url: str, *, timeout: int, wait_until: str) -> None:
        self.goto_calls += 1
        if self.goto_fails:
            raise RuntimeError("net::ERR_CONNECTION_RESET")

    def wait_for_selector(self, selector: str, *, timeout: int) -> None:
        self.wait_calls += 1
        assert selector == "time[datetime]"
        if self.appears_at_ms is None or self.appears_at_ms > timeout:
            self.now = timeout
            raise TimeoutError(f"{selector} not visible after {timeout}ms")
        self.now = self.appears_at_ms

    def locator(self, selector: str):
        if selector == "time[datetime]":
            return _FakeTimeLocator(self)
        if selector == "video":
            return _FakeVideoLocator(self.has_video, raises=self.video_raises)
        raise AssertionError(f"unexpected selector: {selector}")


class ReadPermalinkPostedAtTests(unittest.TestCase):
    def test_waits_out_a_render_slower_than_the_old_fixed_sleep(self):
        # The old code read the page after a fixed 1000ms sleep. This element
        # renders at 5000ms -- a fixed 1s sleep would have read it too early
        # and gotten None. The new wait must not give up before it appears.
        page = _FakePermalinkPage(appears_at_ms=5000)
        posted_at, reason = ig._read_permalink_posted_at(page, timeout_ms=8000)
        self.assertEqual(posted_at, "2026-08-30")
        self.assertIsNone(reason)
        self.assertGreaterEqual(page.now, 5000, "must actually wait, not read immediately")

    def test_element_never_appearing_is_reported_with_a_distinct_reason(self):
        page = _FakePermalinkPage(appears_at_ms=None)
        posted_at, reason = ig._read_permalink_posted_at(page, timeout_ms=8000)
        self.assertIsNone(posted_at)
        self.assertIn("never appeared", reason)

    def test_empty_datetime_attribute_is_reported_with_a_distinct_reason(self):
        page = _FakePermalinkPage(appears_at_ms=0, datetime_attr="")
        posted_at, reason = ig._read_permalink_posted_at(page, timeout_ms=8000)
        self.assertIsNone(posted_at)
        self.assertIn("empty", reason)

    def test_unparseable_datetime_attribute_is_reported_with_a_distinct_reason(self):
        page = _FakePermalinkPage(appears_at_ms=0, datetime_attr="not-a-date")
        posted_at, reason = ig._read_permalink_posted_at(page, timeout_ms=8000)
        self.assertIsNone(posted_at)
        self.assertIn("unparseable", reason)


class ScrapePermalinkTests(unittest.TestCase):
    def setUp(self):
        self._log_patch = mock.patch.object(ig, "logger", MagicMock())
        self.logger = self._log_patch.start()
        self.logger.level = logging.INFO
        self.addCleanup(self._log_patch.stop)

    def test_retries_once_then_succeeds_on_a_slow_but_recoverable_render(self):
        # First navigation's render never completes in the (default) budget on
        # the first attempt's page instance; simulate that by having the fake
        # succeed only from the second goto onward.
        class _FlakyThenGood(_FakePermalinkPage):
            def goto(self, url, *, timeout, wait_until):
                super().goto(url, timeout=timeout, wait_until=wait_until)
                if self.goto_calls == 1:
                    self.appears_at_ms = None  # never renders on attempt 1
                else:
                    self.appears_at_ms = 100  # renders fine on attempt 2

        page = _FlakyThenGood(appears_at_ms=None)
        posted_at, is_video = ig._scrape_permalink(page, "https://www.instagram.com/x/p/abc/")
        self.assertEqual(posted_at, "2026-08-30")
        self.assertEqual(page.goto_calls, 2, "must re-navigate and retry once")
        self.logger.warning.assert_not_called()

    def test_still_none_after_retry_logs_a_warning_naming_permalink_and_reason(self):
        page = _FakePermalinkPage(appears_at_ms=None)
        permalink = "https://www.instagram.com/x/p/abc/"
        posted_at, is_video = ig._scrape_permalink(page, permalink)
        self.assertIsNone(posted_at)
        self.assertEqual(page.goto_calls, 2, "must retry the permalink once before giving up")
        self.logger.warning.assert_called_once()
        args = self.logger.warning.call_args[0]
        self.assertIn(permalink, args)
        self.assertTrue(any("never appeared" in str(a) for a in args))

    def test_video_detection_failure_is_logged_not_swallowed(self):
        page = _FakePermalinkPage(appears_at_ms=0, video_raises=True)
        posted_at, is_video = ig._scrape_permalink(page, "https://www.instagram.com/x/p/abc/")
        self.assertEqual(is_video, 0)
        self.logger.warning.assert_called_once()
        self.assertIn("video-element read failed", self.logger.warning.call_args[0][0])

    def test_navigation_failure_raises_instead_of_being_silently_skipped(self):
        page = _FakePermalinkPage(appears_at_ms=0, goto_fails=True)
        with self.assertRaises(ig._PermalinkNavigationError):
            ig._scrape_permalink(page, "https://www.instagram.com/x/p/abc/")


if __name__ == "__main__":
    unittest.main()
