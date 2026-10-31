"""The post-scrape classify chain keeps a scrape green on a classifier error, but not on a version-skewed model.

``local_model`` raises ``ModelVersionMismatch`` on purpose so a scheduled scrape aborts
instead of leaving every new row ``unknown``; the chain's blanket ``except Exception``
used to swallow it into one warning and exit 0.

Run: & .\\.venv\\Scripts\\python.exe -m pytest tests/linkedin/test_linkedin_scrape_classify_chain.py -q
"""

from __future__ import annotations

import unittest
from unittest import mock

import engagement.linkedin.scrape_comments as scrape_comments
from engagement.classify.local_model import ModelVersionMismatch

COMMENTS = [{"comment_id": "c1"}]


def persist(classify_effect):
    """Run ``_persist`` with the Supabase writes faked and ``classify_pending`` raising/returning ``classify_effect``."""
    classify = mock.MagicMock(side_effect=classify_effect) if isinstance(classify_effect, Exception)         else mock.MagicMock(return_value=classify_effect)
    with mock.patch.object(scrape_comments, "migrate_fallback_ids_to_urn"),             mock.patch.object(scrape_comments, "upsert_commenters"),             mock.patch.object(scrape_comments, "upsert_comments") as upsert,             mock.patch.object(scrape_comments, "mark_replied_as_ignored", return_value=0),             mock.patch("engagement.classify.rules.classify_pending", classify):
        written = scrape_comments._persist(list(COMMENTS), {})
    return written, upsert


class PersistClassifyChainTests(unittest.TestCase):
    def test_version_mismatch_aborts_the_run(self) -> None:
        with self.assertRaises(ModelVersionMismatch):
            persist(ModelVersionMismatch("trained on 1.5, runtime 1.7"))

    def test_other_classifier_errors_do_not_mask_the_scrape(self) -> None:
        with self.assertLogs(level="WARNING") as logs:
            written, upsert = persist(RuntimeError("hub down"))
        self.assertEqual(written, COMMENTS)
        upsert.assert_called_once()
        self.assertTrue(any("post-scrape classify failed" in line for line in logs.output))

    def test_a_clean_classify_returns_the_comments(self) -> None:
        written, _ = persist({"classified": 1})
        self.assertEqual(written, COMMENTS)


if __name__ == "__main__":
    unittest.main()
