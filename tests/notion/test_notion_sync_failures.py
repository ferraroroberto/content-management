r"""A failed Notion read is a failed sync, not a synced database.

``sync_database`` used to ``break`` out of the page loop on a failed query, so a first-page
failure read as "no new pages", a later-page failure upserted the partial fetch, and
``run_sync_cycle`` still counted the database as synced.

No network, no database: every Notion and Supabase call is a fake.

Run: & .\.venv\Scripts\python.exe -m pytest tests/notion/test_notion_sync_failures.py -q
"""

from __future__ import annotations

import unittest
from unittest import mock

from reporting.notion import notion_supabase_sync as nss

DATABASE = {"id": "db1", "name": "Posts", "supabase_table": "posts"}
PAGE = {"id": "p1"}


def _syncer(query_results, schema=None) -> nss.NotionSupabaseSync:
    syncer = object.__new__(nss.NotionSupabaseSync)
    syncer.databases = [DATABASE]
    syncer.environment = "local"
    syncer._get_last_sync_time = mock.Mock(return_value=None)
    syncer._get_database_schema = mock.Mock(return_value={"title": "title"} if schema is None else schema)
    syncer._query_database = mock.Mock(side_effect=query_results)
    syncer._transform_page_to_row = mock.Mock(side_effect=lambda page, _schema: {"id": page["id"]})
    syncer._create_or_alter_table = mock.Mock()
    syncer._upsert_rows = mock.Mock(return_value=1)
    return syncer


class SyncDatabaseTests(unittest.TestCase):
    def test_first_page_failure_is_a_failed_sync(self):
        syncer = _syncer([None])
        self.assertFalse(syncer.sync_database(DATABASE, mock.Mock()))
        syncer._upsert_rows.assert_not_called()

    def test_later_page_failure_never_upserts_the_partial_fetch(self):
        syncer = _syncer([{"results": [PAGE], "has_more": True, "next_cursor": "c"}, None])
        self.assertFalse(syncer.sync_database(DATABASE, mock.Mock()))
        syncer._upsert_rows.assert_not_called()

    def test_schema_failure_is_a_failed_sync(self):
        syncer = _syncer([], schema={})
        syncer._get_database_schema.return_value = None
        self.assertFalse(syncer.sync_database(DATABASE, mock.Mock()))
        syncer._query_database.assert_not_called()

    def test_empty_but_successful_read_is_a_success(self):
        self.assertTrue(_syncer([{"results": [], "has_more": False}]).sync_database(DATABASE, mock.Mock()))

    def test_full_read_upserts_and_succeeds(self):
        syncer = _syncer([{"results": [PAGE], "has_more": False}])
        self.assertTrue(syncer.sync_database(DATABASE, mock.Mock()))
        syncer._upsert_rows.assert_called_once()


class RunSyncCycleTests(unittest.TestCase):
    def _cycle(self, syncer):
        connection = mock.Mock()
        with mock.patch.object(nss, "load_db_config"), \
                mock.patch.object(nss, "get_db_connection", return_value=connection):
            return syncer.run_sync_cycle()

    def test_a_failed_database_is_not_counted_as_synced(self):
        with self.assertLogs("notion_supabase_sync", level="INFO") as logs:
            self.assertFalse(self._cycle(_syncer([None])))
        self.assertTrue(any("0/1 databases synced" in line and "ERROR" in line for line in logs.output))

    def test_every_database_synced_reports_success(self):
        self.assertTrue(self._cycle(_syncer([{"results": [PAGE], "has_more": False}])))


if __name__ == "__main__":
    unittest.main()
