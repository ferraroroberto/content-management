"""The Threads and Twitter dry-run teardown lives in the composer modules only.

A button label (Discard, Cancel, Close) changes in one place, and
``/schedule-autoheal`` fixes selectors in one place, so the post schedulers and the
weekly-video schedulers must call the composer helper rather than carry their own
copy of the click loop. Nothing here opens a browser: the page is a recording fake.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from planning.threads.threads_composer import discard_composer
from planning.twitter.twitter_composer import close_schedule_modal

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Every module that drives a Threads or Twitter composer from outside the composer.
_CALLERS = {
    "planning/threads/schedule_threads_posts.py": "planning.threads.threads_labels",
    "planning/videos/videos_threads.py": "planning.threads.threads_labels",
    "planning/twitter/schedule_twitter_posts.py": "planning.twitter.twitter_labels",
    "planning/videos/videos_twitter.py": "planning.twitter.twitter_labels",
}


class _FakeLocator:
    def __init__(self, page: "_FakePage", label: str) -> None:
        self._page = page
        self._label = label

    def count(self) -> int:
        return 1 if self._label in self._page.present else 0

    @property
    def first(self) -> "_FakeLocator":
        return self

    def click(self, timeout: int = 0) -> None:
        self._page.clicks.append(self._label)
        self._page.present.discard(self._label)


class _FakePage:
    """Buttons named in ``present`` exist until clicked; every click is recorded."""

    def __init__(self, *buttons: str) -> None:
        self.present = set(buttons)
        self.clicks: list[str] = []

    def get_by_role(self, role: str, name=None) -> _FakeLocator:
        for label in self.present:
            if name is not None and name.search(label):
                return _FakeLocator(self, label)
        return _FakeLocator(self, "")

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, "")

    def wait_for_timeout(self, ms: int) -> None:
        return None


class ComposerTeardownTest(unittest.TestCase):
    def test_schedulers_do_not_import_button_labels(self) -> None:
        for rel, labels_module in _CALLERS.items():
            tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
            imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
            self.assertNotIn(labels_module, imported, f"{rel} owns a label loop again")

    def test_discard_composer_cancels_then_confirms_discard(self) -> None:
        page = _FakePage("Cancel", "Discard")
        discard_composer(page)
        self.assertEqual(page.clicks, ["Cancel", "Discard"])

    def test_discard_composer_tolerates_a_missing_prompt(self) -> None:
        page = _FakePage("Cancel")
        discard_composer(page)
        self.assertEqual(page.clicks, ["Cancel"])

    def test_close_schedule_modal_closes_modal_once(self) -> None:
        page = _FakePage("Cancel", "Close")
        close_schedule_modal(page)
        self.assertEqual(page.clicks[0], "Cancel")
        self.assertNotIn("Discard", page.clicks)


if __name__ == "__main__":
    unittest.main()
