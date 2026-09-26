"""Regression test for the Threads composer-entry labels (issue #315).

The 2026-09-26 live run failed every Threads row at "Threads composer modal":
all three profile-row candidates reported ``count=0``. Threads had switched the
placeholder to a typographic apostrophe (``What’s new?``, U+2019) and dropped
the ``Create new thread`` aria-label, while every selector hard-coded the ASCII
``'``. The strings below are copied verbatim from a live DOM probe of that
build, so this test fails on the old straight-quote-only labels.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest

from planning.threads.threads_labels import (
    COMPOSE_ENTRY_BTN_RE,
    WHATS_NEW_PLACEHOLDER_RE,
    WHATS_NEW_TEXTBOX_RE,
)

# Live DOM, 2026-09-26: profile composer row and dialog caption box share it.
LIVE_ARIA_LABEL = "Empty text field. Type to compose a new post."
LIVE_PLACEHOLDER = "What’s new?"
LEGACY_PLACEHOLDER = "What's new?"


class ComposeEntryLabelTests(unittest.TestCase):
    def test_placeholder_matches_both_apostrophes(self) -> None:
        for text in (LIVE_PLACEHOLDER, LEGACY_PLACEHOLDER):
            with self.subTest(text=text):
                self.assertRegex(text, WHATS_NEW_PLACEHOLDER_RE)

    def test_compose_entry_matches_current_and_legacy_aria(self) -> None:
        for name in (LIVE_ARIA_LABEL, "Create new thread"):
            with self.subTest(name=name):
                self.assertRegex(name, COMPOSE_ENTRY_BTN_RE)

    def test_compose_entry_ignores_other_create_controls(self) -> None:
        # The sidebar "New thread" link and the floating "+" ("Create") must
        # not be mistaken for the inline composer row.
        for name in ("New thread", "Create", "Post"):
            with self.subTest(name=name):
                self.assertIsNone(COMPOSE_ENTRY_BTN_RE.search(name))

    def test_caption_textbox_matches_live_and_legacy_names(self) -> None:
        for name in (LIVE_ARIA_LABEL, LIVE_PLACEHOLDER, LEGACY_PLACEHOLDER):
            with self.subTest(name=name):
                self.assertRegex(name, WHATS_NEW_TEXTBOX_RE)


if __name__ == "__main__":
    unittest.main()
