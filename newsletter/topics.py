"""The three canonical newsletter topics and one tolerant matcher for them.

The labels mirror the ``topic`` select options on the Notion articles +
connections databases exactly — there is no fourth option, and
``newsletter_archive.topic_to_rollup`` in ``config.json`` maps exactly these
three to their newsletter rollups.

:func:`match_topic` is shared by every consumer that has to turn a free-text
model reply into one of those labels — the archive classifier
(``newsletter.classifier``) and triage scoring (``newsletter.triage.score``).
It is deliberately tolerant (``**innovation**``, ``Topic: leadership and
management``) and returns ``None`` when nothing fits, so an unmatched reply
stays an explicit unknown instead of becoming a guess.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Pattern, Tuple

TOPICS: Tuple[str, ...] = ("leadership and management", "personal development", "innovation")
VALID = set(TOPICS)

# Checked in order; the first alias found anywhere in the reply wins. The long
# aliases stay plain substrings (``personally`` -> personal development), but
# ``ai`` is two letters and must be a whole word — as a substring it matched
# ``unavailable``, ``email`` and model refusals about "the available text".
_ALIASES: Tuple[Tuple[Pattern[str], str], ...] = tuple(
    (re.compile(p), t) for p, t in (
        ("leadership", TOPICS[0]), ("management", TOPICS[0]), ("personal", TOPICS[1]),
        ("innovation", TOPICS[2]), (r"\bai\b", TOPICS[2]),
        ("artificial intelligence", TOPICS[2]),
    )
)


def match_topic(v: Any) -> Optional[str]:
    """Return the canonical topic ``v`` names, or ``None`` if it names none.

    Exact match first, then a prefix match, then an alias — so
    ``"innovation (AI tooling)"``, ``"**innovation**"`` and ``"Topic:
    leadership and management"`` all resolve, while ``"none of the above"``
    and ``"unavailable"`` stay ``None``.
    """
    s = (str(v) if v is not None else "").strip().lower()
    for t in TOPICS:
        if s == t or s.startswith(t[:10]):
            return t
    for pattern, t in _ALIASES:
        if pattern.search(s):
            return t
    return None
