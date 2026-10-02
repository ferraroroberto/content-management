"""The review CLI writes the same review.json the Podcast tab does (issue #350)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcast import review_cli, review_state  # noqa: E402
from podcast.episode import load_episode  # noqa: E402

CFG = {"package_dirname": "podcast package"}


class ReviewCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        (self.folder / "episode.json").write_text(json.dumps({
            "guest": "Alex Rivera", "tracks": {"host": "h.mp4", "guest": "g.mp4"}}), encoding="utf-8")
        (self.folder / "podcast package").mkdir()
        (self.folder / "podcast package" / "clips.json").write_text(json.dumps(
            [{"number": n, "title": f"clip {n}", "start": 0.0, "end": 30.0} for n in (1, 2, 3)]), encoding="utf-8")

    def _state(self) -> dict:
        return review_state.load_review(load_episode(self.folder, CFG))["clips"]

    def test_statuses_and_feedback_land_in_review_json(self) -> None:
        self.assertEqual(review_cli.main([str(self.folder), "approve", "1", "3"], cfg=CFG), 0)
        self.assertEqual(review_cli.main([str(self.folder), "feedback", "2", "start", "at", "'sleep'"], cfg=CFG), 0)
        state = self._state()
        self.assertEqual((state["1"]["status"], state["3"]["status"]), ("approved", "approved"))
        self.assertEqual(state["2"]["status"], "changes")
        self.assertEqual(state["2"]["rounds"][-1]["feedback"], "start at 'sleep'")
        ep = load_episode(self.folder, CFG)
        self.assertEqual(review_state.to_revise(json.loads((ep.package / "clips.json").read_text()),
                                                review_state.load_review(ep)), [2])
        self.assertEqual(review_cli.main([str(self.folder), "drop", "2"], cfg=CFG), 0)
        self.assertEqual(self._state()["2"]["status"], "dropped")
        self.assertEqual(review_cli.main([str(self.folder), "show"], cfg=CFG), 0)

    def test_unknown_clip_or_missing_note_is_refused_and_writes_nothing(self) -> None:
        self.assertEqual(review_cli.main([str(self.folder), "approve", "9"], cfg=CFG), 2)
        self.assertEqual(review_cli.main([str(self.folder), "feedback", "2"], cfg=CFG), 2)
        self.assertFalse((self.folder / review_state.REVIEW_FILE).exists())


if __name__ == "__main__":
    unittest.main()
