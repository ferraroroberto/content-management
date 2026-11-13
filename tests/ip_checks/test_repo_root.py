"""Every check_ip module resolves the repository root from its place under tools/."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from tools.check_ip import db, lens, migrate, review, run, screen  # noqa: E402


class RepoRootTests(unittest.TestCase):
    def test_each_module_points_at_the_repository_root(self) -> None:
        for module in (db, lens, migrate, review, run, screen):
            with self.subTest(module=module.__name__):
                self.assertEqual(module.REPO_ROOT, REPO_ROOT)

    def test_schema_ships_beside_the_db_module(self) -> None:
        self.assertTrue(db.SCHEMA_PATH.is_file())


if __name__ == "__main__":
    unittest.main()
