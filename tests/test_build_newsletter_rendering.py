"""Article titles and links render as text, never as markup.

``newsletter/build_newsletter.py`` assembles the weekly HTML from the Notion
article rows the archive step wrote. Both fields on those rows are copied
verbatim from an arbitrary third-party page — the title from the page's own
``<title>`` / readability short-title, the link from the tab that was open —
so neither is authored by us and neither may be treated as markup. The built
file is then written to ``results/newsletter/`` and opened in the operator's
own browser, which makes "renders as text" a property worth pinning rather
than assuming.

Pinned here, all pure (no Notion, no network, no browser):

* a title carrying markup characters survives as escaped text — no element
  from the input appears in the output;
* a link carrying a quote cannot terminate the ``href`` attribute it sits in;
* a link whose scheme is not ``http``/``https`` keeps its title readable but
  is not emitted as a clickable ``href``;
* ordinary titles and links are unchanged, so the escaping is not paid for
  with a mangled newsletter.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest

from newsletter.build_newsletter import generate_complete_html, generate_html_lists

TOPICS = ["personal development", "innovation", "leadership and management"]


def _grouped(*articles: tuple[str, str]) -> dict[str, list[tuple[str, str]]]:
    """One topic carrying ``articles``; the other two empty."""
    return {
        "personal development": list(articles),
        "innovation": [],
        "leadership and management": [],
    }


class ArticleTitleRenderingTests(unittest.TestCase):
    def test_markup_in_a_title_is_escaped_not_emitted(self):
        title = '"><img src=x onerror=alert(1)>Ten lessons'
        out = generate_html_lists(_grouped((title, "https://example.com/a")), TOPICS)
        # The element from the input must not survive as an element, and the
        # leading quote must not survive as a real quote — both become text.
        self.assertNotIn("<img", out)
        self.assertNotIn('">', out.replace('<a href="https://example.com/a">', ""))
        self.assertIn("&lt;img", out)
        self.assertIn("&quot;&gt;", out)

    def test_ampersand_in_a_title_becomes_an_entity(self):
        out = generate_html_lists(_grouped(("Tools & rituals", "https://example.com/a")), TOPICS)
        self.assertIn("Tools &amp; rituals", out)
        self.assertNotIn("Tools & rituals", out)

    def test_a_plain_title_is_unchanged(self):
        out = generate_html_lists(_grouped(("Ten lessons on focus", "https://example.com/a")), TOPICS)
        self.assertIn('<a href="https://example.com/a">Ten lessons on focus</a>', out)


class ArticleLinkRenderingTests(unittest.TestCase):
    def test_a_quote_in_a_link_cannot_close_the_href(self):
        url = 'https://example.com/a" onmouseover="alert(1)'
        out = generate_html_lists(_grouped(("Ten lessons", url)), TOPICS)
        # The quote from the input must not survive as a real quote, so no
        # second attribute can be grafted onto the anchor.
        self.assertNotIn('" onmouseover=', out)
        self.assertIn("&quot;", out)
        # Exactly one attribute-closing quote after the href opener.
        anchor = out[out.index('<a href="'):]
        self.assertEqual(anchor[len('<a href="'):].count('"'), 1)

    def test_an_off_list_scheme_is_not_linked(self):
        out = generate_html_lists(_grouped(("Ten lessons", "javascript:alert(1)")), TOPICS)
        self.assertNotIn("<a href=", out)
        self.assertIn("Ten lessons", out)

    def test_https_and_http_links_stay_clickable(self):
        for url in ("https://example.com/a", "http://example.com/b"):
            with self.subTest(url=url):
                out = generate_html_lists(_grouped(("Ten lessons", url)), TOPICS)
                self.assertIn(f'<a href="{url}">', out)


class CompleteDocumentTests(unittest.TestCase):
    def test_the_full_document_carries_the_escaped_form(self):
        doc = generate_complete_html(
            _grouped(('<script>alert(1)</script>', "https://example.com/a")), TOPICS
        )
        self.assertNotIn("<script>alert(1)</script>", doc)
        self.assertIn("&lt;script&gt;", doc)
        # The document's own template markup is untouched.
        self.assertIn("<!DOCTYPE html>", doc)
        self.assertIn("<h2>Personal development</h2>", doc)


if __name__ == "__main__":
    unittest.main()
