"""The weekly-video helpers spawn ffprobe/ffmpeg only through ``podcast.media`` (issue #423).

``planning.videos.videos_session`` (``probe_duration_seconds``, ``_probe_video``,
``_run_ffmpeg_transcode``) used to hand-roll its own spawns. These fake the
spawn at the ``podcast.media`` boundary — so they only pass when the calls are
routed through it — and need no real ffmpeg.

Run: & .\\.venv\\Scripts\\python.exe -m unittest tests.videos.test_videos_ffprobe_routing -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from planning.videos import videos_session  # noqa: E402


def _no_tool():
    """``podcast.media`` finds neither ffprobe nor ffmpeg on PATH."""
    return mock.patch("podcast.media._tool", side_effect=RuntimeError("ffprobe not found on PATH"))


def _completed(payload: dict, returncode: int = 0, stderr: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, json.dumps(payload).encode(), stderr)


class _FakeTools:
    """Patch ``podcast.media``'s tool lookup (``_tool``) and spawn; record the argv it was handed.

    ``subprocess`` and ``shutil`` are shared modules, so patching them alone would also
    catch a hand-rolled spawn; patching ``_tool`` is what proves the call went through
    ``podcast.media``.
    """

    def __init__(self, test: unittest.TestCase, run: mock.Mock):
        self.run = run
        for target, new in (("podcast.media._tool", lambda name: f"/fake/{name}"),
                            ("podcast.media.subprocess.run", run)):
            patcher = mock.patch(target, new)
            patcher.start()
            test.addCleanup(patcher.stop)

    @property
    def argv(self) -> list[str]:
        return self.run.call_args.args[0]


class ProbeDurationTests(unittest.TestCase):
    def test_parses_the_format_duration(self):
        fake = _FakeTools(self, mock.Mock(return_value=_completed({"format": {"duration": "12.5"}})))
        self.assertEqual(videos_session.probe_duration_seconds(Path("clip.mp4")), 12.5)
        self.assertEqual(fake.argv[0], "/fake/ffprobe")
        self.assertIn("format=duration", fake.argv)

    def test_raises_when_ffprobe_reports_no_duration(self):
        _FakeTools(self, mock.Mock(return_value=_completed({"format": {}})))
        with self.assertRaisesRegex(RuntimeError, "no duration"):
            videos_session.probe_duration_seconds(Path("clip.mp4"))

    def test_raises_when_ffprobe_is_missing(self):
        with _no_tool():
            with self.assertRaisesRegex(RuntimeError, "ffprobe not found"):
                videos_session.probe_duration_seconds(Path("clip.mp4"))


class ProbeVideoTests(unittest.TestCase):
    def test_reads_size_bitrate_and_flags_a_non_av_stream(self):
        payload = {"format": {"size": "2097152", "bit_rate": "8000000"},
                   "streams": [{"codec_type": "video"}, {"codec_type": "audio"}, {"codec_type": "subtitle"}]}
        _FakeTools(self, mock.Mock(return_value=_completed(payload)))
        self.assertEqual(videos_session._probe_video(Path("clip.mp4")),
                         {"size": 2097152, "bit_rate": 8000000, "has_non_av_stream": True})

    def test_none_when_ffprobe_cannot_read_the_file(self):
        err = subprocess.CalledProcessError(1, ["ffprobe"], stderr=b"invalid data")
        _FakeTools(self, mock.Mock(side_effect=err))
        self.assertIsNone(videos_session._probe_video(Path("clip.mp4")))

    def test_none_when_ffprobe_is_missing(self):
        with _no_tool():
            self.assertIsNone(videos_session._probe_video(Path("clip.mp4")))


class TranscodeTests(unittest.TestCase):
    TCFG = {"target_bitrate_mbps": 10}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dst = Path(tmp.name) / "out" / "clip.mp4"

    def test_runs_ffmpeg_with_the_platform_safe_args(self):
        fake = _FakeTools(self, mock.Mock(return_value=subprocess.CompletedProcess([], 0, "", "")))
        self.assertTrue(videos_session._run_ffmpeg_transcode(Path("in.mov"), self.dst, self.TCFG))
        self.assertEqual(fake.argv[0], "/fake/ffmpeg")
        self.assertIn("-sn", fake.argv)
        self.assertIn("10M", fake.argv)
        self.assertEqual(fake.argv[-1], str(self.dst))

    def test_failure_removes_the_partial_output_and_returns_false(self):
        self.dst.parent.mkdir(parents=True)
        self.dst.write_bytes(b"partial")
        _FakeTools(self, mock.Mock(return_value=subprocess.CompletedProcess([], 1, "", "encoder exploded")))
        self.assertFalse(videos_session._run_ffmpeg_transcode(Path("in.mov"), self.dst, self.TCFG))
        self.assertFalse(self.dst.exists())

    def test_missing_ffmpeg_returns_false(self):
        with _no_tool():
            self.assertFalse(videos_session._run_ffmpeg_transcode(Path("in.mov"), self.dst, self.TCFG))


if __name__ == "__main__":
    unittest.main()
