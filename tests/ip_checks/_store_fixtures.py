"""Shared fixtures for the check_ip store, queue and review tests (issue #286).

The load-bearing guarantee these tests pin is the one in the middle: the screening pass
proposes, it never decides. ``check_ip.screen.record_verdict`` must not be able
to touch ``ok`` / ``person`` / ``chat`` / ``report`` / ``fixed`` — those are ten
months of manual triage and the skill runs unattended against them.

The second one, added with issue #295, is that a licence condition nobody
assessed stays ``NULL`` and is never counted, sorted or rendered as if it had
passed. One ``coalesce(col, 1)`` would undo the whole model silently, so the
tests assert it in Python, in SQL, in the queue stats and in the tab's frame.

The third, added with issue #305, is that the two halves of that NULL are kept
apart: "a worker looked and could not establish this" (``2``) and "nobody ever
looked" (``NULL``) were one value, so ``screen next --unassessed`` re-served
rows that had just been judged and two live batches went to the identical ten
posts. Screening a row must remove it from that queue, and the new value must
not become a back door into the passing state — both are asserted in this
folder's ``test_store_*.py`` modules.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db  # noqa: E402


def _seed(conn, rows):
    """Insert result rows, returning their ids in order."""
    ids = []
    for row in rows:
        cur = conn.execute(
            """
            insert into results
                (local_image, uploaded_url, found_link, title, duplicate, match_type,
                 source, post_date, search_date, "order", ok, person, chat, report, fixed,
                 poster_key)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("local_image", "img.png"), row.get("uploaded_url", "https://i/x.png"),
                row["found_link"], row.get("title", ""), row.get("duplicate", 0),
                row.get("match_type", "Exact Match"), row.get("source", "LinkedIn"),
                row.get("post_date"), row.get("search_date", "2026-01-01 00:00:00"),
                row.get("order", 1), row.get("ok"), row.get("person"), row.get("chat"),
                row.get("report"), row.get("fixed"),
                # Derived here exactly as upsert_results derives it in production,
                # so the queue's ranking is exercised the way it really runs.
                db.poster_key_for(row["found_link"]),
            ),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def _retire_similar(conn):
    """Put every Similar Match row in the retired state the removed lane used to set."""
    conn.execute("update results set retired = 1 where match_type = 'Similar Match'")
    conn.commit()


class StoreTestCase(unittest.TestCase):
    """A fresh check_ip store per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = db.connect(Path(self._tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()
