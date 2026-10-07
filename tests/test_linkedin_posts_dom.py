"""``fetch_posts`` finds posts in LinkedIn's server-driven activity markup (issue #377).

On 2026-10-07 the daily Reporting job lost ``linkedin_posts``: LinkedIn moved
the activity URN off a ``data-urn`` attribute on the post container. The
recent-activity feed now renders each post as ``<div role="listitem">`` and the
URN survives only inside anchor hrefs (``/analytics/post-summary/urn:li:activity:<id>/``
on every post, ``/feed/update/urn:li:activity:<id>/`` on some), so
``wait_for_selector("[data-urn^='urn:li:activity:']")`` timed out on a logged-in page.

The fixtures below are anonymised, structure-only fragments: invented ids, names
and counts, no real post text, hashed class names dropped. The tests drive the
real ``fetch_posts`` against them in headless Chrome, so a selector that does not
match the markup fails here exactly as it did in production.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.test_linkedin_posts_dom -v
"""

from __future__ import annotations

import unittest
from unittest import mock

from planning.linkedin.linkedin_session import LinkedInSession
from reporting.scrape_client import linkedin as li
from reporting.scrape_client.base import ScrapeError

HANDLE_URL = "https://www.linkedin.com/in/someone"
ID_VIDEO = "7500000000000000001"
ID_REPOST = "7500000000000000002"
ID_PLAIN = "7500000000000000003"
ID_LEGACY = "7500000000000000004"


def _sdui_post(activity_id: str, *, links: str, body: str, stats: str) -> str:
    return f"""
    <div role="listitem" componentkey="post-{activity_id}">
      <div data-display-contents="true">
        <div>
          <a href="/analytics/post-summary/urn:li:activity:{activity_id}/" aria-label="analytics">View analytics</a>
          {links}
          <p>{body}</p>
          {stats}
          <button>Like</button><button>Comment</button><button>Repost</button>
          <div>1,234 impressions</div>
        </div>
      </div>
    </div>"""


_STATS_TWO_REACTIONS = "<div>Alex Example and 41 others reacted</div><div>Alex Example and 41 others</div>"

SDUI_FEED = f"""<!doctype html><html><body>
<aside>
  <div role="list">
    <div role="listitem"><div>Someone You May Know</div><button>Connect</button></div>
  </div>
</aside>
<main>
  <h2>All activity</h2>
  <div role="list" data-testid="feed">
    {_sdui_post(
        ID_VIDEO,
        links=f'<a href="/feed/update/urn:li:activity:{ID_VIDEO}/">post</a><video></video>',
        body="A video post.",
        stats=_STATS_TWO_REACTIONS + "<div>12 comments</div><div>12 comments</div>",
    )}
    {_sdui_post(
        ID_REPOST,
        links="",
        body="A post with a single repost.",
        stats=_STATS_TWO_REACTIONS + "<div>5 comments</div><div>1 repost</div><div>1 repost</div>",
    )}
    {_sdui_post(
        ID_PLAIN,
        links="",
        body="A post with several reposts.",
        stats=_STATS_TWO_REACTIONS + "<div>1 comment</div><div>3 reposts</div><div>3 reposts</div>",
    )}
  </div>
</main></body></html>"""

LEGACY_FEED = f"""<!doctype html><html><body><main>
  <div data-urn="urn:li:activity:{ID_LEGACY}">
    <div>Alex Example and 9 others</div><div>2 comments</div>
  </div>
</main></body></html>"""

# Logged in; the activity URNs are in the page but no longer under a known post container.
UNRECOGNISED_FEED = f"""<!doctype html><html><body><main>
  <section><a href="/analytics/post-summary/urn:li:activity:{ID_PLAIN}/">View analytics</a></section>
</main></body></html>"""

# Logged in, genuinely nothing to show.
EMPTY_FEED = "<!doctype html><html><body><main><h2>All activity</h2></main></body></html>"


