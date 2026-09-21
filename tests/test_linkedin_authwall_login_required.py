"""A late ``/authwall`` redirect is reported as login-required (issue #310).

On 2026-09-21 the LinkedIn profile session expired. LinkedIn sends logged-out
visitors of ``/in/<handle>/…`` to ``/authwall`` client-side, *after*
``domcontentloaded`` — past ``goto_with_login_check``'s single URL check, and
``/authwall`` was not a login marker anyway. The reporting scraper therefore
failed with "Could not parse follower count" and a ``wait_for_selector``
timeout instead of telling the owner to log in again.

The fake page models that late redirect: ``goto`` lands on the profile URL,
and the URL flips to the authwall only when the scraper's wait fails.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from unittest import mock

from planning._session_base import LoginRequiredError
from planning.linkedin.linkedin_session import LinkedInSession
from reporting.scrape_client import linkedin as li
from reporting.scrape_client.base import ScrapeError

HANDLE_URL = "https://www.linkedin.com/in/someone"
AUTHWALL_URL = "https://www.linkedin.com/authwall?trk=bf&original_referer="


class _Locator:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    def wait_for(self, *, state: str, timeout: float) -> None:
        self._page.fail_wait()

    def inner_text(self, timeout: float) -> str:
        return ""

    def count(self) -> int:
        return 0


class _FakePage:
    """A page whose wait fails, optionally after a late client-side redirect."""

    def __init__(self, *, redirect_to_authwall: bool) -> None:
        self.url = "about:blank"
        self._redirect = redirect_to_authwall

    def goto(self, url: str, **_kwargs) -> None:
        self.url = url

    def fail_wait(self) -> None:
        if self._redirect:
            self.url = AUTHWALL_URL
        raise TimeoutError("Timeout 20000ms exceeded.")

    def locator(self, _selector: str) -> _Locator:
        return _Locator(self)

    def wait_for_selector(self, _selector: str, *, timeout: float) -> None:
        self.fail_wait()


def _session_factory(page: _FakePage):
    class _FakeSession(LinkedInSession):
        def __init__(self, _cfg: dict) -> None:
            self._page = page
            self.logger = mock.Mock()

        def __enter__(self) -> "_FakeSession":
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def screenshot_failure(self, _label: str) -> None:
            return None

    return _FakeSession


class AuthwallMarkerTest(unittest.TestCase):
    def test_authwall_url_is_a_login_page(self) -> None:
        session = _session_factory(_FakePage(redirect_to_authwall=False))({})
        session.page.url = AUTHWALL_URL
        with self.assertRaises(LoginRequiredError):
            session.raise_if_login_page()

    def test_profile_url_is_not_a_login_page(self) -> None:
        session = _session_factory(_FakePage(redirect_to_authwall=False))({})
        session.page.url = HANDLE_URL + "/recent-activity/all/"
        session.raise_if_login_page()


class LateRedirectTest(unittest.TestCase):
    def _run(self, fetch, *, redirect: bool) -> ScrapeError:
        page = _FakePage(redirect_to_authwall=redirect)
        with mock.patch.object(li, "LinkedInSession", _session_factory(page)), \
                mock.patch.object(li, "load_linkedin_config", return_value={}), \
                mock.patch.object(li, "_get_handle_url", return_value=HANDLE_URL):
            with self.assertRaises(ScrapeError) as ctx:
                fetch("2026-09-21")
        return ctx.exception

    def test_fetch_profile_reports_login_required(self) -> None:
        err = self._run(li.fetch_profile, redirect=True)
        self.assertIn("login required", str(err))
        self.assertIn("bootstrap_session", str(err))

    def test_fetch_posts_reports_login_required(self) -> None:
        err = self._run(li.fetch_posts, redirect=True)
        self.assertIn("login required", str(err))

    def test_real_page_failure_keeps_its_own_message(self) -> None:
        err = self._run(li.fetch_posts, redirect=False)
        self.assertNotIn("login required", str(err))
        self.assertIn("No activity posts appeared", str(err))


if __name__ == "__main__":
    unittest.main()
