"""``PlatformSession.goto_with_login_check`` retries a stalled page load once.

A live planning run lost the whole Instagram and Twitter platforms to a single
45 s ``Page.goto`` timeout each, although both sites loaded fine minutes later.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.test_session_goto_retry -v
"""

from __future__ import annotations

import logging
import unittest

from playwright.sync_api import TimeoutError as PWTimeoutError

from planning._session_base import (
    LoginRequiredError,
    NavigationTimeoutError,
    PlatformSession,
)

URL = "https://example.test/home"


class _FakePage:
    """``goto`` raises the queued outcomes in order; ``url`` is what the last good goto landed on."""

    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.goto_calls: list[tuple[str, int]] = []
        self.url = ""

    def goto(self, url: str, *, timeout: int, wait_until: str) -> None:
        self.goto_calls.append((url, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        self.url = outcome


class _Session(PlatformSession):
    platform_name = "fake"
    login_markers = ("/login",)
    session_display = "Fake"


def _session(outcomes: list) -> tuple[_Session, _FakePage]:
    # Skip __init__: it resolves a Chrome profile dir; only the page is under test.
    session = _Session.__new__(_Session)
    session.logger = logging.getLogger("fake_session")
    session._page = _FakePage(outcomes)
    return session, session._page


class GotoRetryTests(unittest.TestCase):
    def test_first_timeout_is_retried_and_succeeds(self) -> None:
        session, page = _session([PWTimeoutError("Timeout 45000ms exceeded"), URL])
        with self.assertLogs("fake_session", level="INFO") as logs:
            session.goto_with_login_check(URL)
        self.assertEqual(len(page.goto_calls), 2)
        self.assertTrue(any(URL in line and "1/2" in line for line in logs.output))

    def test_second_timeout_raises_distinct_error(self) -> None:
        session, page = _session([PWTimeoutError("t1"), PWTimeoutError("t2")])
        with self.assertRaises(NavigationTimeoutError) as ctx:
            session.goto_with_login_check(URL)
        self.assertEqual(len(page.goto_calls), 2)
        self.assertIn(URL, str(ctx.exception))
        self.assertIn("2 attempts", str(ctx.exception))
        # Existing row handlers catch RuntimeError / the Playwright timeout.
        self.assertIsInstance(ctx.exception, RuntimeError)
        self.assertIsInstance(ctx.exception, PWTimeoutError)

    def test_no_retry_when_first_load_succeeds(self) -> None:
        session, page = _session([URL])
        session.goto_with_login_check(URL)
        self.assertEqual(len(page.goto_calls), 1)

    def test_login_redirect_is_not_retried(self) -> None:
        session, page = _session(["https://example.test/login"])
        with self.assertRaises(LoginRequiredError):
            session.goto_with_login_check(URL)
        self.assertEqual(len(page.goto_calls), 1)


if __name__ == "__main__":
    unittest.main()
