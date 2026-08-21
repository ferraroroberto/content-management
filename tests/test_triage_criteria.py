"""Pure tests for newsletter.triage.criteria — rules shape + priors derivation."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.triage import criteria as cr  # noqa: E402

_STATS = {
    "window": {"emails": 10, "editions": 2, "positives": 5, "first_email": "2025-08-08", "last_email": "2026-08-21"},
    "match": {"rate": 0.9},
    "senders": [
        {"sender": "Strong", "address": "strong@example.com", "emails": 10, "selected": 9, "hit_rate_per_email": 0.9,
         "hit_rate_per_link": 0.1, "stars": 2, "topics": {"innovation": 9}},
        {"sender": "Floor", "address": "news@alumni.example.edu", "emails": 25, "selected": 0, "hit_rate_per_email": 0.0,
         "hit_rate_per_link": 0.0, "stars": 0, "topics": {}},
        {"sender": "Rare", "address": "rare@example.com", "emails": 2, "selected": 1, "hit_rate_per_email": 0.5,
         "hit_rate_per_link": 0.2, "stars": 0, "topics": {"personal development": 1}},
    ],
    "domains": {"hbr.org": {"total": 3, "editions": 2, "max_per_edition": 2, "avg_per_edition": 1.5, "per_edition_hist": {}}},
    "stars_by_domain": {"hbr.org": 1},
    "topic_domains": {"leadership and management": {"hbr.org": 3}, "personal development": {}, "innovation": {}},
    "topic_authors": {}, "stars_by_author": {}, "must_read": {"by_author": {}},
}


class CriteriaTests(unittest.TestCase):
    def test_rules_carry_the_owner_caps(self) -> None:
        self.assertEqual(cr.RULES["edition"]["per_topic"], 8)
        self.assertEqual(cr.RULES["caps"]["hbr_per_edition"]["target"], 3)
        self.assertEqual(cr.RULES["caps"]["same_author_per_edition"]["target"], 2)
        for topic in cr.TOPICS:
            self.assertIn(topic, cr.RULES["topics"])
            self.assertTrue(cr.RULES["topics"][topic]["themes"])

    def test_sender_priors_split_ranked_and_floor(self) -> None:
        crit = cr.build_criteria(_STATS)
        ranked = [r["address"] for r in crit["sender_priors"]["ranked"]]
        floor = [r["address"] for r in crit["sender_priors"]["floor"]]
        self.assertEqual(ranked, ["strong@example.com"])      # ≥5 emails and picks
        self.assertEqual(floor, ["news@alumni.example.edu"])  # ≥20 emails, 0 picks
        self.assertEqual(crit["window"]["match_rate"], 0.9)

    def test_owner_overrides_are_merged(self) -> None:
        crit = cr.build_criteria(_STATS)
        ov = crit["sender_overrides"]
        self.assertEqual(ov["gustavorazzetti@substack.com"]["tier"], "never")   # paywalled — owner rule
        self.assertIn("paywall", cr.RULES)
        self.assertIn("cadence", cr.RULES)
        self.assertIn("feedback_loop", cr.RULES)

    def test_domain_priors_merge_topics_and_stars(self) -> None:
        crit = cr.build_criteria(_STATS)
        self.assertEqual(crit["domain_priors"][0],
                         {"domain": "hbr.org", "picks": 3, "editions": 2, "max_per_edition": 2, "stars": 1,
                          "topics": {"leadership and management": 3}})


if __name__ == "__main__":
    unittest.main()
