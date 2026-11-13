"""The engagement comment status: the declared states, the table that reads them and the refused writes.

``tools/engagement/db/comment_status.py`` is the only module that names ``pending`` / ``approved`` /
``sent`` / ``rejected`` / ``ignored``; ``update_comment_status`` writes through it. No network:
the Supabase client is a mock.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.engagement.db import client  # noqa: E402
from tools.engagement.db.comment_status import (  # noqa: E402
    STORED_STATUSES,
    CommentStatus,
    derive_comment_status,
    writable_status,
)

DERIVE_TABLE = (
    ("pending", CommentStatus.PENDING),
    ("approved", CommentStatus.APPROVED),
    ("sent", CommentStatus.SENT),
    ("rejected", CommentStatus.REJECTED),
    ("ignored", CommentStatus.IGNORED),
    (" Approved ", CommentStatus.APPROVED),
    (CommentStatus.SENT, CommentStatus.SENT),
    ("read", CommentStatus.UNKNOWN),
    ("", CommentStatus.UNKNOWN),
    (None, CommentStatus.UNKNOWN),
    (3, CommentStatus.UNKNOWN),
)


class DeriveTableTests(unittest.TestCase):
    def test_every_stored_value(self) -> None:
        for stored, expected in DERIVE_TABLE:
            with self.subTest(stored=stored):
                self.assertIs(derive_comment_status(stored), expected)

    def test_stored_values_are_the_schema_values(self) -> None:
        schema = (REPO_ROOT / "tools" / "engagement" / "db" / "schema.sql").read_text(encoding="utf-8")
        line = next(ln for ln in schema.splitlines() if ln.strip().startswith("status "))
        documented = {w.strip() for w in line.split("--", 1)[1].split("/")}
        self.assertEqual({s.value for s in STORED_STATUSES}, documented)


class UpdateCommentStatusTests(unittest.TestCase):
    def test_a_known_status_is_written_as_its_string(self) -> None:
        sb = mock.MagicMock()
        with mock.patch.object(client, "supabase_client", return_value=sb):
            client.update_comment_status("linkedin", "c1", CommentStatus.APPROVED)
        payload = sb.table.return_value.update.call_args.args[0]
        self.assertEqual((payload["status"], type(payload["status"])), ("approved", str))

    # state-machine-proof: engagement-comment-status
    def test_a_status_outside_the_set_is_never_written(self) -> None:
        """The illegal transition: moving a comment to a value the review never names, or to the read-only unknown."""
        for target in ("read", "", "unknown", None, CommentStatus.UNKNOWN):
            with self.subTest(target=target):
                sb = mock.MagicMock()
                with mock.patch.object(client, "supabase_client", return_value=sb):
                    with self.assertRaises(ValueError):
                        client.update_comment_status("linkedin", "c1", target)  # type: ignore[arg-type]
                    with self.assertRaises(ValueError):
                        writable_status(target)
                sb.table.assert_not_called()


if __name__ == "__main__":
    unittest.main()
