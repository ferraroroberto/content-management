"""Topic classifier — outputs one of three canonical labels.

The three valid labels match the ``topic`` select options on the Notion
articles + connections databases.
"""

from __future__ import annotations

import logging

from archive.newsletter import llm

logger = logging.getLogger("newsletter_archive.classifier")

VALID = {"personal development", "innovation", "leadership and management"}
FALLBACK = "personal development"

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
             snippet_chars: int = 1500) -> str:
    snippet = (body_text or "")[:snippet_chars]
    prompt = PROMPT_TEMPLATE.format(title=title or "", snippet=snippet)
    for attempt in (1, 2):
        raw = llm.call(base_url=base_url, model=model, prompt=prompt, max_tokens=32)
        candidate = raw.strip().strip(".").strip('"').strip("'").lower()
        if candidate in VALID:
            return candidate
        logger.warning("⚠️ Classifier returned '%s' (attempt %d); valid=%s",
                       raw, attempt, sorted(VALID))
    logger.error("❌ Classifier failed twice — falling back to '%s'", FALLBACK)
    return FALLBACK
