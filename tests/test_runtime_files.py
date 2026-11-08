"""Tests for common/runtime_files.py: writes land at the path the caller names."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.runtime_files import (  # noqa: E402
    copy_file_to,
    ensure_folder,
    open_for_write,
    write_text_at,
)


class RuntimeFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_ensure_folder_creates_parents_and_is_idempotent(self) -> None:
        target = self.root / "a" / "b"
        self.assertEqual(ensure_folder(target), target)
        self.assertEqual(ensure_folder(target), target)
        self.assertTrue(target.is_dir())

    def test_ensure_folder_without_parents_needs_the_parent(self) -> None:
        with self.assertRaises(FileNotFoundError):
            ensure_folder(self.root / "missing" / "child", parents=False)
        self.assertTrue(ensure_folder(self.root / "child", parents=False).is_dir())

    def test_write_text_at_replaces_and_keeps_unicode(self) -> None:
        target = self.root / "note.txt"
        write_text_at(target, "old")
        self.assertEqual(write_text_at(target, "ü\nnew"), 5)
        self.assertEqual(target.read_text(encoding="utf-8"), "ü\nnew")

    def test_write_text_at_honours_newline(self) -> None:
        target = self.root / "lf.txt"
        write_text_at(target, "a\nb", newline="\n")
        self.assertEqual(target.read_bytes(), b"a\nb")

    def test_open_for_write_binary_and_text(self) -> None:
        raw = self.root / "raw.bin"
        with open_for_write(raw) as handle:
            handle.write(b"\x00\x01")
        self.assertEqual(raw.read_bytes(), b"\x00\x01")
        text = self.root / "t.txt"
        with open_for_write(text, "w", encoding="utf-8") as handle:
            handle.write("é")
        self.assertEqual(text.read_text(encoding="utf-8"), "é")

    def test_copy_file_to_returns_destination(self) -> None:
        src = self.root / "src.txt"
        src.write_text("x", encoding="utf-8")
        dst = self.root / "dst.txt"
        self.assertEqual(copy_file_to(src, dst), dst)
        self.assertEqual(dst.read_text(encoding="utf-8"), "x")


if __name__ == "__main__":
    unittest.main()
