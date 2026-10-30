"""``video_fps`` logs why it fell back to 30 fps, so a wrong retime of a 24/25 fps source is diagnosable."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from podcast import sync  # noqa: E402


class VideoFpsFallbackTests(unittest.TestCase):
    def _fps(self, **patch_kwargs):
        with mock.patch.object(sync, "ffprobe_json", **patch_kwargs):
            return sync.video_fps(Path("guest.mp4"))

    def test_reads_rate(self) -> None:
        with self.assertNoLogs("podcast.sync", level="WARNING"):
            self.assertEqual(self._fps(return_value={"streams": [{"avg_frame_rate": "25/1"}]}), 25)

    def test_ffprobe_failure_warns_and_falls_back(self) -> None:
        with self.assertLogs("podcast.sync", level="WARNING"):
            self.assertEqual(self._fps(side_effect=subprocess.CalledProcessError(1, "ffprobe")), 30)

    def test_missing_rate_warns_and_falls_back(self) -> None:
        with self.assertLogs("podcast.sync", level="WARNING"):
            self.assertEqual(self._fps(return_value={"streams": []}), 30)


if __name__ == "__main__":
    unittest.main()
