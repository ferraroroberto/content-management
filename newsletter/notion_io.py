"""Notion read + write for the newsletter archive pipeline."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Callable, Dict, Iterator, List, Optional, TypeVar

from notion_client import Client
from notion_client.errors import APIResponseError, HTTPResponseError, RequestTimeoutError

from newsletter.cache import CacheState, Connection, canonicalize_url

logger = logging.getLogger("newsletter_archive.notion_io")

NOTION_RICH_TEXT_LIMIT = 2000
NOTION_BLOCK_LIMIT = 100

T = TypeVar("T")


def _retry(fn: Callable[[], T], *, label: str, retries: int = 4,
           base_delay: float = 2.0) -> T:
    """Retry a Notion call with exponential backoff on transient errors."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except (APIResponseError, HTTPResponseError, RequestTimeoutError) as exc:
            last_exc = exc
            msg = str(exc)
            transient = any(s in msg.lower() for s in (
                "temporarily unavailable", "timeout", "timed out",
                "service_unavailable", "internal_server_error", "rate_limited",
                "conflict_error",
            ))
            if not transient or attempt == retries:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning("⚠️ Notion %s transient error (attempt %d/%d): %s — sleeping %.1fs",
                           label, attempt, retries, msg.splitlines()[0][:160], delay)
            time.sleep(delay)
    # Defensive — should never reach here because retries==retries raised above.
    assert last_exc is not None
    raise last_exc


def init_client(api_token: str) -> Client:
    return Client(auth=api_token)


# ---------------------------------------------------------------------------
# property readers


def _read_title(page: Dict[str, Any], prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name, {})
    arr = prop.get("title", [])
    return arr[0].get("plain_text", "").strip() if arr else ""


def _read_url(page: Dict[str, Any], prop_name: str) -> str:
    prop = page.get("properties", {}).get(prop_name, {})
    return (prop.get("url") or "").strip()


def _read_select(page: Dict[str, Any], prop_name: str) -> Optional[str]:
    prop = page.get("properties", {}).get(prop_name, {})
    sel = prop.get("select")
    return sel.get("name") if sel else None


def _read_rollup_number(page: Dict[str, Any], prop_name: str) -> Optional[float]:
    prop = page.get("properties", {}).get(prop_name, {})
    if prop.get("type") == "rollup":
        rollup = prop.get("rollup", {})
        if rollup.get("type") == "number":
            return rollup.get("number")
        if rollup.get("type") == "array":
            total = 0.0
            for item in rollup.get("array", []):
                if item.get("type") == "number" and item.get("number") is not None:
                    total += item["number"]
            return total
    return prop.get("number")


# ---------------------------------------------------------------------------
# pagination


def _iter_database(client: Client, db_id: str, *, page_size: int = 100,
                   query_filter: Optional[Dict[str, Any]] = None,
                   sorts: Optional[List[Dict[str, Any]]] = None) -> Iterator[Dict[str, Any]]:
    cursor: Optional[str] = None
    while True:
        kwargs: Dict[str, Any] = {"database_id": db_id, "page_size": page_size}
        if cursor:
            kwargs["start_cursor"] = cursor
        if query_filter:
            kwargs["filter"] = query_filter
        if sorts:
            kwargs["sorts"] = sorts
        resp = _retry(lambda: client.databases.query(**kwargs),
                      label=f"query {db_id[:8]}…")
        for row in resp.get("results", []):
            yield row
        if not resp.get("has_more"):
            return
        cursor = resp.get("next_cursor")


# ---------------------------------------------------------------------------
# cache hydration


def hydrate_cache(client: Client, *, articles_db_id: str, connections_db_id: str,
                  fuzzy_threshold: int) -> CacheState:
    state = CacheState(fuzzy_threshold=fuzzy_threshold)

    logger.info("⏬ Loading connections from Notion…")
    for page in _iter_database(client, connections_db_id):
        name = _read_title(page, "name")
        if not name:
            continue
        topic = _read_select(page, "topic")
        state.register_connection(Connection(page_id=page["id"], name=name, topic=topic))
    logger.info("✅ %d connections loaded", len(state.connections))

    logger.info("⏬ Loading existing article URLs from Notion…")
    count = 0
    for page in _iter_database(client, articles_db_id):
        link = _read_url(page, "link")
        if link:
            state.article_urls.add(canonicalize_url(link))
            count += 1
    logger.info("✅ %d article URLs loaded", count)

    return state


# ---------------------------------------------------------------------------
# newsletter selection


def pick_newsletter(
    client: Client, *, newsletter_db_id: str, topic: str,
    topic_to_rollup: Dict[str, str], category_cap: int,
    today: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    rollup_field = topic_to_rollup.get(topic)
    if not rollup_field:
        raise ValueError(f"No rollup mapping for topic '{topic}'")
    today = today or date.today()
    filt = {"property": "Date", "date": {"on_or_after": today.isoformat()}}
    sorts = [{"property": "Date", "direction": "ascending"}]
    for row in _iter_database(client, newsletter_db_id, page_size=50,
                              query_filter=filt, sorts=sorts):
        count = _read_rollup_number(row, rollup_field) or 0
        number = _read_title(row, "number")
        logger.debug("📰 Newsletter candidate %s: %s=%s", number, rollup_field, count)
        if count < category_cap:
            return row
    return None


# ---------------------------------------------------------------------------
# writes


def create_connection(client: Client, *, connections_db_id: str, name: str,
                      topic: str, cache: CacheState) -> Connection:
    page = _retry(
        lambda: client.pages.create(
            parent={"database_id": connections_db_id},
            properties={
                "name": {"title": [{"type": "text", "text": {"content": name}}]},
                "topic": {"select": {"name": topic}},
            },
        ),
        label="create connection",
    )
    conn = Connection(page_id=page["id"], name=name, topic=topic)
    cache.register_connection(conn)
    logger.info("➕ Created connection: %s (topic=%s)", name, topic)
    return conn


def _chunk_rich_text(text: str, limit: int = NOTION_RICH_TEXT_LIMIT) -> List[Dict[str, Any]]:
    if not text:
        return [{"type": "text", "text": {"content": ""}}]
    return [
        {"type": "text", "text": {"content": text[i : i + limit]}}
        for i in range(0, len(text), limit)
    ]


def _body_to_blocks(body_text: str, *, max_blocks: int = NOTION_BLOCK_LIMIT - 1) -> List[Dict[str, Any]]:
    paragraphs = [p.strip() for p in (body_text or "").split("\n") if p.strip()]
    blocks: List[Dict[str, Any]] = []
    for para in paragraphs:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": _chunk_rich_text(para)},
        })
        if len(blocks) >= max_blocks:
            break
    return blocks


def create_article(
    client: Client, *, articles_db_id: str, title: str, link: str, summary: str,
    topic: str, author_id: Optional[str], newsletter_id: str, body_text: str,
    cache: CacheState,
) -> Dict[str, Any]:
    properties: Dict[str, Any] = {
        "article": {"title": [{"type": "text", "text": {"content": title}}]},
        "link": {"url": link},
        "summary": {"rich_text": _chunk_rich_text(summary)},
        "topic": {"select": {"name": topic}},
        "type": {"select": {"name": "article"}},
        "news": {"relation": [{"id": newsletter_id}]},
    }
    if author_id:
        properties["author or source"] = {"relation": [{"id": author_id}]}
    children = _body_to_blocks(body_text)
    page = _retry(
        lambda: client.pages.create(
            parent={"database_id": articles_db_id},
            properties=properties,
            children=children,
        ),
        label="create article",
    )
    cache.register_article(link)
    logger.info("➕ Created article: %s", title)
    return page
