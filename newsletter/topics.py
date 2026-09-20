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

from typing import Any, Dict, Optional, Tuple

TOPICS: Tuple[str, ...] = ("leadership and management", "personal development", "innovation")
VALID = set(TOPICS)

_ALIASES: Dict[str, str] = {
    "leadership": TOPICS[0], "management": TOPICS[0], "personal": TOPICS[1],
    "innovation": TOPICS[2], "ai": TOPICS[2],
}


def match_topic(v: Any) -> Optional[str]:
    """Return the canonical topic ``v`` names, or ``None`` if it names none.

    Exact match first, then a prefix match, then a substring alias — so
    ``"innovation (AI tooling)"``, ``"**innovation**"`` and ``"Topic:
    leadership and management"`` all resolve, while ``"none of the above"``
    stays ``None``.
    """
    s = (str(v) if v is not None else "").strip().lower()
    for t in TOPICS:
        if s == t or s.startswith(t[:10]):
            return t
    for k, t in _ALIASES.items():
        if k in s:
            return t
    return None
