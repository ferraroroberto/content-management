"""Every LinkedIn share-box click proves its effect before it counts (issue #271).

Since 2026-09 every feed load logs React error #418 (hydration mismatch), so
LinkedIn re-renders the share box client-side and an early click is swallowed.
``click_feed_entry`` already had the guard for that — ``expect_selector``,
re-click until the click's effect attaches (issue #150) — but only the Photo
caller passed one. The 2026-09-11 run lost the carousel row ('Start a post' →
no composer → 'Expand content types' timeout) and the weekly video's LinkedIn
leg ('Video' → no editor → 45 s file-input timeout) to exactly that.

The fakes model a feed whose first click is swallowed. The regression proof is
``test_inert_first_click_is_retried``: the pre-#271 callers passed no
``expect_selector``, so the swallowed click returned as a success.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from unittest import mock

from planning.linkedin import linkedin_composer as lc
from planning.linkedin import schedule_linkedin_posts as sl
from planning.linkedin.linkedin_labels import (
    COMPOSER_EDITOR_SEL,
    DIALOG_SEL,
    MEDIA_FILE_INPUT_SEL,
)
from planning.videos import videos_linkedin as vl


class _Effect:
    """The locator for the effect a click should produce."""

    def __init__(self, feed: "_FakeFeed") -> None:
        self._feed = feed

    @property
    def first(self) -> "_Effect":
        return self

    def count(self) -> int:
        return 1 if self._feed.effect_on else 0

    def wait_for(self, *, state: str, timeout: float) -> None:
        if not self._feed.effect_on:
            raise TimeoutError("effect never attached")


class _Button:
    def __init__(self, feed: "_FakeFeed") -> None:
        self._feed = feed

    @property
    def first(self) -> "_Button":
        return self

    def click(self, *, timeout: float) -> None:
        self._feed.clicks += 1
        if self._feed.clicks > self._feed.swallow:
            self._feed.effect_on = True


class _FakeFeed:
    """A share box whose first ``swallow`` clicks land without effect."""

    def __init__(self, swallow: int = 0, late_effect_after_wait: bool = False) -> None:
        self.swallow = swallow
        self.late_effect_after_wait = late_effect_after_wait
        self.clicks = 0
        self.effect_on = False
        self.selectors: list[str] = []

    def get_by_text(self, _rx) -> _Button:
        return _Button(self)

    def locator(self, selector: str) -> _Effect:
        self.selectors.append(selector)
        return _Effect(self)

    def wait_for_timeout(self, _ms: float) -> None:
        # The effect of the swallowed click turning up late, during the settle.
        if self.late_effect_after_wait:
            self.effect_on = True


class ClickFeedEntryGuardTests(unittest.TestCase):

    def test_inert_first_click_is_retried(self):
        feed = _FakeFeed(swallow=1)
        lc.click_feed_entry(feed, None, "Video", expect_selector=MEDIA_FILE_INPUT_SEL)
        self.assertEqual(feed.clicks, 2)
        self.assertTrue(feed.effect_on)

    def test_late_effect_is_not_re_clicked(self):
        """A re-click would hit the editor's modal and burn the 30 s budget."""
        feed = _FakeFeed(swallow=99, late_effect_after_wait=True)
        lc.click_feed_entry(feed, None, "Video", expect_selector=MEDIA_FILE_INPUT_SEL)
        self.assertEqual(feed.clicks, 1)

    def test_unguarded_click_returns_on_first_landing(self):
        feed = _FakeFeed(swallow=1)
        lc.click_feed_entry(feed, None, "Start a post")
        self.assertEqual(feed.clicks, 1, "no guard → a swallowed click reads as success")

    def test_never_effective_click_fails_loud(self):
        feed = _FakeFeed(swallow=10**9)
        with mock.patch.object(lc, "FEED_ENTRY_CLICK_TIMEOUT_MS", 0):
            with self.assertRaisesRegex(RuntimeError, "Could not click 'Video'"):
                lc.click_feed_entry(feed, None, "Video", expect_selector=MEDIA_FILE_INPUT_SEL)


class CallersPassAGuardTests(unittest.TestCase):
    """Pin that every share-box caller passes its effect selector."""

    def _guard_of(self, fn, module) -> str:
        with mock.patch.object(module, "click_feed_entry") as spy:
            fn(mock.MagicMock())
        return spy.call_args.kwargs.get("expect_selector")

    def test_start_a_post_waits_for_the_composer_editor(self):
        self.assertEqual(self._guard_of(sl._click_start_a_post, sl), COMPOSER_EDITOR_SEL)

    def test_video_waits_for_its_file_input(self):
        self.assertEqual(self._guard_of(vl._click_video_button, vl), MEDIA_FILE_INPUT_SEL)

    def test_photo_waits_for_its_file_input(self):
        self.assertEqual(self._guard_of(sl._click_add_photo, sl), MEDIA_FILE_INPUT_SEL)


class ComposerEditorSelectorTests(unittest.TestCase):

    def test_scoped_to_every_dialog_branch(self):
        """Unscoped, a feed comment box could satisfy the guard vacuously."""
        clauses = [c.strip() for c in COMPOSER_EDITOR_SEL.split(",")]
        branches = [b.strip() for b in DIALOG_SEL.split(",")]
        self.assertEqual(len(clauses), len(branches))
        for clause, branch in zip(clauses, branches):
            self.assertTrue(clause.startswith(branch + " "), clause)
            self.assertIn('div[role="textbox"][contenteditable="true"]', clause)


if __name__ == "__main__":
    unittest.main()
