r"""The Notion HTTP calls through ``pooled_request`` pass an explicit timeout.

A stalled connection used to block the run forever; each call now carries
``NOTION_REQUEST_TIMEOUT`` and a ``requests`` timeout is logged as its own
failure (distinct from other HTTP errors) before the caller gets ``None``.

No network: ``pooled_request`` is mocked at the module boundary.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from unittest import mock

import requests

from tools.reporting.notion import notion_database_list as ndl
from tools.reporting.notion import notion_supabase_sync as nss
from tools.reporting.notion._client import NOTION_REQUEST_TIMEOUT


def _lister() -> ndl.NotionDatabaseLister:
    lister = object.__new__(ndl.NotionDatabaseLister)
    lister.headers = {}
    return lister


def _syncer() -> nss.NotionSupabaseSync:
    syncer = object.__new__(nss.NotionSupabaseSync)
    syncer.headers = {}
    return syncer


class DatabaseListTimeoutTests(unittest.TestCase):
    def test_search_passes_timeout(self):
        with mock.patch.object(ndl, "pooled_request") as post:
            post.return_value.json.return_value = {"results": []}
            _lister()._search_databases()
        self.assertEqual(post.call_args.kwargs["timeout"], NOTION_REQUEST_TIMEOUT)

    def test_search_timeout_is_logged_distinctly_and_returns_none(self):
        with mock.patch.object(ndl, "pooled_request", side_effect=requests.exceptions.ReadTimeout("slow")):
            with self.assertLogs("notion_database_list", level="ERROR") as logs:
                result = _lister()._search_databases()
        self.assertIsNone(result)
        self.assertIn("timed out", logs.output[0])


class SupabaseSyncTimeoutTests(unittest.TestCase):
    def test_get_and_post_pass_timeout(self):
        with mock.patch.object(nss.time, "sleep"), \
                mock.patch.object(nss, "pooled_request") as request:
            syncer = _syncer()
            syncer._notion_api_call("databases/abc")
            syncer._notion_api_call("databases/abc/query", method="POST", data={})
        self.assertEqual([c.args[0] for c in request.call_args_list], ["GET", "POST"])
        for call in request.call_args_list:
            self.assertEqual(call.kwargs["timeout"], NOTION_REQUEST_TIMEOUT)

    def test_timeout_is_logged_distinctly_and_returns_none(self):
        with mock.patch.object(nss.time, "sleep"), \
                mock.patch.object(nss, "pooled_request", side_effect=requests.exceptions.ConnectTimeout("slow")):
            with self.assertLogs("notion_supabase_sync", level="ERROR") as logs:
                result = _syncer()._notion_api_call("databases/abc")
        self.assertIsNone(result)
        self.assertIn("timed out", logs.output[0])


if __name__ == "__main__":
    unittest.main()
