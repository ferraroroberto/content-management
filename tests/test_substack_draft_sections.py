"""The newsletter -> Substack draft body must be structurally correct (issue #184).

``newsletter/substack_draft.py`` turns ``build_newsletter``'s grouped articles
into a Substack draft. The body is ProseMirror JSON, and a malformed body fails
silently — it produces a draft that *exists* but renders as plain text with dead
links, which you only notice by eye in the Substack editor.

Two things are pinned here, both pure (no Notion, no Substack, no network):

* ``build_sections`` mirrors the HTML builder — same headings, same order, empty
  topics kept rather than dropped.
* ``build_section_nodes`` emits real ``bullet_list``/``list_item`` nodes with
  ``link`` marks, and treats article titles as **literal text**. The library's
  ``Post.from_markdown`` cannot express this shape (it folds bullets that follow
  a heading into the heading's own text node), which is why the nodes are built
  explicitly — this test is what stops a well-meaning refactor back to it.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest

from newsletter.substack_draft import build_sections, compose_intro
from planning.substack.api_client import build_section_nodes

TOPICS = ["personal development", "innovation", "leadership and management"]


class BuildSectionsTests(unittest.TestCase):
    def test_headings_match_the_html_builder(self):
        grouped = {t: [] for t in TOPICS}
        headings = [h for h, _ in build_sections(grouped, TOPICS)]
        self.assertEqual(
            headings,
            ["Personal development", "Innovation", "Leadership and management"],
        )

    def test_topic_order_and_articles_preserved(self):
        grouped = {
            "personal development": [("A", "https://a"), ("B", "https://b")],
            "innovation": [("C", "https://c")],
            "leadership and management": [],
        }
        sections = build_sections(grouped, TOPICS)
        self.assertEqual([len(a) for _, a in sections], [2, 1, 0])
        self.assertEqual(sections[0][1][0], ("A", "https://a"))

    def test_empty_topic_keeps_its_heading(self):
        """The HTML build emits an empty <ul> rather than dropping the topic."""
        sections = build_sections({t: [] for t in TOPICS}, TOPICS)
        self.assertEqual(len(sections), 3)


class BuildSectionNodesTests(unittest.TestCase):
    def test_linked_bullets_carry_link_marks(self):
        nodes = build_section_nodes([("Innovation", [("The new stack", "https://x/c")])])
        self.assertEqual([n["type"] for n in nodes], ["heading", "bullet_list"])
        self.assertEqual(nodes[0]["attrs"]["level"], 2)

        items = nodes[1]["content"]
        self.assertEqual(len(items), 1)
        text_node = items[0]["content"][0]["content"][0]
        self.assertEqual(text_node["text"], "The new stack")
        self.assertEqual(
            text_node["marks"], [{"type": "link", "attrs": {"href": "https://x/c"}}]
        )

    def test_empty_topic_emits_heading_without_empty_bullet_list(self):
        """An empty bullet_list is not valid ProseMirror."""
        nodes = build_section_nodes([("Innovation", [])])
        self.assertEqual([n["type"] for n in nodes], ["heading"])

    def test_titles_are_literal_text_not_markdown(self):
        """Article titles come from arbitrary web pages — markdown-ish characters
        must survive verbatim rather than being parsed into marks or eaten."""
        title = "Why *args and [brackets] break `parsers`"
        nodes = build_section_nodes([("Innovation", [(title, "https://x")])])
        text_node = nodes[1]["content"][0]["content"][0]["content"][0]
        self.assertEqual(text_node["text"], title)
        self.assertEqual([m["type"] for m in text_node["marks"]], ["link"])

    def test_intro_becomes_the_first_paragraph(self):
        nodes = build_section_nodes([("Innovation", [])], intro="Read this first.")
        self.assertEqual(nodes[0]["type"], "paragraph")
        self.assertEqual(nodes[0]["content"][0]["text"], "Read this first.")
        self.assertNotIn("marks", nodes[0]["content"][0])

    def test_no_intro_means_no_leading_paragraph(self):
        nodes = build_section_nodes([("Innovation", [])])
        self.assertEqual(nodes[0]["type"], "heading")


class ComposeIntroTests(unittest.TestCase):
    FULL = {
        "personal development": [("A", "https://a")],
        "innovation": [("B", "https://b")],
        "leadership and management": [("C", "https://c")],
    }

    def test_none_when_no_choice_made(self):
        self.assertIsNone(compose_intro(TOPICS, self.FULL, None))

    def test_none_when_a_topic_has_no_articles(self):
        sparse = dict(self.FULL, innovation=[])
        self.assertIsNone(compose_intro(TOPICS, sparse, 1))

    def test_choice_reorders_the_named_topic_first(self):
        self.assertTrue(compose_intro(TOPICS, self.FULL, 2).startswith("B"))


if __name__ == "__main__":
    unittest.main()
