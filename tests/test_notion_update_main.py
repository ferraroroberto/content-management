r"""Characterization tests for ``reporting.notion.notion_update.main`` (issue #409).

``main`` looks up the previous-day and current-day Notion rows, maps Supabase
values onto the configured fields, builds one update payload + change log per
row (posts on the previous day, followers on the current day, plus the
``next`` relation), applies them and logs the changes to Supabase. These pin
that flow so splitting it into lookup / build / apply keeps every payload,
change record and return value unchanged.

No Notion, no Supabase: every I/O boundary is a mock; the field extraction,
mapping and payload helpers are the real ones.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import contextlib
import unittest
from types import SimpleNamespace
from unittest import mock

from reporting.notion import notion_update as nu

POSTS_FIELDS = ["Impressions", "Likes", "Skipped"]
FOLLOWER_FIELDS = ["Followers"]
POSTS_MAP = {"Impressions": "posts.impressions", "Likes": "posts.likes", "Skipped": None}
FOLLOWER_MAP = {"Followers": "profile.followers"}


def _row(page_id: str, props: dict, next_id: str | None = None) -> dict:
    properties = dict(props)
    properties["next"] = {"type": "relation", "relation": [{"id": next_id}] if next_id else []}
    return {"id": page_id, "properties": properties}


def _num(value):
    return {"type": "number", "number": value}


class MainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = mock.Mock(name="supabase")
        self.notion = mock.Mock(name="notion")
        self.previous = _row("prev-1", {"Impressions": _num(10), "Likes": _num(1), "Skipped": _num(5)})
        self.current = _row("cur-1", {"Followers": _num(100)}, next_id="old-next")
        self.next_row = {"id": "tomorrow-1"}
        self.update_result = True
        self.update_calls: list[tuple] = []
        self.logged: list[tuple] = []
        self.args = SimpleNamespace(date="2026-01-05", debug=False, database_id=None, yes=True)
        self.config = ("tok", [{"id": "db-1", "name": "main"}], FOLLOWER_FIELDS, POSTS_FIELDS,
                       FOLLOWER_MAP, POSTS_MAP, "posts", "profile")
        self.posts_data = {"impressions": 25, "likes": 1}
        self.profile_data = {"followers": 130}

    def _update(self, notion, page_id, updates):
        self.update_calls.append((page_id, updates))
        if isinstance(self.update_result, dict):
            return self.update_result[page_id]
        return self.update_result

    def _run(self) -> bool:
        patches = {
            "parse_arguments": lambda: self.args,
            "configure_logger": lambda debug: None,
            "load_notion_config": lambda: self.config,
            "init_notion_client": lambda token: self.notion,
            "search_by_date": lambda *a: self.previous,
            "search_by_current_date": lambda *a: self.current,
            "get_db_connection": lambda: self.conn,
            "get_supabase_data": lambda *a: (self.posts_data, self.profile_data),
            "find_row_for_date": lambda *a: self.next_row,
            "update_notion_page": self._update,
            "create_tracking_table": lambda conn: self.logged.append(("tracking",)),
            "log_field_changes": lambda conn, page_id, changes: self.logged.append((page_id, changes)),
        }
        with contextlib.ExitStack() as stack:
            for name, value in patches.items():
                stack.enter_context(mock.patch.object(nu, name, value))
            return nu.main()

    def test_builds_and_applies_posts_followers_and_next_updates(self) -> None:
        self.assertTrue(self._run())
        self.assertEqual(self.update_calls, [
            ("prev-1", {"Impressions": {"number": 25.0}, "Likes": {"number": 1.0}}),
            ("cur-1", {"Followers": {"number": 130.0}, "next": {"relation": [{"id": "tomorrow-1"}]}}),
        ])
        self.assertEqual(self.logged, [
            ("tracking",),
            ("prev-1", [{"field": "Impressions", "initial": 10, "final": 25},
                        {"field": "Likes", "initial": 1, "final": 1}]),
            ("tracking",),
            ("cur-1", [{"field": "Followers", "initial": 100, "final": 130},
                       {"field": "next", "initial": "old-next", "final": "tomorrow-1"}]),
        ])
        self.conn.close.assert_called_once_with()

    def test_unmapped_none_and_missing_values_are_not_sent(self) -> None:
        self.posts_data = {"impressions": None, "likes": 3}
        self.profile_data = None
        self.current = _row("cur-1", {"Followers": _num(100)}, next_id="tomorrow-1")
        self.assertTrue(self._run())
        # Impressions maps to None, "Skipped" has no mapping value, followers has no profile row,
        # and `next` already points at tomorrow: only Likes is written.
        self.assertEqual(self.update_calls, [("prev-1", {"Likes": {"number": 3.0}})])
        self.assertEqual(self.logged[-1], ("prev-1", [{"field": "Likes", "initial": 1, "final": 3}]))

    def test_field_without_a_mapping_entry_is_skipped(self) -> None:
        self.config = self.config[:5] + ({"Impressions": "posts.impressions"},) + self.config[6:]
        self.assertTrue(self._run())
        self.assertEqual(self.update_calls[0], ("prev-1", {"Impressions": {"number": 25.0}}))

    def test_missing_next_day_row_skips_the_relation(self) -> None:
        self.next_row = None
        self.assertTrue(self._run())
        self.assertEqual(self.update_calls[1], ("cur-1", {"Followers": {"number": 130.0}}))

    def test_nothing_to_update_returns_true_without_writing(self) -> None:
        self.posts_data = {"impressions": None, "likes": None}
        self.profile_data = None
        self.next_row = None
        self.assertTrue(self._run())
        self.assertEqual((self.update_calls, self.logged), ([], []))
        self.conn.close.assert_called_once_with()

    def test_a_failed_posts_update_returns_false_but_still_updates_followers(self) -> None:
        self.update_result = {"prev-1": False, "cur-1": True}
        self.assertFalse(self._run())
        self.assertEqual([c[0] for c in self.update_calls], ["prev-1", "cur-1"])
        self.assertEqual([entry[0] for entry in self.logged], ["tracking", "cur-1"])

    def test_early_bail_outs_return_false(self) -> None:
        self.config = (None,) + self.config[1:]
        self.assertFalse(self._run())
        self.assertEqual(self.update_calls, [])
        self.config = ("tok",) + self.config[1:]
        self.previous = None
        self.assertFalse(self._run())
        self.previous = _row("prev-1", {})
        self.current = None
        self.assertFalse(self._run())
        self.current = _row("cur-1", {})
        self.conn = None
        self.assertFalse(self._run())


if __name__ == "__main__":
    unittest.main()
