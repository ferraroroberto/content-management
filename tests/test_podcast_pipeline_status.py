"""``podcast_pipeline.py --status``: where an episode stands, read-only (issue #350)."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import podcast_pipeline  # noqa: E402

CFG = {"package_dirname": "podcast package", "host": {"first": "Sam", "name": "Sam Host"}}


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        video = self.folder / "video editing"
        video.mkdir()
        for name in ("h.mp4", "g.mp4"):
            (video / name).write_bytes(b"")
        (self.folder / "episode.json").write_text(json.dumps({
            "guest": "Alex Rivera", "tracks": {"host": "video editing/h.mp4", "guest": "video editing/g.mp4"}}),
            encoding="utf-8")

    def _status(self) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(podcast_pipeline, "load_podcast_config", return_value=CFG), \
                contextlib.redirect_stdout(out):
            code = podcast_pipeline.main([str(self.folder), "--status"])
        return code, out.getvalue()

    def test_a_fresh_episode_lists_every_stage_to_run_and_writes_nothing(self) -> None:
        before = sorted(p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*"))
        code, out = self._status()
        self.assertEqual(code, 0)
        self.assertIn("transcribe: to run", out)
        self.assertIn("sync: not needed", out)  # no recorder references: nothing to align
        self.assertIn("next: transcribe", out)
        self.assertEqual(sorted(p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*")), before)

    def test_review_gated_stages_say_they_wait_for_the_review(self) -> None:
        package = self.folder / "podcast package"
        (package / "transcript").mkdir(parents=True)
        (package / "transcript" / "turns.json").write_text("[]", encoding="utf-8")
        (package / "Alex Rivera - Sam Host - transcript.md").write_text("", encoding="utf-8")
        (package / "clips.md").write_text("", encoding="utf-8")
        (package / "clips.json").write_text(json.dumps([{"number": 1, "keep": [], "caption_review": {}},
                                                       {"number": 2, "keep": [], "caption_review": {}}]),
                                            encoding="utf-8")
        (self.folder / "review.json").write_text(json.dumps({"clips": {
            "1": {"status": "approved", "version": 1, "rounds": []},
            "2": {"status": "changes", "version": 1, "rounds": [{"at": "x", "feedback": "no punch-ins",
                                                                  "version": 1}]}}}), encoding="utf-8")
        code, out = self._status()
        self.assertEqual(code, 0)
        self.assertIn("select: done", out)
        self.assertIn("covers: waiting for review", out)
        self.assertIn("1/2 approved", out)
        self.assertIn("open feedback on clips [2]", out)

    def test_a_missing_track_fails_the_status(self) -> None:
        (self.folder / "video editing" / "g.mp4").unlink()
        code, out = self._status()
        self.assertEqual(code, 2)
        self.assertIn("guest track missing", out)


if __name__ == "__main__":
    unittest.main()
