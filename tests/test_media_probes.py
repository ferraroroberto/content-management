"""Characterization tests for the ffmpeg/ffprobe call sites outside ``podcast.media`` (issue #406).

``podcast.sync.video_fps``, ``podcast.init_episode`` (duration / recording
date), ``demo_video.media.has_audio`` and ``recorder.store``
(``remux`` / ``probe_duration``) each spawned ffprobe/ffmpeg by hand. These pin
what each returns — on a real clip, a video-only clip and an unreadable file —
so routing them through ``podcast.media`` keeps the results unchanged.

Clips are synthesised with ffmpeg's ``lavfi`` sources into a temp dir; the
tests skip without ffmpeg on PATH.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from demo_video import media as demo_media  # noqa: E402
from podcast import init_episode, sync  # noqa: E402
from podcast.media import NO_WINDOW  # noqa: E402
from recorder import store  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _make_clip(dst: Path, *, rate: int = 30, audio: bool = True, extra: tuple[str, ...] = ()) -> Path:
    inputs = ["-f", "lavfi", "-i", f"testsrc=size=160x90:rate={rate}:duration=2"]
    if audio:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, *extra, "-shortest", str(dst)],
                   check=True, capture_output=True, creationflags=NO_WINDOW)
    return dst


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg not on PATH")
class MediaProbeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.garbage = self.dir / "garbage.mp4"
        self.garbage.write_bytes(b"not a video")

    def test_video_fps(self):
        self.assertEqual(sync.video_fps(_make_clip(self.dir / "c30.mp4", rate=30)), 30)
        self.assertEqual(sync.video_fps(_make_clip(self.dir / "c25.mp4", rate=25, audio=False)), 25)

    def test_video_fps_falls_back_to_30_when_unreadable(self):
        self.assertEqual(sync.video_fps(self.garbage), 30.0)

    def test_init_episode_duration_and_creation_date(self):
        clip = _make_clip(self.dir / "clip.mp4", extra=("-metadata", "creation_time=2026-03-04T10:00:00Z"))
        self.assertAlmostEqual(init_episode.duration(clip), 2.0, delta=0.1)
        self.assertEqual(init_episode.recording_date(clip), "2026-03-04")

    def test_init_episode_duration_raises_when_unreadable(self):
        with self.assertRaises(subprocess.CalledProcessError):
            init_episode.duration(self.garbage)

    def test_has_audio(self):
        self.assertIs(demo_media.has_audio(_make_clip(self.dir / "av.mp4")), True)
        self.assertIs(demo_media.has_audio(_make_clip(self.dir / "v.mp4", audio=False)), False)
        self.assertIsNone(demo_media.has_audio(self.garbage))

    def test_recorder_remux_and_probe_duration(self):
        src = _make_clip(self.dir / "joined.webm")
        dst = self.dir / "out.mp4"
        store.remux(src, dst)
        self.assertTrue(dst.exists())
        self.assertAlmostEqual(store.probe_duration(dst), 2.0, delta=0.1)
        self.assertIs(demo_media.has_audio(dst), True)

    def test_recorder_remux_raises_on_unreadable_input(self):
        with self.assertRaises(RuntimeError):
            store.remux(self.garbage, self.dir / "out.mp4")

    def test_recorder_probe_duration_raises_on_unreadable_input(self):
        with self.assertRaises(subprocess.CalledProcessError):
            store.probe_duration(self.garbage)


if __name__ == "__main__":
    unittest.main()
