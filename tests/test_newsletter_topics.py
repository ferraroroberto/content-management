"""Topic matcher: the ``ai`` alias must match a word, not two letters (issue #297).

Before the fix ``match_topic`` resolved any string containing ``ai`` anywhere
(``unavailable``, ``email``, a model refusal mentioning "available text") to
``innovation``, while missing ``artificial intelligence``. The differential
test pins that nothing outside the ``ai`` alias changed. No network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter import topics  # noqa: E402


def _pre_297_match_topic(v: Any) -> Optional[str]:
    """The matcher as merged in #284, kept verbatim as the differential baseline."""
    aliases = {"leadership": topics.TOPICS[0], "management": topics.TOPICS[0],
               "personal": topics.TOPICS[1], "innovation": topics.TOPICS[2],
               "ai": topics.TOPICS[2]}
    s = (str(v) if v is not None else "").strip().lower()
    for t in topics.TOPICS:
        if s == t or s.startswith(t[:10]):
            return t
    for k, t in aliases.items():
        if k in s:
            return t
    return None


class AiAliasTests(unittest.TestCase):
    def test_words_merely_containing_ai_give_none(self) -> None:
        for raw in ("unavailable", "uncertain", "maintenance", "email", "retail",
                    "domain", "explain", "failed", "chain of command",
                    "I cannot determine this from the available text"):
            with self.subTest(raw=raw):
                self.assertIsNone(topics.match_topic(raw))

    def test_ai_as_a_word_still_means_innovation(self) -> None:
        for raw in ("AI tooling", "AI and automation", "ai", "AI",
                    "artificial intelligence", "Artificial Intelligence in HR",
                    "AI-powered teams", "**AI**"):
            with self.subTest(raw=raw):
                self.assertEqual(topics.match_topic(raw), "innovation")


class DifferentialTests(unittest.TestCase):
    """Outside the ``ai`` alias, behaviour is identical to the pre-fix matcher."""

    CORPUS = (
        "leadership and management", "Leadership & Management", "Leadership",
        "management", "Topic: leadership and management", "team management tips",
        "personal development", "Personal Development", "personal growth",
        "personal development.", "innovation", "**innovation**",
        "innovation (AI tooling)", "Innovation!", "open innovation",
        "", None, "sports", "none of the above", "<no topic>", "I'm not sure.",
        "leadership in personal life", "management of innovation",
        "personal d", "innovations", "personally", "self-management",
    )

    def test_no_change_outside_the_ai_alias(self) -> None:
        for raw in self.CORPUS:
            with self.subTest(raw=raw):
                self.assertEqual(topics.match_topic(raw), _pre_297_match_topic(raw))


if __name__ == "__main__":
    unittest.main()
