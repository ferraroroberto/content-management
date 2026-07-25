"""Regression test for the LinkedIn comment scraper's silent-outage bug (issue #175).

Every failure inside ``scrape_post_comments`` was downgraded to a ``WARNING``
and an empty return, and ``main()`` had no exit-status logic at all — a run
that visited N posts and extracted 0 comments from all of them was
indistinguishable, to the scheduler, from a run that worked. A LinkedIn DOM
change broke extraction for six days; 18 consecutive scheduled runs all
reported ``"status": "success"``, ``"exit_code": 0``.

``_evaluate_scrape_health()`` is the extracted decision function ``run()``
now calls to decide the aggregate outcome. It is deliberately pure (no
Playwright/Notion/Supabase dependency) so this scenario is testable without a
live LinkedIn session — the fix is the decision logic, not the DOM walk.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from typing import Optional
from unittest import mock

import engagement.linkedin.scrape_comments as scrape_comments
from engagement.linkedin.scrape_comments import _evaluate_scrape_health


class ScrapeHealthTests(unittest.TestCase):
    def test_broken_when_every_post_hits_a_structural_error(self):
        """Replays the 2026-07-15 -> 07-20 outage: every visited post's
        extractor reported `no_list_container`. Must be reported broken so
        the run fails on post #1 of the outage, not the 18th."""
        errors = ["no_list_container"] * 18
        self.assertEqual(_evaluate_scrape_health(18, errors), "broken")

    def test_ok_when_posts_have_zero_comments_but_no_structural_error(self):
        """A quiet day — posts were visited, the comment list container was
        found, there just aren't any comments yet. Must NOT be flagged
        broken: comment count alone is not the discriminator."""
        self.assertEqual(_evaluate_scrape_health(5, []), "ok")

    def test_ok_when_only_some_posts_structurally_fail(self):
        """Partial failure (1 of 3 posts errored) is not the full-outage
        signal this check exists to catch."""
        self.assertEqual(_evaluate_scrape_health(3, ["no_list_container"]), "ok")

    def test_ok_when_no_posts_were_attempted(self):
        """Nothing to scrape (e.g. an empty lookback window) is not an
        outage — there's nothing for the extractor to have failed on."""
        self.assertEqual(_evaluate_scrape_health(0, []), "ok")

    def test_broken_counts_exceptions_as_structural(self):
        """A per-post exception (network error, crash mid-scrape) is as much
        a structural failure as an extractor-reported DOM miss — both mean
        the post never produced a real result."""
        errors = ["exception:TimeoutError", "no_list_container"]
        self.assertEqual(_evaluate_scrape_health(2, errors), "broken")


class MainExitCodeTests(unittest.TestCase):
    """`main()` is what the scheduler actually observes — the health check
    only matters if it reaches `sys.exit`. `run()` is mocked so this exercises
    just the wiring, with no live LinkedIn/Notion/Supabase dependency."""

    def _run_main(self, run_result: dict) -> Optional[int]:
        with mock.patch.object(scrape_comments, "run", return_value=run_result), \
             mock.patch.object(scrape_comments, "load_config", return_value={"engagement": {}}), \
             mock.patch.object(scrape_comments, "setup_logger"), \
             mock.patch.object(scrape_comments, "force_utf8_stdio"), \
             mock.patch("sys.argv", ["scrape_comments.py", "--dry-run"]):
            try:
                scrape_comments.main()
            except SystemExit as exc:
                return exc.code
            return None

    def test_main_exits_nonzero_on_broken_status(self):
        code = self._run_main({"posts": 3, "comments": 0, "commenters": 0, "structural_failures": 3, "status": "broken"})
        self.assertEqual(code, 1)

    def test_main_does_not_exit_on_ok_status(self):
        code = self._run_main({"posts": 3, "comments": 0, "commenters": 0, "structural_failures": 0, "status": "ok"})
        self.assertIsNone(code)


if __name__ == "__main__":
    unittest.main()
