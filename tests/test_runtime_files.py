"""Tests for common/runtime_files.py: writes land at the path the caller names."""

from __future__ import annotations

import shutil
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.runtime_files import (  # noqa: E402
    CODE_FOLDERS,
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


class CodeFolderGuardTests(unittest.TestCase):
    """Every entry point refuses a path that resolves under a runtime-write code folder."""

    def setUp(self) -> None:
        # Probe folder under a real code folder; removed on teardown so a helper that
        # does not refuse leaves nothing behind.
        self.probe = REPO_ROOT / "src" / "_runtime_files_guard_probe"
        self.addCleanup(shutil.rmtree, self.probe, ignore_errors=True)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_code_folders_match_the_ratchet_config(self) -> None:
        with (REPO_ROOT / ".fleet.toml").open("rb") as fh:
            configured = tomllib.load(fh)["runtime_write_ratchet"]["code_folders"]
        self.assertEqual(sorted(CODE_FOLDERS), sorted(configured))

    def test_ensure_folder_refuses_a_code_folder(self) -> None:
        with self.assertRaises(ValueError):
            ensure_folder(self.probe / "sub")
        self.assertFalse(self.probe.exists())

    def test_write_text_at_refuses_a_code_folder(self) -> None:
        self.probe.mkdir()
        with self.assertRaises(ValueError):
            write_text_at(self.probe / "note.txt", "x")
        self.assertFalse((self.probe / "note.txt").exists())

    def test_open_for_write_refuses_a_code_folder(self) -> None:
        self.probe.mkdir()
        with self.assertRaises(ValueError):
            open_for_write(self.probe / "raw.bin")
        self.assertFalse((self.probe / "raw.bin").exists())

    def test_copy_file_to_refuses_a_code_folder(self) -> None:
        self.probe.mkdir()
        src = self.root / "src.txt"
        src.write_text("x", encoding="utf-8")
        with self.assertRaises(ValueError):
            copy_file_to(src, self.probe / "dst.txt")
        self.assertFalse((self.probe / "dst.txt").exists())

    def test_refuses_the_code_folder_itself(self) -> None:
        with self.assertRaises(ValueError):
            ensure_folder(REPO_ROOT / "src")

    def test_refuses_a_dotdot_path_that_resolves_into_a_code_folder(self) -> None:
        with self.assertRaises(ValueError):
            ensure_folder(REPO_ROOT / "results" / ".." / "src" / "_runtime_files_guard_probe")
        self.assertFalse(self.probe.exists())

    def test_refuses_a_link_that_resolves_into_a_code_folder(self) -> None:
        self.probe.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(self.probe, target_is_directory=True)
        except OSError:
            if sys.platform != "win32":
                raise
            import _winapi  # a directory junction needs no elevated rights

            _winapi.CreateJunction(str(self.probe), str(link))
        with self.assertRaises(ValueError):
            write_text_at(link / "note.txt", "x")
        self.assertFalse((self.probe / "note.txt").exists())

    def test_error_names_the_folder_not_the_full_path(self) -> None:
        with self.assertRaises(ValueError) as caught:
            ensure_folder(self.probe)
        self.assertIn("src", str(caught.exception))
        self.assertNotIn(str(REPO_ROOT), str(caught.exception))

    def test_runtime_folders_inside_the_checkout_stay_writable(self) -> None:
        # results/ and data/ are the documented runtime output folders: never code folders.
        for name in ("results", "data"):
            self.assertNotIn(name, CODE_FOLDERS)
        target = REPO_ROOT / "results" / "_runtime_files_guard_probe"
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        self.assertEqual(ensure_folder(target), target)
        self.assertTrue(target.is_dir())


if __name__ == "__main__":
    unittest.main()
