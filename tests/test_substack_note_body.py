"""A Note's ProseMirror body must match what Substack's composer sends (issue #185).

``planning/substack/api_client.py::publish_note`` posts a Note over the HTTP API
instead of driving the composer. The body is ProseMirror JSON and the endpoint
accepts a malformed-but-parseable doc happily, so a wrong shape publishes a
mangled Note **to the whole follower list** — irreversibly, since a Note has no
draft state. That makes this shape worth pinning hard.

Pinned here, all pure (no Substack, no network):

* the ``doc`` wrapper carries ``schemaVersion: v1`` and a null title, and blank
  lines split into separate ``paragraph`` nodes — both verified against the
  stored ``body_json`` of already-published notes;
* body text is inserted **literally**, never markdown-parsed (it comes from a
  Notion column and legitimately contains ``*``, ``[`` and backticks);
* an empty body raises rather than publishing a blank Note.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest

from planning.substack.api_client import build_note_body_json, note_permalink


class NoteBodyJsonTests(unittest.TestCase):
    def test_doc_wrapper_matches_substacks_own_shape(self):
        doc = build_note_body_json("Hello.")
        self.assertEqual(doc["type"], "doc")
        self.assertEqual(doc["attrs"], {"schemaVersion": "v1", "title": None})

    def test_single_line_is_one_paragraph(self):
        doc = build_note_body_json("Shaming others is never acceptable.")
        self.assertEqual(
            doc["content"],
            [{
                "type": "paragraph",
                "content": [{"type": "text",
                             "text": "Shaming others is never acceptable."}],
            }],
        )

    def test_blank_line_splits_into_separate_paragraphs(self):
        doc = build_note_body_json("First para.\n\nSecond para.")
        self.assertEqual(len(doc["content"]), 2)
        self.assertEqual([p["type"] for p in doc["content"]], ["paragraph", "paragraph"])
        self.assertEqual(doc["content"][1]["content"][0]["text"], "Second para.")

    def test_multiple_blank_lines_do_not_create_empty_paragraphs(self):
        doc = build_note_body_json("A.\n\n\n\nB.\n\n   \n\nC.")
        self.assertEqual(len(doc["content"]), 3)
        for para in doc["content"]:
            self.assertTrue(para["content"][0]["text"].strip())

    def test_body_text_is_literal_not_markdown(self):
        # A real editorial body can contain any of these; markdown-parsing it
        # would silently drop or restyle characters the author intended.
        raw = "Why *args and [brackets] break `parsers` -- 100% of the time"
        doc = build_note_body_json(raw)
        self.assertEqual(doc["content"][0]["content"][0]["text"], raw)
        self.assertEqual(doc["content"][0]["content"][0], {"type": "text", "text": raw})

    def test_no_link_marks_are_invented(self):
        doc = build_note_body_json("See https://example.invalid for more.")
        node = doc["content"][0]["content"][0]
        self.assertNotIn("marks", node)

    def test_surrounding_whitespace_is_trimmed(self):
        doc = build_note_body_json("\n\n  Padded.  \n\n")
        self.assertEqual(len(doc["content"]), 1)
        self.assertEqual(doc["content"][0]["content"][0]["text"], "Padded.")

    def test_empty_body_raises_instead_of_publishing_blank(self):
        for empty in ("", "   ", "\n\n", None):
            with self.subTest(empty=empty):
                with self.assertRaises(ValueError):
                    build_note_body_json(empty)


class NotePermalinkTests(unittest.TestCase):
    def test_permalink_matches_the_reporting_post_id_format(self):
        # Must equal what substack_native.note_record builds, or the editorial
        # post_url and the posts table would key on different strings.
        self.assertEqual(
            note_permalink("someone", 123456789),
            "https://substack.com/@someone/note/c-123456789",
        )

    def test_accepts_string_ids(self):
        self.assertEqual(
            note_permalink("someone", "123456789"),
            "https://substack.com/@someone/note/c-123456789",
        )


if __name__ == "__main__":
    unittest.main()
