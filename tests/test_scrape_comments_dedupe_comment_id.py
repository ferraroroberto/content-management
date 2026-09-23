"""Regression test for the duplicate `comment_id` upsert crash (issue #313).

``_extract_comment_id`` derives a comment's id from the LinkedIn URN found on
its DOM wrapper (`replaceableComment_...`). A reply that doesn't carry its
own wrapper inherits its parent comment's URN instead, so two distinct
comments can land in the same scrape batch with the same `comment_id`.
Supabase's ``upsert(rows, on_conflict="platform,comment_id")`` is a single
Postgres statement — it rejects the whole batch (``ON CONFLICT DO UPDATE
command cannot affect row a second time``) when two rows in it target the
same conflict key, so one colliding pair took down persistence for an entire
run's worth of comments (2026-09-23 06:15 scheduled run, exit 1).

``_dedupe_comments_by_id()`` is the extracted fix ``run()`` now calls right
before the upsert. It is deliberately pure (plain dicts in, plain dicts out)
so this scenario is testable without a live LinkedIn/Supabase dependency —
the fix is the dedup logic, not the DOM walk.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest

from engagement.linkedin.scrape_comments import _dedupe_comments_by_id


class DedupeCommentsByIdTests(unittest.TestCase):
    def test_collapses_rows_sharing_a_comment_id(self):
        """Replays the 2026-09-23 failure: a reply inherited its parent's
        URN, so both rows carried the same comment_id. Without this fix,
        both reach the upsert and Postgres rejects the whole batch."""
        rows = [
            {"comment_id": "urn:li:comment:(x,1)", "post_url": "p", "commenter_url": "a", "my_reply_text": None},
            {"comment_id": "urn:li:comment:(x,1)", "post_url": "p", "commenter_url": "b", "my_reply_text": None},
        ]
        result = _dedupe_comments_by_id(rows)
        self.assertEqual(len(result), 1)

    def test_no_collision_leaves_every_row_intact(self):
        """The ordinary case — every comment_id unique — must be a no-op."""
        rows = [
            {"comment_id": "urn:li:comment:(x,1)", "post_url": "p", "commenter_url": "a", "my_reply_text": None},
            {"comment_id": "urn:li:comment:(x,2)", "post_url": "p", "commenter_url": "b", "my_reply_text": None},
        ]
        result = _dedupe_comments_by_id(rows)
        self.assertEqual(len(result), 2)
        self.assertEqual({r["comment_id"] for r in result}, {"urn:li:comment:(x,1)", "urn:li:comment:(x,2)"})

    def test_prefers_the_row_carrying_a_reply(self):
        """When one of the colliding rows has `my_reply_text` set, keep that
        one — it carries more information than the other."""
        rows = [
            {"comment_id": "urn:li:comment:(x,1)", "post_url": "p", "commenter_url": "a", "my_reply_text": None},
            {"comment_id": "urn:li:comment:(x,1)", "post_url": "p", "commenter_url": "b", "my_reply_text": "thanks!"},
        ]
        result = _dedupe_comments_by_id(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["my_reply_text"], "thanks!")

    def test_empty_input_returns_empty_output(self):
        self.assertEqual(_dedupe_comments_by_id([]), [])


if __name__ == "__main__":
    unittest.main()
