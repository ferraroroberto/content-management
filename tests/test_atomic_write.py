"""Tests for common/atomic_write.py: the swap lands, and a failed write leaves nothing behind."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import atomic_write  # noqa: E402
from common.atomic_write import atomic_write_bytes, atomic_write_text  # noqa: E402


class AtomicWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)
        self.target = self.folder / "state.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_text_replace_succeeds(self) -> None:
        self.target.write_text("old", encoding="utf-8")
        atomic_write_text(self.target, '{"k": "ü"}')
        self.assertEqual(self.target.read_text(encoding="utf-8"), '{"k": "ü"}')
        self.assertEqual([p.name for p in self.folder.iterdir()], ["state.json"])

    def test_bytes_replace_creates_the_file(self) -> None:
        atomic_write_bytes(self.target, b"\x00\x01")
        self.assertEqual(self.target.read_bytes(), b"\x00\x01")
        self.assertEqual([p.name for p in self.folder.iterdir()], ["state.json"])

    def test_failed_replace_keeps_old_content_and_no_temp(self) -> None:
        self.target.write_text("old", encoding="utf-8")
        with mock.patch.object(atomic_write.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                atomic_write_text(self.target, "new")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "old")
        self.assertEqual([p.name for p in self.folder.iterdir()], ["state.json"])

    def test_failed_write_keeps_old_content_and_no_temp(self) -> None:
        self.target.write_text("old", encoding="utf-8")
        with self.assertRaises(UnicodeEncodeError):
            atomic_write_text(self.target, "\udcff", encoding="ascii")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "old")
        self.assertEqual([p.name for p in self.folder.iterdir()], ["state.json"])


if __name__ == "__main__":
    unittest.main()
