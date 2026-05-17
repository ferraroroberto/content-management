"""Resolve an article's author/source to a Notion ``connections`` page id.

Decision order:

1. **Single clean byline** extracted from the article (meta tag, OG, byline div):
   - fuzzy-match against the cache (``rapidfuzz.token_sort_ratio`` >=
     threshold). If hit, use it.
   - If no hit, mark as ``byline-create``. The caller creates a new
     connection (we only create from REAL bylines, never from LLM output).
2. **Multiple authors, missing byline, or any ambiguity** → ask the LLM to
   pick the primary author OR identify the publishing organisation. Then
   verify the LLM's choice against the cache (fuzzy):
   - If match, use it.
   - If no match (or LLM says ``UNKNOWN``), fall back to the configured
     fallback connection (e.g. ``"not classified"``).
3. If the fallback connection itself is missing from the DB, we log loudly
   and let the article save with no author relation.

The point of this split: a byline scraped from the page is **trusted**
(it's what the article actually says), but anything the LLM produces is
**unverified** — so we never create from LLM output, only verify against
existing connections.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from newsletter import llm
from newsletter.cache import CacheState, Connection, normalize_name

logger = logging.getLogger("newsletter_archive.author_resolver")

# Bylines that suggest >1 author. Send these to the LLM rather than
# treating them as a single name.
MULTI_AUTHOR_RE = re.compile(r"\s+(and|&|,|\bwith\b)\s+", re.IGNORECASE)

LLM_PROMPT = """You are identifying the primary AUTHOR of an article.

Reply with ONE of:
- A single person's full name (e.g., "Elena Verna")
- A single company or organisation name (e.g., "Google", "Anthropic",
  "McKinsey", "Microsoft")
- The literal string: UNKNOWN

If multiple authors are listed, return ONLY the primary one — usually
whoever appears first or is most prominent on the page.

If you are not confident, return UNKNOWN. Do not invent names.
Reply on a single line, nothing else.

Title: {title}

Beginning of article:
{snippet}
"""


@dataclass
class AuthorResolution:
    connection: Optional[Connection]
    via: str  # "byline-match" | "byline-create" | "llm-match" | "fallback" | "none"
    raw_byline: Optional[str]
    llm_choice: Optional[str]


def _looks_multi(name: str) -> bool:
    return bool(MULTI_AUTHOR_RE.search(name))


def resolve(
    *,
    byline: Optional[str],
    title: str,
    body_text: str,
    cache: CacheState,
    fallback_name: str,
    llm_base_url: str,
    llm_model: str,
    snippet_chars: int = 1500,
) -> AuthorResolution:
    raw = (byline or "").strip() or None

    # 1) Single clean byline path
    if raw and not _looks_multi(raw):
        existing = cache.find_author(raw)
        if existing:
            return AuthorResolution(existing, "byline-match", raw, None)
        return AuthorResolution(None, "byline-create", raw, None)

    # 2) Multi / missing → ask the LLM
    snippet = (body_text or "")[:snippet_chars]
    prompt = LLM_PROMPT.format(title=title or "", snippet=snippet)
    raw_response = llm.call(
        base_url=llm_base_url, model=llm_model, prompt=prompt, max_tokens=64,
    )
    candidate = raw_response.strip().strip(".").strip('"').strip("'")
    candidate = candidate.splitlines()[0].strip() if candidate else ""
    logger.info("🤖 LLM author pick: '%s' (raw byline was: %s)",
                candidate, raw or "<none>")

    if not candidate or candidate.upper() == "UNKNOWN":
        return _use_fallback(cache, fallback_name, raw, candidate or None)

    match = cache.find_author(candidate)
    if match:
        return AuthorResolution(match, "llm-match", raw, candidate)

    return _use_fallback(cache, fallback_name, raw, candidate)


def _use_fallback(cache: CacheState, fallback_name: str, raw_byline,
                  llm_choice) -> AuthorResolution:
    norm = normalize_name(fallback_name)
    fallback = cache.connections_by_norm.get(norm)
    if not fallback:
        logger.error(
            "❌ Fallback author '%s' not in connections DB — article will be "
            "saved without an author relation", fallback_name,
        )
        return AuthorResolution(None, "none", raw_byline, llm_choice)
    logger.info("↩️  Falling back to '%s'", fallback.name)
    return AuthorResolution(fallback, "fallback", raw_byline, llm_choice)
