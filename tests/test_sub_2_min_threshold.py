"""The sub-2-minute cutoff lives in phrases.json and drives all three consumers (issue #374)."""

from __future__ import annotations

import copy
import unittest

from engagement.classify import local_model
from engagement.classify.rules import _score_comment, load_phrases
from engagement.reputation.update import _aggregate

POST_AT = "2026-10-07T08:00:00Z"
COMMENT = {"text": "x", "post_posted_at": POST_AT, "posted_at": "2026-10-07T08:03:00Z"}  # 180 s later


def _phrases(cutoff: int) -> dict:
    phrases = copy.deepcopy(load_phrases())
    phrases["rules"]["sub_2_min_max_seconds"] = cutoff
    return phrases


class Sub2MinThresholdTests(unittest.TestCase):
    def test_shipped_cutoff_is_two_minutes(self) -> None:
        self.assertEqual(load_phrases()["rules"]["sub_2_min_max_seconds"], 120)

    def test_rules_score_follows_the_configured_cutoff(self) -> None:
        for cutoff, expected in ((120, False), (180, True)):
            with self.subTest(cutoff=cutoff):
                _, reasons = _score_comment(COMMENT, None, set(), _phrases(cutoff))
                self.assertEqual(any(r["rule"] == "sub_2_min" for r in reasons), expected)

    def test_model_feature_follows_the_configured_cutoff(self) -> None:
        for cutoff, expected in ((120, 0), (180, 1)):
            with self.subTest(cutoff=cutoff):
                feats = local_model.featurize_one(COMMENT, set(), _phrases(cutoff))
                self.assertEqual(feats["sub_2_min"], expected)

    def test_reputation_counter_follows_the_configured_cutoff(self) -> None:
        for cutoff, expected in ((120, 0), (180, 1)):
            with self.subTest(cutoff=cutoff):
                counters, *_ = _aggregate([COMMENT], set(), _phrases(cutoff))
                self.assertEqual(counters["sub_2_min_count"], expected)


if __name__ == "__main__":
    unittest.main()
