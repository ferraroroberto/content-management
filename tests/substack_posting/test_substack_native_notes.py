"""Native note-engagement records must match the Playwright scraper's shape (issue #185).

``reporting/scrape_client/substack_native.py`` reads note engagement over the
private HTTP API instead of screen-scraping the rendered feed. It is selected by
a ``source`` flag and writes into the same Postgres/Notion columns as the
Playwright path, so a field that silently changes meaning corrupts history
rather than failing loudly.

Pinned here, all pure (no Substack, no network) against captured payload shapes:

* the record is field-for-field what ``_scrape_note_permalink`` returned —
  ``post_id`` is the same ``/@handle/note/c-<id>`` permalink the DB already keys on;
* engagement maps to ``reaction_count`` / ``children_count`` / ``restacks``, the
  same three values Substack's own feed code renders in the note toolbar;
* ``posted_at`` is converted UTC -> **local** before the date is taken. Slicing
  the UTC string instead would shift late-evening notes onto the wrong day and
  silently break the consolidator's ``posted_at = date - 1 day`` match;
* teaser detection keys on a ``/p/`` attachment, so an ordinary note carrying an
  outbound link is *not* dropped.

Run: & .\\.venv\\Scripts\\python.exe -m unittest discover tests -v
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from reporting.scrape_client.substack_native import (
    note_is_teaser,
    note_posted_date,
    note_record,
)

HANDLE = "someone"

# Shapes copied from live payloads during the issue #185 spike (ids/urls scrubbed).
IMAGE_ATT = {
    "id": "aaaaaaaa-0000-0000-0000-000000000000",
    "type": "image",
    "imageUrl": "https://example.invalid/img_3240x3240.png",
    "imageWidth": 3240,
    "imageHeight": 3240,
}
VIDEO_ATT = {
    "id": "bbbbbbbb-0000-0000-0000-000000000000",
    "type": "video",
    "media_upload_id": "cccccccc-0000-0000-0000-000000000000",
    "mediaUpload": {"media_type": "video", "state": "transcoded", "duration": 72.2},
}
OUTBOUND_LINK_ATT = {
    "id": "dddddddd-0000-0000-0000-000000000000",
    "type": "link",
    "linkMetadata": {
        "url": "https://example.invalid/inspiring-conversations/guest",
        "host": "example.invalid",
        "title": "A conversation",
    },
}
TEASER_LINK_ATT = {
    "id": "eeeeeeee-0000-0000-0000-000000000000",
    "type": "link",
    "linkMetadata": {
        "url": "https://example.substack.com/p/the-weekly-edition",
        "host": "example.substack.com",
        "title": "The weekly edition",
    },
}


def _comment(**overrides) -> dict:
    base = {
        "id": 301901974,
        "type": "feed",
        "date": "2026-07-26T10:03:14.591Z",
        "body": "A daily note.",
        "reaction_count": 18,
        "children_count": 2,
        "restacks": 5,
        "attachments": [IMAGE_ATT],
    }
    base.update(overrides)
    return base


class NoteRecordTests(unittest.TestCase):
    def test_record_has_exactly_the_scraper_keys(self):
        rec = note_record(_comment(), HANDLE)
        self.assertEqual(
            set(rec),
            {"post_id", "posted_at", "is_video", "num_likes",
             "num_comments", "num_reshares", "is_teaser"},
        )

    def test_post_id_is_the_permalink_the_db_keys_on(self):
        rec = note_record(_comment(), HANDLE)
        self.assertEqual(
            rec["post_id"], f"https://substack.com/@{HANDLE}/note/c-301901974"
        )

    def test_engagement_maps_to_the_fields_the_feed_ui_renders(self):
        rec = note_record(_comment(), HANDLE)
        self.assertEqual(rec["num_likes"], 18)       # reaction_count
        self.assertEqual(rec["num_comments"], 2)     # children_count
        self.assertEqual(rec["num_reshares"], 5)     # restacks

    def test_missing_engagement_counts_become_zero_not_none(self):
        rec = note_record(
            _comment(reaction_count=None, children_count=None, restacks=None), HANDLE
        )
        self.assertEqual(
            (rec["num_likes"], rec["num_comments"], rec["num_reshares"]), (0, 0, 0)
        )

    def test_video_attachment_sets_is_video(self):
        self.assertEqual(note_record(_comment(attachments=[VIDEO_ATT]), HANDLE)["is_video"], 1)
        self.assertEqual(note_record(_comment(), HANDLE)["is_video"], 0)

    def test_is_video_is_an_int_not_a_bool(self):
        # The column is numeric; the Playwright path wrote 1/0.
        self.assertIsInstance(note_record(_comment(attachments=[VIDEO_ATT]), HANDLE)["is_video"], int)

    def test_note_with_no_attachments_is_handled(self):
        rec = note_record(_comment(attachments=[]), HANDLE)
        self.assertEqual(rec["is_video"], 0)
        self.assertFalse(rec["is_teaser"])


class PostedDateTests(unittest.TestCase):
    def test_utc_is_converted_to_local_before_taking_the_date(self):
        # 23:30 UTC is already the *next* day anywhere east of UTC. Whatever the
        # runner's zone, the answer must equal the locally-converted date --
        # never a naive slice of the UTC string.
        ts = "2026-07-26T23:30:00.000Z"
        expected = (
            datetime(2026, 7, 26, 23, 30, tzinfo=timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d")
        )
        self.assertEqual(note_posted_date(ts), expected)

    def test_typical_daily_note_timestamp(self):
        ts = "2026-07-26T10:03:14.591Z"
        expected = (
            datetime(2026, 7, 26, 10, 3, 14, tzinfo=timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d")
        )
        self.assertEqual(note_posted_date(ts), expected)

    def test_returns_none_rather_than_raising_on_junk(self):
        self.assertIsNone(note_posted_date(None))
        self.assertIsNone(note_posted_date(""))
        self.assertIsNone(note_posted_date("not a date"))


class TeaserTests(unittest.TestCase):
    def test_p_link_attachment_is_a_teaser(self):
        self.assertTrue(note_is_teaser([TEASER_LINK_ATT]))

    def test_plain_outbound_link_is_not_a_teaser(self):
        # Regression guard: keying on "has a link attachment" instead of on the
        # /p/ URL would silently drop ordinary daily notes.
        self.assertFalse(note_is_teaser([OUTBOUND_LINK_ATT]))
        self.assertFalse(note_record(_comment(attachments=[OUTBOUND_LINK_ATT]), HANDLE)["is_teaser"])

    def test_post_attachment_is_a_teaser(self):
        self.assertTrue(note_is_teaser([{"type": "post"}]))

    def test_image_and_video_notes_are_not_teasers(self):
        self.assertFalse(note_is_teaser([IMAGE_ATT]))
        self.assertFalse(note_is_teaser([VIDEO_ATT]))

    def test_empty_and_none_attachments(self):
        self.assertFalse(note_is_teaser([]))
        self.assertFalse(note_is_teaser(None))


if __name__ == "__main__":
    unittest.main()
