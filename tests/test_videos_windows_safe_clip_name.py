"""Clip files whose Notion name carries Windows-illegal characters (issue #317).

The 2026-09-26 live run failed the weekly video on all four platforms before
any browser work: ``filePC`` was "start small: the 10-at-10 method", but
Windows can't store a colon, so the exported clip is
"start small the 10-at-10 method.mp4". ``load_clip_payload`` built the path
verbatim and raised ``Video file not found``.

These tests pin the fix: the verbatim name still wins when it exists, the
Windows-safe name is the fallback, and a genuinely missing clip still raises
and names both candidates.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planning.videos import videos_session as vs  # noqa: E402

LIVE_FILE_PC = "start small: the 10-at-10 method"
ON_DISK_STEM = "start small the 10-at-10 method"


class WindowsSafeFilenameTests(unittest.TestCase):
    def test_drops_illegal_chars_and_collapses_whitespace(self) -> None:
        self.assertEqual(vs.windows_safe_filename(LIVE_FILE_PC), ON_DISK_STEM)
        self.assertEqual(vs.windows_safe_filename('why? "now" | later*'), "why now later")
        self.assertEqual(vs.windows_safe_filename("trailing dots..."), "trailing dots")

    def test_clean_name_is_unchanged(self) -> None:
        name = "learn like nobody's watching (or evaluating) you"
        self.assertEqual(vs.windows_safe_filename(name), name)


class LoadClipPayloadPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name + "\\"
        self.addCleanup(self._tmp.cleanup)

    def _load(self, file_pc: str):
        props = {"clip_pc": self.folder, "file_pc": file_pc}
        with mock.patch.object(vs, "first_clip_relation_id", return_value="rel"), \
             mock.patch.object(vs, "retrieve_page", return_value={}), \
             mock.patch.object(vs, "_clip_page_title", return_value="clip"), \
             mock.patch.object(vs, "_clip_string_property",
                               side_effect=lambda _p, _c, role: props.get(role, "")), \
             mock.patch.object(vs, "_clip_text_property", return_value="short"), \
             mock.patch.object(vs, "get_page_body_text", return_value="long"), \
             mock.patch.object(vs, "ensure_local_file"), \
             mock.patch.object(vs, "ensure_platform_safe_clip", side_effect=lambda p: p):
            return vs.load_clip_payload(None, {}, {}, {})

    def _touch(self, stem: str, ext: str) -> Path:
        path = Path(self.folder) / f"{stem}{ext}"
        path.write_bytes(b"x")
        return path

    def test_illegal_char_name_resolves_to_windows_safe_file(self) -> None:
        video = self._touch(ON_DISK_STEM, ".mp4")
        thumb = self._touch(ON_DISK_STEM, ".png")
        payload = self._load(LIVE_FILE_PC)
        self.assertEqual(payload.video_path, video)
        self.assertEqual(payload.thumb_path, thumb)

    def test_clean_name_resolves_verbatim(self) -> None:
        video = self._touch("first build then earn", ".mp4")
        payload = self._load("first build then earn")
        self.assertEqual(payload.video_path, video)

    def test_missing_clip_raises_naming_both_candidates(self) -> None:
        with self.assertRaises(FileNotFoundError) as ctx:
            self._load(LIVE_FILE_PC)
        msg = str(ctx.exception)
        self.assertIn(f"{LIVE_FILE_PC}.mp4", msg)
        self.assertIn(f"{ON_DISK_STEM}.mp4", msg)


if __name__ == "__main__":
    unittest.main()
