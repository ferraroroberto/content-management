"""check_ip ported pure helpers (post dates, source, row identity, recency)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from check_ip import process  # noqa: E402


class CheckIpProcessTests(unittest.TestCase):
    """The ported pure helpers — behaviour must match the sibling repo's."""

    def test_extract_linkedin_post_date(self):
        # Synthetic id built as (epoch_ms << 22) for 2023-06-15T12:00Z — exercises
        # the real arithmetic without embedding a third party's post id in a
        # public repo.
        url = "https://www.linkedin.com/feed/update/urn:li:activity:7075079494041600000/"
        self.assertEqual(process.extract_post_date(url), "2023-06-15 12:00:00 UTC")

    def test_extract_twitter_post_date(self):
        # Synthetic snowflake for 2022-06-15T12:00Z: (epoch_ms - twitter_epoch) << 22.
        url = "https://x.com/someone/status/1537042233553846272"
        self.assertEqual(process.extract_post_date(url), "2022-06-15 12:00:00 UTC")

    def test_extract_post_date_returns_none_off_platform(self):
        self.assertIsNone(process.extract_post_date("https://example.test/blog/post"))

    def test_extract_post_date_survives_a_junk_id(self):
        self.assertIsNone(process.extract_post_date("https://x.com/a/status/999999999999999999999999"))

    def test_identify_source(self):
        cases = {
            "https://www.linkedin.com/posts/someone_thing-activity-7392771581359513600-wLzA": "LinkedIn",
            "https://x.com/someone/status/1985821858432684125": "Twitter/X",
            "https://www.instagram.com/someone/p/Ab1Cd2Ef3Gh/": "Instagram",
            "https://example.test/gallery": None,
        }
        for url, expected in cases.items():
            self.assertEqual(process.identify_source(url), expected, url)

    def test_build_rows_treats_match_type_as_part_of_identity(self):
        payload = {"exact_matches": [{"link": "https://example.test/a", "title": "A"}]}
        existing: set = set()
        rows, added, skipped = process.build_rows(payload, "exact_matches", "i.png",
                                                  "https://i/x.png", "2026-01-01 00:00:00", existing)
        self.assertEqual((added, skipped), (1, 0))
        self.assertEqual(rows[0]["match_type"], "Exact Match")

        # Re-running the same search is a duplicate.
        payload = {"exact_matches": [{"link": "https://example.test/a", "title": "A"}]}
        _, added, skipped = process.build_rows(payload, "exact_matches", "i.png",
                                               "https://i/x.png", "2026-01-01 00:00:00", existing)
        self.assertEqual((added, skipped), (0, 1))

    def test_build_rows_no_longer_ingests_the_visual_match_section(self):
        """A new search stores no `Similar Match` row (issue #292).

        Asserted on a payload Lens really would return — a populated
        `visual_matches` list — so an empty result proves the section is
        dropped, not that the fixture had nothing in it.
        """
        payload = {"visual_matches": [
            {"link": "https://example.test/lookalike", "title": "someone else's chart"},
            {"link": "https://example.test/lookalike-2", "title": "another one"},
        ]}
        rows, added, skipped = process.build_rows(payload, "visual_matches", "i.png",
                                                  "https://i/x.png", "2026-01-01 00:00:00", set())
        self.assertEqual((rows, added, skipped), ([], 0, 0))
        self.assertNotIn("visual_matches", process.MATCH_TYPES)
        self.assertEqual(process.RETIRED_MATCH_TYPES["visual_matches"], "Similar Match",
                         "the retired section must stay named, not be deleted")

    def test_is_recently_processed_uses_the_high_linkedin_window(self):
        from datetime import datetime, timedelta
        recent = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        # 10 days ago: inside the 180-day standard window…
        self.assertTrue(process.is_recently_processed(recent, 0, 30, 6, 180))
        # …but past the 6-day window a heavily-reused image gets.
        self.assertFalse(process.is_recently_processed(recent, 99, 30, 6, 180))

    def test_is_recently_processed_treats_an_unparseable_date_as_never(self):
        self.assertFalse(process.is_recently_processed("not a date", 0, 30, 6, 180))
        self.assertFalse(process.is_recently_processed(None, 0, 30, 6, 180))


if __name__ == "__main__":
    unittest.main()
