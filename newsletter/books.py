"""One-book lookup for the Substack draft's book-recommendation section.

Queries the Notion "books" database for the book linked to a given newsletter
page via its "\U0001F4F0 newsletter" relation, reusing the ``notion_client``
helpers already shared across ``reporting/notion/`` rather than standing up a
third Notion client (``build_newsletter.NotionClient`` and
``newsletter/notion_io.py`` are the other two, each scoped to their own DBs).
"""

from __future__ import annotations

import logging
from typing import Optional, TypedDict

from config.loader import load_full_config
from reporting.notion._client import extract_property_value, init_notion_client
from reporting.notion.editorial import query_rows_by_filter

# Column names as they exist in the live "books" Notion DB (see
# reporting/notion/database_sample/books_fb47feb4fc974def8385e6a865aa68ba_structure.json).
_TITLE_TO_COPY_PROP = "title to copy"
_AUTHOR_TO_COPY_PROP = "author to copy"
_URL_PROP = "url"
_NEWSLETTER_RELATION_PROP = "\U0001F4F0 newsletter"  # "📰 newsletter"


class BookRecommendation(TypedDict):
    title: str
    author: str
    url: str


def find_book_for_newsletter(newsletter_page_id: str) -> Optional[BookRecommendation]:
    """Return the book linked to this newsletter's Notion page, or ``None``.

    Uses the denormalized ``title to copy`` / ``author to copy`` formula
    properties instead of resolving the ``author`` relation to the connections
    DB — they already hold plain, copy-ready text.
    """
    cfg = load_full_config()
    api_token = cfg["notion"]["api_token"]
    books_db_id = cfg["newsletter_archive"]["books_db_id"]

    notion = init_notion_client(api_token)
    if notion is None:
        logging.error("❌ Could not initialize Notion client for the books lookup")
        return None

    rows = query_rows_by_filter(
        notion,
        books_db_id,
        {"property": _NEWSLETTER_RELATION_PROP, "relation": {"contains": newsletter_page_id}},
    )
    if not rows:
        logging.info("📖 No book linked to this newsletter")
        return None
    if len(rows) > 1:
        logging.warning("⚠️ Multiple books linked to this newsletter — using the first")

    props = rows[0].get("properties", {})
    title = str(extract_property_value(props.get(_TITLE_TO_COPY_PROP, {})) or "").strip()
    author = str(extract_property_value(props.get(_AUTHOR_TO_COPY_PROP, {})) or "").strip()
    url = str(extract_property_value(props.get(_URL_PROP, {})) or "").strip()
    if not title or not url:
        logging.warning("⚠️ Book row missing title/url — skipping the book section")
        return None
    return {"title": title, "author": author, "url": url}
