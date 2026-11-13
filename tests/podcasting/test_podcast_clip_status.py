"""The podcast clip review status: the declared states, the table that reads them and the refused moves.

``tools/podcast/review_state.py`` is the only module that names ``pending`` / ``approved`` / ``changes`` /
``dropped`` and moves a clip between them. A temp episode folder stands in for ``review.json``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.podcast import review_state  # noqa: E402
from tools.podcast.review_state import ClipStatus, derive_clip_status  # noqa: E402

DERIVE_TABLE = (
    ("pending", ClipStatus.PENDING),
    ("approved", ClipStatus.APPROVED),
    ("changes", ClipStatus.CHANGES),
    ("dropped", ClipStatus.DROPPED),
    (ClipStatus.DROPPED, ClipStatus.DROPPED),
    (None, ClipStatus.PENDING),
)


class DeriveTableTests(unittest.TestCase):
    def test_every_stored_value(self) -> None:
        for stored, expected in DERIVE_TABLE:
            with self.subTest(stored=stored):
                self.assertIs(derive_clip_status(stored), expected)

    def test_a_status_outside_the_set_is_refused_not_counted_as_pending(self) -> None:
        for stored in ("approve", "", "Approved", 3):
            with self.subTest(stored=stored):
                with self.assertRaises(ValueError):
                    derive_clip_status(stored)
        with self.assertRaises(ValueError):
            review_state.counts([{"number": 1}], {"clips": {"1": {"status": "bogus"}}})

    def test_counts_cover_every_state_and_default_to_pending(self) -> None:
        clips = [{"number": n} for n in (1, 2, 3, 4, 5)]
        state = {"clips": {"1": {"status": "approved"}, "2": {"status": "dropped"}, "3": {"status": "changes"}}}
        self.assertEqual(review_state.counts(clips, state),
                         {ClipStatus.PENDING: 2, ClipStatus.APPROVED: 1, ClipStatus.CHANGES: 1, ClipStatus.DROPPED: 1})


class MoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.ep = mock.Mock(folder=Path(self.dir.name))

    def stored(self) -> dict:
        return json.loads((Path(self.dir.name) / review_state.REVIEW_FILE).read_text(encoding="utf-8"))["clips"]

    def test_approve_drop_and_reopen(self) -> None:
        for target in (ClipStatus.APPROVED, ClipStatus.DROPPED, ClipStatus.PENDING):
            with self.subTest(target=str(target)):
                review_state.set_status(self.ep, 1, target)
                self.assertEqual(self.stored()["1"]["status"], target.value)

    # state-machine-proof: podcast-clip-status
    def test_changes_cannot_be_set_without_feedback(self) -> None:
        """The illegal transition: a clip in ``changes`` with no open round for ``revise`` to work on."""
        review_state.set_status(self.ep, 1, ClipStatus.APPROVED)
        for target in ("changes", ClipStatus.CHANGES):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    review_state.set_status(self.ep, 1, target)
        self.assertEqual(self.stored()["1"]["status"], "approved")
        review_state.request_changes(self.ep, 1, "too long")
        entry = self.stored()["1"]
        self.assertEqual(entry["status"], "changes")
        self.assertEqual(review_state.to_revise([{"number": 1}], {"clips": {"1": entry}}), [1])

    # state-machine-proof: podcast-clip-status
    def test_a_status_outside_the_set_is_never_written(self) -> None:
        """The illegal transition: moving a clip to a value the review never names."""
        review_state.set_status(self.ep, 1, ClipStatus.APPROVED)
        for target in ("approve", "", "done"):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    review_state.set_status(self.ep, 1, target)
        self.assertEqual(self.stored()["1"]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
