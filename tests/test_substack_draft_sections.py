"""The newsletter -> Substack draft body must be structurally correct (issue #184,
extended by issue #233 for the "one must read" + "one book" sections).

``newsletter/substack_draft.py`` turns ``build_newsletter``'s grouped articles
into a Substack draft. The body is ProseMirror JSON, and a malformed body fails
silently — it produces a draft that *exists* but renders as plain text with dead
links, which you only notice by eye in the Substack editor.

Pinned here, all pure (no Notion, no Substack, no network):

* ``build_sections`` mirrors the HTML builder — same headings, same order, empty
  topics kept rather than dropped.
* ``build_section_nodes`` emits real ``bullet_list``/``list_item`` nodes with
  ``link`` marks, and treats article titles as **literal text**. The library's
  ``Post.from_markdown`` cannot express this shape (it folds bullets that follow
  a heading into the heading's own text node), which is why the nodes are built
  explicitly — this test is what stops a well-meaning refactor back to it.
* ``build_must_read_nodes``/``build_book_nodes`` match the hand-authored format
  of the live published editions.
* ``compose_title_line``/``compose_must_read_article`` pick the correct featured
  article for a given must-read permutation.
* ``newsletter.books.find_book_for_newsletter`` reads the right Notion columns
  and degrades cleanly when nothing is linked.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from newsletter.build_newsletter import (
    build_article_summary_map,
    extract_article_summary,
    must_read_first_index,
    top_article_by_topic,
)
from newsletter.substack_draft import (
    build_sections,
    compose_must_read_article,
    compose_title_line,
)
from planning.substack.api_client import build_book_nodes, build_must_read_nodes, build_section_nodes

TOPICS = ["personal development", "innovation", "leadership and management"]


def _article(name: str, url: str, topic: str, summary: str = "") -> dict:
    """A raw Notion article page shaped like ``build_newsletter`` expects."""
    return {
        "properties": {
            "article": {"type": "title", "title": [{"plain_text": name}]},
            "link": {"type": "url", "url": url},
            "topic": {"type": "select", "select": {"name": topic}},
            "star": {"type": "checkbox", "checkbox": False},
            "niche": {"type": "multi_select", "multi_select": []},
            "summary": {"type": "rich_text", "rich_text": [{"plain_text": summary}]} if summary else {"type": "rich_text", "rich_text": []},
        }
    }


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

    def test_lead_nodes_come_before_sections(self):
        lead = [{"type": "heading", "attrs": {"level": 2}, "content": []}]
        nodes = build_section_nodes([("Innovation", [])], lead_nodes=lead)
        self.assertEqual(nodes[0], lead[0])
        self.assertEqual(nodes[1]["type"], "heading")  # the Innovation section heading

    def test_trail_nodes_come_after_sections(self):
        trail = [{"type": "heading", "attrs": {"level": 2}, "content": []}]
        nodes = build_section_nodes([("Innovation", [])], trail_nodes=trail)
        self.assertEqual(nodes[-1], trail[0])

    def test_no_lead_or_trail_means_sections_only(self):
        nodes = build_section_nodes([("Innovation", [])])
        self.assertEqual(nodes[0]["type"], "heading")
        self.assertEqual(len(nodes), 1)


class BuildMustReadNodesTests(unittest.TestCase):
    def test_heading_mixes_plain_and_linked_runs(self):
        nodes = build_must_read_nodes("The IKEA effect", "https://x/ikea", "")
        heading = nodes[0]
        self.assertEqual(heading["type"], "heading")
        self.assertEqual(heading["attrs"]["level"], 2)
        runs = heading["content"]
        self.assertEqual(runs[0]["text"], 'One "must" for this week: ')
        self.assertNotIn("marks", runs[0])
        self.assertEqual(runs[1]["text"], "The IKEA effect")
        self.assertEqual(runs[1]["marks"], [{"type": "link", "attrs": {"href": "https://x/ikea"}}])

    def test_summary_lines_become_separate_paragraphs(self):
        summary = "Line one.\nLine two.\nLine three."
        nodes = build_must_read_nodes("Title", "https://x", summary)
        self.assertEqual([n["type"] for n in nodes[1:]], ["paragraph", "paragraph", "paragraph"])
        self.assertEqual(nodes[1]["content"][0]["text"], "Line one.")
        self.assertEqual(nodes[3]["content"][0]["text"], "Line three.")

    def test_empty_summary_means_heading_only(self):
        nodes = build_must_read_nodes("Title", "https://x", "")
        self.assertEqual(len(nodes), 1)


class BuildBookNodesTests(unittest.TestCase):
    def test_quoted_linked_title_and_author(self):
        nodes = build_book_nodes("How Will You Measure Your Life?", "https://gr/x", "Clayton Christensen")
        self.assertEqual(nodes[0]["type"], "heading")
        self.assertEqual(nodes[0]["content"][0]["text"], "One book")

        runs = nodes[1]["content"]
        self.assertEqual(runs[0]["text"], '"')
        self.assertNotIn("marks", runs[0])
        self.assertEqual(runs[1]["text"], "How Will You Measure Your Life?")
        self.assertEqual(runs[1]["marks"], [{"type": "link", "attrs": {"href": "https://gr/x"}}])
        self.assertEqual(runs[2]["text"], '" by Clayton Christensen.')

    def test_no_trailing_by_when_author_is_blank(self):
        nodes = build_book_nodes("Some Title", "https://x", "")
        runs = nodes[1]["content"]
        self.assertEqual(runs[2]["text"], '".')


class ComposeTitleLineTests(unittest.TestCase):
    FULL = {
        "personal development": [("A", "https://a")],
        "innovation": [("B", "https://b")],
        "leadership and management": [("C", "https://c")],
    }

    def test_none_when_no_choice_made(self):
        self.assertIsNone(compose_title_line(TOPICS, self.FULL, None))

    def test_none_when_a_topic_has_no_articles(self):
        sparse = dict(self.FULL, innovation=[])
        self.assertIsNone(compose_title_line(TOPICS, sparse, 1))

    def test_choice_reorders_the_named_topic_first(self):
        self.assertTrue(compose_title_line(TOPICS, self.FULL, 2).startswith("B"))


class ComposeMustReadArticleTests(unittest.TestCase):
    FULL = {
        "personal development": [("A", "https://a")],
        "innovation": [("B", "https://b")],
        "leadership and management": [("C", "https://c")],
    }

    def test_none_when_no_choice_made(self):
        self.assertIsNone(compose_must_read_article(TOPICS, self.FULL, {}, None))

    def test_none_when_a_topic_has_no_articles(self):
        sparse = dict(self.FULL, innovation=[])
        self.assertIsNone(compose_must_read_article(TOPICS, sparse, {}, 1))

    def test_permutation_1_features_the_first_topic(self):
        name, url, summary = compose_must_read_article(TOPICS, self.FULL, {}, 1)
        self.assertEqual((name, url), ("A", "https://a"))
        self.assertEqual(summary, "")

    def test_permutation_2_features_the_second_topic(self):
        name, url, _summary = compose_must_read_article(TOPICS, self.FULL, {}, 2)
        self.assertEqual((name, url), ("B", "https://b"))

    def test_summary_looked_up_by_name_and_url(self):
        summaries = {("A", "https://a"): "A great read."}
        _name, _url, summary = compose_must_read_article(TOPICS, self.FULL, summaries, 1)
        self.assertEqual(summary, "A great read.")


class MustReadFirstIndexTests(unittest.TestCase):
    def test_matches_the_first_topic_for_permutation_1(self):
        self.assertEqual(must_read_first_index(1), 0)

    def test_matches_the_second_topic_for_permutation_2(self):
        self.assertEqual(must_read_first_index(2), 1)

    def test_matches_the_third_topic_for_permutation_3(self):
        self.assertEqual(must_read_first_index(3), 2)


class TopArticleByTopicTests(unittest.TestCase):
    def test_keeps_url_alongside_name(self):
        grouped = {t: [("A", "https://a")] for t in TOPICS}
        top = top_article_by_topic(TOPICS, grouped)
        self.assertEqual(top, [("A", "https://a")] * 3)

    def test_none_when_a_topic_is_empty(self):
        grouped = {t: [("A", "https://a")] for t in TOPICS}
        grouped["innovation"] = []
        self.assertIsNone(top_article_by_topic(TOPICS, grouped))


class ArticleSummaryExtractionTests(unittest.TestCase):
    def test_extracts_the_summary_rich_text(self):
        art = _article("Title", "https://x", "innovation", summary="Line one.\nLine two.")
        self.assertEqual(extract_article_summary(art), "Line one.\nLine two.")

    def test_empty_when_no_summary_property(self):
        art = {"properties": {}}
        self.assertEqual(extract_article_summary(art), "")

    def test_build_article_summary_map_keys_by_name_and_url(self):
        articles = [
            _article("A", "https://a", "innovation", summary="Summary A"),
            _article("B", "https://b", "innovation", summary=""),
        ]
        summaries = build_article_summary_map(articles)
        self.assertEqual(summaries, {("A", "https://a"): "Summary A"})


class FindBookForNewsletterTests(unittest.TestCase):
    """``newsletter/books.py`` — mocked Notion, no network."""

    def _row(self, title: str, author: str, url: str) -> dict:
        return {
            "properties": {
                "title to copy": {"type": "formula", "formula": {"type": "string", "string": title}},
                "author to copy": {"type": "formula", "formula": {"type": "string", "string": author}},
                "url": {"type": "url", "url": url},
            }
        }

    def test_returns_none_when_no_book_linked(self):
        from newsletter.books import find_book_for_newsletter

        with patch("newsletter.books.load_full_config", return_value={
            "notion": {"api_token": "tok"},
            "newsletter_archive": {"books_db_id": "db123"},
        }), patch("newsletter.books.init_notion_client", return_value=object()), \
             patch("newsletter.books.query_rows_by_filter", return_value=[]):
            self.assertIsNone(find_book_for_newsletter("page-id"))

    def test_returns_title_author_url_from_the_first_match(self):
        from newsletter.books import find_book_for_newsletter

        row = self._row("How Will You Measure Your Life?", "Clayton Christensen", "https://gr/x")
        with patch("newsletter.books.load_full_config", return_value={
            "notion": {"api_token": "tok"},
            "newsletter_archive": {"books_db_id": "db123"},
        }), patch("newsletter.books.init_notion_client", return_value=object()), \
             patch("newsletter.books.query_rows_by_filter", return_value=[row]):
            book = find_book_for_newsletter("page-id")
        self.assertEqual(book, {
            "title": "How Will You Measure Your Life?",
            "author": "Clayton Christensen",
            "url": "https://gr/x",
        })

    def test_returns_none_when_title_or_url_missing(self):
        from newsletter.books import find_book_for_newsletter

        row = self._row("", "Clayton Christensen", "https://gr/x")
        with patch("newsletter.books.load_full_config", return_value={
            "notion": {"api_token": "tok"},
            "newsletter_archive": {"books_db_id": "db123"},
        }), patch("newsletter.books.init_notion_client", return_value=object()), \
             patch("newsletter.books.query_rows_by_filter", return_value=[row]):
            self.assertIsNone(find_book_for_newsletter("page-id"))


if __name__ == "__main__":
    unittest.main()