def _session_factory(page):
    class _FakeSession(LinkedInSession):
        def __init__(self, _cfg: dict) -> None:
            self._page = page
            self.logger = mock.Mock()

        def __enter__(self) -> "_FakeSession":
            return self

        def __exit__(self, *_exc) -> None:
            return None

        def goto_with_login_check(self, _url: str, **_kwargs) -> None:
            return None

        def raise_if_login_page(self) -> None:
            return None

        def screenshot_failure(self, _label: str) -> None:
            return None

    return _FakeSession


class LinkedInPostsDomTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
        except ImportError:
            self.skipTest("playwright not installed")
        pw = self.enterContext(sync_playwright())
        try:
            browser = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # noqa: BLE001 - no Chrome installed is a skip, not a failure
            self.skipTest(f"Chrome is not available: {exc}")
        self.addCleanup(browser.close)
        self.page = browser.new_page()
        # Keep the failure path quick: the real waits are 20 s.
        self.enterContext(mock.patch.object(li, "SCROLLS", 0))
        self.enterContext(mock.patch.object(li, "POSTS_WAIT_MS", 1500, create=True))

    def _fetch(self, html: str) -> dict:
        self.page.set_content(html)
        with mock.patch.object(li, "LinkedInSession", _session_factory(self.page)), \
                mock.patch.object(li, "load_linkedin_config", return_value={}), \
                mock.patch.object(li, "_get_handle_url", return_value=HANDLE_URL):
            return li.fetch_posts("2026-10-07")

    def test_sdui_markup_yields_every_stored_field(self) -> None:
        posts = {p["post_id"]: p for p in self._fetch(SDUI_FEED)["posts"]}
        self.assertEqual(len(posts), 3, "the sidebar listitem has no activity link and is not a post")

        video = posts[f"https://www.linkedin.com/feed/update/urn:li:activity:{ID_VIDEO}/"]
        self.assertEqual(video["is_video"], 1)
        self.assertEqual(video["num_likes"], 42)
        self.assertEqual(video["num_comments"], 12)
        self.assertEqual(video["num_reshares"], 0)

        repost = posts[f"https://www.linkedin.com/feed/update/urn:li:activity:{ID_REPOST}/"]
        self.assertEqual((repost["is_video"], repost["num_comments"], repost["num_reshares"]), (0, 5, 1))

        plain = posts[f"https://www.linkedin.com/feed/update/urn:li:activity:{ID_PLAIN}/"]
        self.assertEqual((plain["num_comments"], plain["num_reshares"]), (1, 3))

        for record in posts.values():
            self.assertEqual(
                set(record),
                {"post_id", "posted_at", "is_video", "num_likes", "num_comments", "num_reshares"},
            )
            self.assertRegex(record["posted_at"], r"^\d{4}-\d{2}-\d{2}$")

    def test_legacy_data_urn_markup_still_works(self) -> None:
        (post,) = self._fetch(LEGACY_FEED)["posts"]
        self.assertTrue(post["post_id"].endswith(f"urn:li:activity:{ID_LEGACY}/"))
        self.assertEqual((post["num_likes"], post["num_comments"]), (10, 2))

    def test_unrecognised_markup_is_reported_as_a_selector_problem(self) -> None:
        with self.assertRaises(ScrapeError) as ctx:
            self._fetch(UNRECOGNISED_FEED)
        message = str(ctx.exception)
        self.assertIn("No activity posts appeared", message)
        self.assertIn("no post container matched the selector", message)

    def test_empty_feed_is_reported_as_no_posts(self) -> None:
        with self.assertRaises(ScrapeError) as ctx:
            self._fetch(EMPTY_FEED)
        message = str(ctx.exception)
        self.assertIn("No activity posts appeared", message)
        self.assertIn("no activity URN anywhere", message)
        self.assertNotIn("no post container matched", message)


if __name__ == "__main__":
    unittest.main()
