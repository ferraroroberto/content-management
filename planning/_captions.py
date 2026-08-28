"""Shared caption-resolution helpers for the planning schedulers.

``canonical_caption_from_publish_ig`` was a byte-identical copy in
``planning.instagram.clone_to_other_platforms`` and inlined again in
``planning.linkedin.schedule_linkedin_posts.fetch_illustration``, the latter
justified by "kept local to avoid a cross-package import, since the LinkedIn
module pulls in Playwright" — the exact workaround ``planning/_dates.py`` was
created to retire. This module only touches ``reporting.notion.editorial``
(Playwright-free), so it is safe for every scheduler to import directly.
"""

from __future__ import annotations

import logging

from reporting.notion.editorial import get_field, retrieve_page


def canonical_caption_from_publish_ig(
    notion,
    illustration_page_id: str,
    illust_cols: dict,
    ed_cols: dict,
    logger: logging.Logger,
    page: dict | None = None,
) -> str:
    """Follow an illustration's ``publishIG`` relation back to all editorial
    rows that published it, sort by day ascending, and return the earliest
    one's ``text IG`` (the canonical first-publication caption).

    Fallback: ``text IG to copy`` formula on the illustration page.

    ``page`` lets a caller that already fetched the illustration page (e.g.
    for its filename/alt-text) pass it through instead of triggering a
    redundant Notion round-trip.
    """
    if page is None:
        page = retrieve_page(notion, illustration_page_id)
    publish_col = illust_cols["publish_relation"]
    publish_rels = page.get("properties", {}).get(publish_col, {}).get("relation", []) or []

    if publish_rels:
        candidates: list[tuple[str, str]] = []
        for rel in publish_rels:
            rel_id = rel.get("id")
            if not rel_id:
                continue
            try:
                ed_page = retrieve_page(notion, rel_id)
            except Exception as err:
                logger.warning("⚠️ could not fetch %s for publishIG resolution: %s", rel_id, err)
                continue
            day_str = get_field(ed_page, "title_day", ed_cols) or ""
            text = get_field(ed_page, "caption_text", ed_cols) or ""
            day_str = str(day_str).strip()
            text = str(text).strip()
            if day_str:
                candidates.append((day_str, text))

        candidates.sort(key=lambda x: x[0])  # YYYYMMDD lex = chronological
        for day_str, text in candidates:
            if text:
                logger.info(
                    "📝 canonical caption from publishIG row %s: %d chars", day_str, len(text)
                )
                return text

    fallback = str(get_field(page, "caption_fallback", illust_cols) or "").strip()
    if fallback:
        logger.warning(
            "⚠️ publishIG yielded no caption — falling back to '%s' formula (%d chars)",
            illust_cols["caption_fallback"], len(fallback),
        )
    return fallback
