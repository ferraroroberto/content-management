"""Archive classifier: no guessed topic (issue #284).

Two invalid model replies must produce ``None`` — not the old
``personal development`` fallback — and the pipeline must leave such an
article unwritten with the tab open. Also pins the tolerant matching shared
with triage scoring (``**innovation**``, ``Topic: leadership …``) and the fact
that both modules use the *same* function object. No network, no Chrome.
"""

from __future__ import annotations

import logging
import sys
import unittest
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter import classifier, pipeline, topics  # noqa: E402
from newsletter.triage import score as sc  # noqa: E402

CLASSIFY_KW = dict(base_url="http://127.0.0.1:8000", model="m",
                   title="Some title", body_text="x" * 500)


class ClassifyTests(unittest.TestCase):
    def _calls(self, *replies: str):
        return unittest.mock.patch.object(classifier.llm, "call",
                                          side_effect=list(replies))

    def test_two_invalid_replies_give_none(self) -> None:
        """The bug: this returned 'personal development' before the fix."""
        with self._calls("I'm not sure.", "<no topic>") as call:
            with self.assertLogs("newsletter_archive.classifier", logging.WARNING):
                self.assertIsNone(classifier.classify(**CLASSIFY_KW))
        self.assertEqual(call.call_count, 2)

    def test_second_attempt_can_still_succeed(self) -> None:
        with self._calls("<no topic>", "innovation"):
            self.assertEqual(classifier.classify(**CLASSIFY_KW), "innovation")

    def test_decorated_replies_accepted_on_first_attempt(self) -> None:
        for reply, expected in (("**innovation**", "innovation"),
                                ("Topic: leadership and management",
                                 "leadership and management"),
                                ("innovation (AI tooling)", "innovation"),
                                ("personal development.", "personal development")):
            with self.subTest(reply=reply):
                with self._calls(reply) as call:
                    self.assertEqual(classifier.classify(**CLASSIFY_KW), expected)
                self.assertEqual(call.call_count, 1)


class SharedMatcherTests(unittest.TestCase):
    """One matcher serves the archive classifier and triage scoring."""

    def test_triage_score_uses_the_shared_matcher(self) -> None:
        self.assertIs(sc.match_topic, topics.match_topic)
        self.assertEqual(tuple(sc.TOPICS), tuple(topics.TOPICS))

    def test_triage_matching_behaviour_unchanged(self) -> None:
        # The exact table the pre-move newsletter/triage/score.py:_topic produced.
        cases = {
            "leadership and management": "leadership and management",
            "Leadership": "leadership and management",
            "management": "leadership and management",
            "personal development": "personal development",
            "personal growth": "personal development",
            "innovation": "innovation",
            "AI": "innovation",
            "": None,
            None: None,
            "sports": None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(topics.match_topic(raw), expected)


class _Cache:
    def find_article(self, url: str) -> None:
        return None


class PipelineUnclassifiedTests(unittest.TestCase):
    ARCHIVE_CFG = {"llm_hub_base_url": "http://127.0.0.1:8000", "llm_model": "m",
                   "min_body_chars": 200, "articles_db_id": "a",
                   "connections_db_id": "c", "newsletter_db_id": "n",
                   "topic_to_rollup": {}, "newsletter_category_cap": 8,
                   "fuzzy_author_threshold": 88}

    def test_unclassified_article_is_not_written(self) -> None:
        art = unittest.mock.Mock(title="T", author="A", body_text="x" * 500)
        with unittest.mock.patch.object(pipeline.extractor, "extract", return_value=art), \
             unittest.mock.patch.object(pipeline.classifier, "classify", return_value=None), \
             unittest.mock.patch.object(pipeline.notion_io, "create_article") as create, \
             unittest.mock.patch.object(pipeline.notion_io, "pick_newsletter") as pick, \
             unittest.mock.patch.object(pipeline.summarizer, "summarize") as summarize:
            result = pipeline.process_url(
                url="https://example.com/a", page=unittest.mock.Mock(),
                archive_cfg=self.ARCHIVE_CFG, client=unittest.mock.Mock(),
                cache=_Cache(), write=True,
                logger=logging.getLogger("newsletter_archive.test"),
            )
        self.assertEqual(result, pipeline.UNCLASSIFIED)
        create.assert_not_called()
        pick.assert_not_called()
        summarize.assert_not_called()

    def test_run_batch_counts_it_and_keeps_the_tab_open(self) -> None:
        tab = unittest.mock.Mock(url="https://example.com/a", page=unittest.mock.Mock())
        cfg = {"newsletter_archive": dict(self.ARCHIVE_CFG, chrome_debug_port=9222,
                                          skip_url_substrings=[]),
               "notion": {"api_token": "t"}}
        with unittest.mock.patch.object(pipeline, "setup_logger",
                                        return_value=logging.getLogger("newsletter_archive")), \
             unittest.mock.patch.object(pipeline, "load_config", return_value=cfg), \
             unittest.mock.patch.object(pipeline.llm, "health_check", return_value=True), \
             unittest.mock.patch.object(pipeline.notion_io, "init_client"), \
             unittest.mock.patch.object(pipeline.notion_io, "hydrate_cache"), \
             unittest.mock.patch.object(pipeline.chrome_tabs, "connect"), \
             unittest.mock.patch.object(pipeline.chrome_tabs, "close_browser"), \
             unittest.mock.patch.object(pipeline.chrome_tabs, "list_tabs", return_value=[tab]), \
             unittest.mock.patch.object(pipeline.chrome_tabs, "should_skip", return_value=False), \
             unittest.mock.patch.object(pipeline, "process_url",
                                        return_value=pipeline.UNCLASSIFIED):
            with self.assertLogs("newsletter_archive", logging.INFO) as logs:
                rc = pipeline.run_batch(write=True)
        self.assertEqual(rc, 0)
        self.assertTrue(any("1 unclassified" in m for m in logs.output),
                        f"no unclassified count in the summary: {logs.output}")
        self.assertTrue(any("0 archived, 0 skipped" in m for m in logs.output))
        tab.page.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
