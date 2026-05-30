"""Body-text loader + ``textLI`` cache for the LinkedIn posts DB.

Used by the POST and CAROUSEL routes in ``schedule_linkedin_posts.py`` to
resolve the long-form LI caption that lives on a Notion page in the LI
posts database.

Page layout convention (validated against the posts DB):

* The page body is a sequence of heading + ``code`` block pairs:
  - ``text``          → the canonical LI caption (this is what we want).
  - ``text (old)``    → previous version, kept for reference (ignored).
  - ``source``        → citation / podcast link (ignored).
  - ``article`` etc.  → any other helper section (ignored).
* Only the FIRST ``code`` block (the one under ``text``) is returned.
  Anything after it is treated as additional sections and dropped — this
  is what keeps the caption under LinkedIn's 3000-char limit even when
  the page contains multiple drafts side by side.

Why the first-code-block rule (and not ``get_page_body_text``):

* ``get_page_body_text`` (used by the videos clips DB, which has only one
  ``code`` block per page) concatenates every text-bearing block on the
  page. Applied to a posts page with ``text`` + ``text (old)`` + ``source``
  it returns 2-3× the intended caption, blowing past LI's hard limit.

Public API: ``load_post_payload(notion, post_page_id, posts_columns) ->
PostPayload``. Strategy:
  1. Prefer the ``textLI`` rich_text cache if non-empty AND ≤ LI's hard
     3000-char limit (cache hit).
  2. If the cache is over the limit, invalidate it (treat as stale —
     could be left over from a previous run that read multiple blocks)
     and re-read.
  3. Read the first ``code`` block via ``first_code_block_text`` and
     write it back into ``textLI`` (chunked into ≤2000-char rich_text
     segments since Notion's per-segment limit is 2000).
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from notion_client.errors import HTTPResponseError, RequestTimeoutError

sys.path.append(str(Path(__file__).parent.parent.parent))
from reporting.notion.editorial import (  # noqa: E402
    retrieve_page,
)

logger = logging.getLogger("linkedin_posts_body")

# Notion intermittently returns a transient 5xx / 429 — most notably a 503
# "Public API object rendering exceeded the response time budget", whose own
# remediation text says to retry with exponential backoff. We classify on the
# HTTP status (the message wording is not stable) and retry a few times before
# letting the error propagate; the caller's per-row handler then degrades a
# still-failing read to a single FAIL row instead of aborting the platform.
_NOTION_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_NOTION_RETRY_ATTEMPTS = 4
_NOTION_RETRY_BASE_DELAY = 2.0


def _list_children_with_retry(notion, **kwargs) -> dict:
    """``notion.blocks.children.list`` with bounded exponential backoff on
    transient Notion errors (5xx / 429 / request timeout)."""
    for attempt in range(1, _NOTION_RETRY_ATTEMPTS + 1):
        try:
            return notion.blocks.children.list(**kwargs)
        except (HTTPResponseError, RequestTimeoutError) as exc:
            status = getattr(exc, "status", None)
            transient = isinstance(exc, RequestTimeoutError) or status in _NOTION_TRANSIENT_STATUS
            if not transient or attempt == _NOTION_RETRY_ATTEMPTS:
                raise
            delay = _NOTION_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "⚠️ Notion blocks.children.list transient error (status=%s, attempt %d/%d): "
                "%s — sleeping %.1fs",
                status, attempt, _NOTION_RETRY_ATTEMPTS,
                str(exc).splitlines()[0][:160], delay,
            )
            time.sleep(delay)
    raise RuntimeError("unreachable")  # pragma: no cover — loop always returns or raises

# Notion enforces 2000 chars per rich_text segment's `text.content`.
# Long LI captions (4k+ chars) must be split across multiple segments
# inside the same property value.
_NOTION_RICH_TEXT_SEGMENT_MAX = 2000

# LinkedIn's published hard limit for a regular post's text body. The
# composer disables the final Schedule button when the typed text exceeds
# this; pre-flighting against it lets us fail the row before opening the
# UI rather than after typing 4k+ chars into a doomed composer.
LINKEDIN_POST_CAPTION_MAX = 3000


def _chunk_for_rich_text(text: str, limit: int = _NOTION_RICH_TEXT_SEGMENT_MAX) -> list[str]:
    """Split ``text`` into ``<=limit``-char chunks, breaking on newlines when possible."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        chunks.append(remaining)
    return chunks


def _write_rich_text_multi_segment(notion, page_id: str, col_name: str, text: str) -> None:
    """Write a long string into a Notion rich_text property as multi-segment payload."""
    segments = [{"text": {"content": chunk}} for chunk in _chunk_for_rich_text(text)]
    notion.pages.update(
        page_id=page_id,
        properties={col_name: {"rich_text": segments}},
    )


@dataclass
class PostPayload:
    """Resolved long caption + title for a posts-DB page.

    ``title`` is the page's title property (e.g. ``"co-active coaching: being
    and doing"`` or ``"LI - failure and success 04"``) — needed by the
    carousel route to fuzzy-match the PDF folder.
    """

    page_id: str
    title: str
    caption: str


