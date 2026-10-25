"""Regression coverage for the LinkedIn comment DOM selector variants."""

from __future__ import annotations

import unittest

from engagement.linkedin.scrape_comments import (
    SEL_COMMENT_LIST_CONTAINER,
    SEL_COMMENT_TEXT_NODES,
)


class LinkedInCommentSelectorTests(unittest.TestCase):
    """Keep the scheduled-run fallback alongside the component DOM hooks."""

    def test_list_selector_supports_component_and_legacy_comment_markup(self):
        self.assertIn("[data-testid$='FeedType_FEED_DETAIL']", SEL_COMMENT_LIST_CONTAINER)
        self.assertIn(".comments-comment-list__container", SEL_COMMENT_LIST_CONTAINER)

    def test_text_selector_supports_component_and_legacy_comment_markup(self):
        self.assertIn("[data-testid='expandable-text-box']", SEL_COMMENT_TEXT_NODES)
        self.assertIn(".comments-comment-item__main-content", SEL_COMMENT_TEXT_NODES)


if __name__ == "__main__":
    unittest.main()
