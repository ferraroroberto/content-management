r"""Characterization tests for ``planning._session_base.results_dir``.

Every planning scheduler / video driver used to build ``results/<platform>`` by
counting ``.parent`` hops from its own ``__file__``, so moving a file one level
silently redirected its screenshots. ``results_dir(name)`` now derives it from
``REPO_ROOT`` once. These tests pin that each former site resolves to the very
path it used to, and that no counted-parent ``results`` expression is left.

Run: & .\.venv\Scripts\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from planning._session_base import REPO_ROOT, results_dir
from planning.videos.videos_ledger import DEFAULT_LEDGER_PATH

# module path (relative to the repo root) -> results sub-folder it wrote to
SITES = {
    "planning/instagram/schedule_instagram_posts.py": "instagram",
    "planning/instagram/instagram_upload.py": "instagram",
    "planning/threads/schedule_threads_posts.py": "threads",
    "planning/twitter/schedule_twitter_posts.py": "twitter",
    "planning/linkedin/schedule_linkedin_posts.py": "linkedin",
    "planning/substack/post_substack_note.py": "substack",
    "planning/videos/videos_instagram.py": "videos",
    "planning/videos/videos_twitter.py": "videos",
    "planning/videos/videos_threads.py": "videos",
    "planning/videos/videos_linkedin.py": "videos",
}


class ResultsDirTests(unittest.TestCase):
    def test_resolves_to_the_path_each_site_used_to_build(self) -> None:
        for rel, name in SITES.items():
            with self.subTest(module=rel):
                old = Path(REPO_ROOT / rel).resolve().parent.parent.parent / "results" / name
                self.assertEqual(results_dir(name), old)

    def test_ledger_default_is_unchanged(self) -> None:
        self.assertEqual(
            DEFAULT_LEDGER_PATH,
            REPO_ROOT / "results" / "videos" / "tag_along_scheduled.json",
        )

    def test_no_planning_module_counts_parents_to_reach_results(self) -> None:
        counted = re.compile(r"\.parent\.parent\.parent\s*/\s*[\"']results[\"']")
        for path in (REPO_ROOT / "planning").rglob("*.py"):
            with self.subTest(module=path.name):
                self.assertIsNone(counted.search(path.read_text(encoding="utf-8")))

    def test_every_site_goes_through_the_helper(self) -> None:
        for rel, name in SITES.items():
            with self.subTest(module=rel):
                source = (REPO_ROOT / rel).read_text(encoding="utf-8")
                self.assertIn(f'results_dir("{name}")', source)


if __name__ == "__main__":
    unittest.main()
