"""episode.json from a recorder or Riverside folder (issue #349) — synthetic fixtures only."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcast import init_episode  # noqa: E402
from podcast.episode import load_episode  # noqa: E402

CFG = {"host": {"first": "Sam", "name": "Sam Host"}}
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _recorder_file(video: Path, side: str, stamp: str, *, kind: str = "main", seconds: float = 0.0) -> Path:
    label = side if kind == "main" else f"{side} remote-ref"
    path = video / f"recorder - {label} - {stamp}.mp4"
    path.write_bytes(b"not a real video")
    path.with_suffix(".json").write_text(json.dumps({"kind": kind, "side": side, "rid": "x", "file": path.name,
                                                     "duration_s": seconds}), encoding="utf-8")
    return path


def _clip(path: Path, seconds: float, created: str = "2025-11-03T09:30:00Z") -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10",
                    "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", str(seconds), "-c:v", "libx264",
                    "-c:a", "aac", "-metadata", f"creation_time={created}", str(path)],
                   check=True, capture_output=True, creationflags=NO_WINDOW)
    return path


class RecorderLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        video = self.folder / "video editing"
        video.mkdir()
        _recorder_file(video, "host", "2026-09-30 101500", seconds=3605.24)
        _recorder_file(video, "guest", "2026-09-30 101507", seconds=3600.43)
        _recorder_file(video, "host", "2026-09-30 101502", kind="ref", seconds=3600.0)
        _recorder_file(video, "guest", "2026-09-30 101509", kind="ref", seconds=3598.0)
        (video / "synced - guest.mp4").write_bytes(b"sync stage output")

    def test_writes_an_episode_json_that_load_episode_accepts(self) -> None:
        code = init_episode.main([str(self.folder), "--guest", "Alex Rivera", "--adjective", "brilliant",
                                  "--pronoun", "her", "--link", "LinkedIn=https://example.com/alex"], cfg=CFG)
        self.assertEqual(code, 0)
        spec = json.loads((self.folder / "episode.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["tracks"], {"guest": "video editing/recorder - guest - 2026-09-30 101507.mp4",
                                          "host": "video editing/recorder - host - 2026-09-30 101500.mp4"})
        self.assertEqual((spec["date"], spec["start_s"], spec["end_s"]), ("2026-09-30", 0, 3600.4))
        self.assertEqual((spec["website_slug"], spec["guest_first"]), ("alex-rivera", "Alex"))
        self.assertIs(spec["host_is_interviewee"], False)
        ep = load_episode(self.folder, CFG)
        self.assertEqual((ep.guest, ep.adjective, ep.guest_pronoun_possessive), ("Alex Rivera", "brilliant", "her"))
        self.assertEqual(ep.extra["links"], [{"label": "LinkedIn", "url": "https://example.com/alex"}])
        self.assertEqual(ep.tracks["host"].name, "recorder - host - 2026-09-30 101500.mp4")

    def test_unknown_facts_are_marked_placeholders_and_still_load(self) -> None:
        self.assertEqual(init_episode.main([str(self.folder)], cfg=CFG), 0)
        spec = json.loads((self.folder / "episode.json").read_text(encoding="utf-8"))
        self.assertTrue(spec["guest"].startswith(init_episode.TODO))
        self.assertTrue(spec["adjective"].startswith(init_episode.TODO))
        self.assertEqual(spec["links"], [{"label": "LinkedIn", "url": init_episode.TODO}])
        load_episode(self.folder, CFG)

    def test_never_overwrites_without_force(self) -> None:
        existing = self.folder / "episode.json"
        existing.write_text('{"hand": "written"}', encoding="utf-8")
        self.assertEqual(init_episode.main([str(self.folder), "--guest", "Alex Rivera"], cfg=CFG), 1)
        self.assertEqual(existing.read_text(encoding="utf-8"), '{"hand": "written"}')
        self.assertEqual(init_episode.main([str(self.folder), "--guest", "Alex Rivera", "--force"], cfg=CFG), 0)
        self.assertEqual(json.loads(existing.read_text(encoding="utf-8"))["guest"], "Alex Rivera")

    def test_two_takes_of_one_side_need_the_owner_to_pick(self) -> None:
        _recorder_file(self.folder / "video editing", "host", "2026-09-30 112000", seconds=600.0)
        self.assertEqual(init_episode.main([str(self.folder)], cfg=CFG), 2)
        self.assertFalse((self.folder / "episode.json").exists())
        code = init_episode.main([str(self.folder), "--host-track", "recorder - host - 2026-09-30 101500.mp4"],
                                 cfg=CFG)
        self.assertEqual(code, 0)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg/ffprobe not on PATH")
class RiversideLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        self.video = self.folder / "video editing"
        self.video.mkdir()
        _clip(self.video / "riverside_sam_raw-synced-video-cfr_alex_- sam_0002.mp4", 2.0)
        _clip(self.video / "riverside_alex_rivera_raw-synced-video-cfr_alex_- sam_0003.mp4", 3.0)
        (self.video / "Alex Rivera and Sam Host.mp4").write_bytes(b"an old full edit, not a track")

    def test_writes_an_episode_json_that_load_episode_accepts(self) -> None:
        self.assertEqual(init_episode.main([str(self.folder), "--guest", "Alex Rivera"], cfg=CFG), 0)
        spec = json.loads((self.folder / "episode.json").read_text(encoding="utf-8"))
        self.assertEqual(spec["tracks"]["host"], "video editing/riverside_sam_raw-synced-video-cfr_alex_- sam_0002.mp4")
        self.assertEqual(spec["tracks"]["guest"],
                         "video editing/riverside_alex_rivera_raw-synced-video-cfr_alex_- sam_0003.mp4")
        self.assertEqual(spec["date"], "2025-11-03")  # the container's creation time
        self.assertAlmostEqual(spec["end_s"], 2.0, delta=0.15)
        ep = load_episode(self.folder, CFG)
        self.assertEqual(ep.guest_first, "Alex")


class RiversideNameTests(unittest.TestCase):
    def test_speaker_tokens_of_the_known_name_patterns(self) -> None:
        cases = {
            "riverside_sam_raw-synced-video-cfr_alex_- sam_0002": "sam",
            "riverside_dr._alex rivera_raw-synced-video-cfr_alex_0059": "dr._alex rivera",
            "sam_host_raw-synced-video-cfr_riverside_0001": "sam_host",
            "riverside_alex-rivera-and-sam-host-alex": "alex",
            "Alex Rivera and Sam Host": None,
            "source - Alex": None,
        }
        for stem, token in cases.items():
            self.assertEqual(init_episode.riverside_speaker(stem), token, stem)

    def test_host_is_told_apart_by_first_name_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp)
            for name in ("sam_host_raw-synced-video-cfr_riverside_0001.mp4",
                         "riverside_dr._alex rivera_raw-synced-video-cfr_alex_0059.mp4",
                         "riverside_samantha_raw-synced-video-cfr_x_0003.mp4"):
                (video / name).write_bytes(b"")
            found = init_episode.riverside_tracks(video, "Sam")
            self.assertEqual([p.name for p in found["host"]], ["sam_host_raw-synced-video-cfr_riverside_0001.mp4"])
            self.assertEqual(len(found["guest"]), 2)

    def test_slug(self) -> None:
        self.assertEqual(init_episode.slugify("Dr. Zoë O'Neil"), "dr-zoe-o-neil")


if __name__ == "__main__":
    unittest.main()
