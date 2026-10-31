"""check_ip search run: a failed search neither starts the re-search window nor exits green.

``_search_image`` used to stamp ``last_processed_date`` before the Lens searches ran, so an
image whose every search failed (HTTP error, timeout, SerpAPI quota) sat inside its window
and was not retried, and ``main`` returned 0 regardless.

See ``_store_fixtures`` for the guarantees the store tests pin.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from check_ip import run  # noqa: E402
from tests.ip_checks._store_fixtures import StoreTestCase  # noqa: E402

IMAGE = Path("books pile.png")
URL = "https://i.example.test/books.png"


class _Lens:
    """``search`` answers from ``replies`` in order; ``None`` is a failed search."""

    def __init__(self, replies):
        self.replies = list(replies)

    def search(self, conn, image_url, filename, search_type):
        return self.replies.pop(0)

    def validate(self):
        return True


class _Imgur:
    def __init__(self):
        self.uploads = 0

    def upload(self, path):
        self.uploads += 1
        return URL

    def validate(self):
        return True


def _image_row(conn):
    return conn.execute("select imgur_url, last_processed_date from images").fetchone()


class SearchImageTests(StoreTestCase):
    def _search(self, replies, search_types=("exact_matches",)):
        tally: Counter = Counter()
        imgur = _Imgur()
        with mock.patch.object(run.process, "build_rows", return_value=([], 0, 0)):
            run._search_image(self.conn, _Lens(replies), imgur, IMAGE, list(search_types), tally)
        return tally, imgur

    def test_all_searches_failed_leaves_the_image_due_but_keeps_its_hosted_url(self):
        tally, _ = self._search([None])
        row = _image_row(self.conn)
        self.assertEqual(row["imgur_url"], URL)
        self.assertIsNone(row["last_processed_date"])
        self.assertEqual(tally["failed"], 1)

    def test_a_successful_search_starts_the_window(self):
        tally, _ = self._search([{}])
        self.assertIsNotNone(_image_row(self.conn)["last_processed_date"])
        self.assertEqual((tally["searched"], tally["failed"]), (1, 0))

    def test_one_success_among_failures_starts_the_window(self):
        tally, _ = self._search([None, {}], search_types=("exact_matches", "visual_matches"))
        self.assertIsNotNone(_image_row(self.conn)["last_processed_date"])
        self.assertEqual((tally["searched"], tally["failed"]), (1, 1))

    def test_a_failed_re_search_keeps_the_previous_window(self):
        self.conn.execute("insert into images (filename, imgur_url, last_processed_date) values (?, ?, ?)",
                          (IMAGE.name, URL, "2026-01-01 00:00:00"))
        self.conn.commit()
        self._search([None])
        self.assertEqual(_image_row(self.conn)["last_processed_date"], "2026-01-01 00:00:00")


class MainExitCodeTests(StoreTestCase):
    def _main(self, replies):
        lens = _Lens(replies)
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(run.db, "config", return_value={"images_folder": folder}), \
                mock.patch.object(run.db, "connect", return_value=self.conn), \
                mock.patch.object(run.db, "credentials",
                                  return_value={"serpapi_key": "k", "imgur_client_id": "i", "imgur_access_token": "t"}), \
                mock.patch.object(run.db, "mark_duplicates", return_value={}), \
                mock.patch.object(run.db, "refresh_image_counts"), \
                mock.patch.object(run.db, "refresh_poster_keys"), \
                mock.patch.object(run.process, "collect_image_paths", return_value=[IMAGE]), \
                mock.patch.object(run.process, "build_rows", return_value=([], 0, 0)), \
                mock.patch.object(run, "_enabled_search_types", return_value=["exact_matches"]), \
                mock.patch.object(run, "LensClient", return_value=lens), \
                mock.patch.object(run, "ImgurClient", return_value=_Imgur()), \
                mock.patch.object(run.sys, "stdout"), mock.patch.object(run.sys, "stderr"), \
                mock.patch.object(run.logging, "basicConfig"):
            return run.main([])

    def test_a_failed_search_makes_the_run_exit_non_zero(self):
        self.assertEqual(self._main([None]), 1)

    def test_a_clean_run_exits_zero(self):
        self.assertEqual(self._main([{}]), 0)


if __name__ == "__main__":
    unittest.main()
