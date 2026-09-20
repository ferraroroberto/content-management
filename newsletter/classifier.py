"""Topic classifier — outputs one of three canonical labels, or nothing.

The three valid labels match the ``topic`` select options on the Notion
articles + connections databases; they live in :mod:`newsletter.topics`
together with the tolerant matcher this module shares with triage scoring.

When no attempt yields a valid label the classifier returns ``None`` rather
than a guess: an unclassifiable article is an explicit unknown for the caller
to handle, never a silent ``personal development``.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from newsletter import llm
from newsletter.topics import VALID, match_topic

logger = logging.getLogger("newsletter_archive.classifier")

PROMPT_TEMPLATE = """You classify articles into exactly one of these three topics:

personal development
innovation
leadership and management

Reply with ONLY the topic name in lowercase, on a single line, no quotes,
no punctuation, no preamble.

Title: {title}

First chunk of the article:
{snippet}
"""


def classify(*, base_url: str, model: str, title: str, body_text: str,
             snippet_chars: int = 1500) -> Optional[str]:
    """Classify one article, or return ``None`` when no attempt produced a label."""
    snippet = (body_text or "")[:snippet_chars]
    prompt = PROMPT_TEMPLATE.format(title=title or "", snippet=snippet)
    replies: List[str] = []
    for attempt in (1, 2):
        raw = llm.call(base_url=base_url, model=model, prompt=prompt, max_tokens=32)
        replies.append(raw)
        topic = match_topic(raw)
        if topic is not None:
            return topic
        logger.warning("⚠️ Classifier returned %r (attempt %d); valid=%s",
                       raw, attempt, sorted(VALID))
    logger.warning("❌ Classifier produced no valid topic in %d attempts — "
                   "leaving the article unclassified. Raw replies: %s",
                   len(replies), replies)
    return None