def _read_text_property(post_page: dict, col_name: str) -> str:
    """Extract a plain string from a rich_text / title / formula(string) prop."""
    prop = post_page.get("properties", {}).get(col_name, {})
    ptype = prop.get("type")
    if ptype == "rich_text":
        segs = prop.get("rich_text", []) or []
        return "".join(s.get("plain_text", "") for s in segs).strip()
    if ptype == "title":
        segs = prop.get("title", []) or []
        return "".join(s.get("plain_text", "") for s in segs).strip()
    if ptype == "formula":
        formula = prop.get("formula", {})
        if formula.get("type") == "string":
            return str(formula.get("string") or "").strip()
    return ""


def first_code_block_text(notion, page_id: str) -> str:
    """Walk the page body and return the text of the FIRST ``code`` block.

    Returns an empty string if the page has no ``code`` block. Pagination
    is handled defensively even though the canonical layout puts the
    ``text`` heading + its code block at the very top of the page.
    """
    cursor: Optional[str] = None
    while True:
        kwargs: dict = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = _list_children_with_retry(notion, **kwargs)
        for block in response.get("results", []):
            if block.get("type") != "code":
                continue
            rich = block.get("code", {}).get("rich_text", []) or []
            return "".join(seg.get("plain_text", "") for seg in rich)
        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")
        if not cursor:
            break
    return ""


def post_page_title(post_page: dict) -> str:
    """Return the title text of a posts-DB page (first title property)."""
    for prop in (post_page.get("properties") or {}).values():
        if prop.get("type") == "title":
            segs = prop.get("title", []) or []
            return "".join(s.get("plain_text", "") for s in segs).strip()
    return ""


def load_post_payload(notion, post_page_id: str, posts_columns: dict) -> PostPayload:
    """Return the title + LI long caption for a posts-DB page.

    Strategy:
      1. Prefer the ``textLI`` cache if present AND within LI's 3000-char
         limit (a cached value over the limit is treated as stale — it can
         only mean the cache was written by an earlier reader that didn't
         apply the "first code block only" rule).
      2. Otherwise read the first ``code`` block via
         ``first_code_block_text`` and write it back into ``textLI``
         (chunked into ≤2000-char rich_text segments).

    Raises ``RuntimeError`` if neither cache nor body yield text — the LI
    driver requires a non-empty caption.
    """
    page = retrieve_page(notion, post_page_id)
    title = post_page_title(page) or post_page_id

    caption_li_col = posts_columns.get("caption_li") or "textLI"
    cached = _read_text_property(page, caption_li_col)
    if cached and len(cached) <= LINKEDIN_POST_CAPTION_MAX:
        logger.info("🗂️ Caption cache hit on %r.%s (%d chars).",
                    title, caption_li_col, len(cached))
        return PostPayload(page_id=post_page_id, title=title, caption=cached)

    if cached:
        logger.warning(
            "🧹 Stale %s cache on %r (%d chars > LI limit %d) — invalidating and re-reading body.",
            caption_li_col, title, len(cached), LINKEDIN_POST_CAPTION_MAX,
        )

    body = first_code_block_text(notion, post_page_id).strip()
    if not body:
        raise RuntimeError(
            f"Post page {title!r} has empty {caption_li_col} cache AND no usable first 'code' block."
        )

    # Write-through: cache the body into textLI for the next run. Notion
    # enforces 2000 chars per rich_text segment, so chunk long captions
    # (LI posts can run close to the 3000-char cap) into multiple segments.
    try:
        _write_rich_text_multi_segment(notion, post_page_id, caption_li_col, body)
        logger.info(
            "🔁 Cached LI caption (%d chars, %d segments) into post %r.%s.",
            len(body), len(_chunk_for_rich_text(body)), title, caption_li_col,
        )
    except Exception as err:
        logger.warning(
            "⚠️ Could not cache LI caption into %s on %r: %s",
            caption_li_col, title, err,
        )

    return PostPayload(page_id=post_page_id, title=title, caption=body)


def assert_caption_within_linkedin_limit(payload: PostPayload) -> None:
    """Raise ``RuntimeError`` if the caption exceeds LI's hard 3000-char limit.

    Called by the scheduler before opening the LI composer so we don't
    waste a session typing into a composer whose final Schedule button
    will be disabled by LI's own validation.
    """
    if len(payload.caption) > LINKEDIN_POST_CAPTION_MAX:
        raise RuntimeError(
            f"Caption for post {payload.title!r} is {len(payload.caption)} chars, "
            f"exceeding LinkedIn's hard limit of {LINKEDIN_POST_CAPTION_MAX}. "
            f"Trim the source body in the Notion posts DB (and clear the "
            f"textLI cache so the next read picks up the trimmed version)."
        )


__all__ = [
    "LINKEDIN_POST_CAPTION_MAX",
    "PostPayload",
    "assert_caption_within_linkedin_limit",
    "first_code_block_text",
    "load_post_payload",
    "post_page_title",
]
