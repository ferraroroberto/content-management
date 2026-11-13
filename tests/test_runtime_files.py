"""Tests for common/runtime_files.py: writes land at the path the caller names."""

from __future__ import annotations

import shutil
import sys
import tempfile
import tomllib
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.runtime_files import (  # noqa: E402
    CODE_FOLDERS,
    copy_file_to,
    ensure_folder,
    fold_legacy_folder,
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


class FoldLegacyFolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.legacy = self.root / "legacy"
        self.target = self.root / "target"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_moves_missing_files_and_empties_the_legacy_side(self) -> None:
        (self.legacy / "sub").mkdir(parents=True)
        (self.legacy / "a.json").write_text("a", encoding="utf-8")
        (self.legacy / "sub" / "b.json").write_text("b", encoding="utf-8")
        self.assertEqual(fold_legacy_folder(self.legacy, self.target), 2)
        self.assertEqual((self.target / "a.json").read_text(encoding="utf-8"), "a")
        self.assertEqual((self.target / "sub" / "b.json").read_text(encoding="utf-8"), "b")
        self.assertEqual([p for p in self.legacy.rglob("*") if p.is_file()], [])

    def test_a_name_already_in_the_target_wins_and_the_legacy_copy_stays(self) -> None:
        self.legacy.mkdir()
        self.target.mkdir()
        (self.legacy / "a.json").write_text("old", encoding="utf-8")
        (self.target / "a.json").write_text("new", encoding="utf-8")
        self.assertEqual(fold_legacy_folder(self.legacy, self.target), 0)
        self.assertEqual((self.target / "a.json").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.legacy / "a.json").exists())

    def test_a_second_fold_is_a_no_op_and_a_missing_legacy_folder_is_fine(self) -> None:
        self.legacy.mkdir()
        (self.legacy / "a.json").write_text("a", encoding="utf-8")
        self.assertEqual(fold_legacy_folder(self.legacy, self.target), 1)
        (self.target / "a.json").unlink()  # deleted on purpose later: must not come back
        self.assertEqual(fold_legacy_folder(self.legacy, self.target), 0)
        self.assertFalse((self.target / "a.json").exists())
        self.assertEqual(fold_legacy_folder(self.root / "absent", self.target), 0)

    def test_refuses_a_target_inside_a_code_folder(self) -> None:
        self.legacy.mkdir()
        (self.legacy / "a.json").write_text("a", encoding="utf-8")
        with self.assertRaises(ValueError):
            fold_legacy_folder(self.legacy, REPO_ROOT / "src" / "_fold_probe")
        self.assertTrue((self.legacy / "a.json").exists())


class ReportingResultsFolderTests(unittest.TestCase):
    def test_config_relative_value_lands_at_the_repo_level_not_under_reporting(self) -> None:
        from tools.reporting import results_paths

        results_paths.results_folder.cache_clear()
        self.addCleanup(results_paths.results_folder.cache_clear)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "reporting" / "results" / "raw"
            legacy.mkdir(parents=True)
            (legacy / "x_posts_2026-01-01.json").write_text("{}", encoding="utf-8")
            with unittest.mock.patch.object(results_paths, "REPO_ROOT", root):
                folder = results_paths.results_folder("results/raw")
            self.assertEqual(folder, root / "results" / "raw")
            self.assertTrue((folder / "x_posts_2026-01-01.json").exists())
            self.assertFalse((legacy / "x_posts_2026-01-01.json").exists())


if __name__ == "__main__":
    unittest.main()
