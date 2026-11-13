"""Extract title + body text + author byline from a live Playwright Page.

Uses readability-lxml for the main article body and OpenGraph / standard meta
tags for the author fallback when readability does not surface a byline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from lxml import html as lxml_html
from playwright.sync_api import Page
from readability import Document


@dataclass
class ExtractedArticle:
    url: str
    title: str
    body_text: str
    author: Optional[str]


_AUTHOR_META_NAMES: List[str] = [
    "author",
    "article:author",
    "og:author",
    "twitter:creator",
    "parsely-author",
    "dc.creator",
    "sailthru.author",
]


def _meta(tree, names: List[str]) -> Optional[str]:
    for name in names:
        for attr in ("property", "name"):
            els = tree.xpath(f"//meta[@{attr}=$n]", n=name)
            if els:
                content = (els[0].get("content") or "").strip()
                if content:
                    return content
    return None


def _normalise_author(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    candidate = raw.strip()
    if candidate.startswith("@"):
        candidate = candidate[1:]
    if candidate.startswith(("http://", "https://")):
        candidate = candidate.rstrip("/").rsplit("/", 1)[-1]
        candidate = candidate.replace("-", " ").replace("_", " ")
    return candidate or None


def extract(page: Page) -> ExtractedArticle:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except Exception:
        pass

    url = page.url
    html = page.content()

    doc = Document(html)
    article_html = doc.summary(html_partial=True)
    title = (doc.short_title() or page.title() or "").strip()

    article_tree = lxml_html.fromstring(article_html) if article_html else None
    body_text = ""
    if article_tree is not None:
        body_text = "\n".join(
            line.strip() for line in article_tree.text_content().splitlines() if line.strip()
        )

    page_tree = lxml_html.fromstring(html)
    author = _normalise_author(_meta(page_tree, _AUTHOR_META_NAMES))

    if not author:
        nodes = page_tree.xpath(
            "//*[contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            " 'abcdefghijklmnopqrstuvwxyz'), 'byline') or "
            "contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            " 'abcdefghijklmnopqrstuvwxyz'), 'author')]"
        )
        for n in nodes:
            text = " ".join(n.text_content().split())
            if 2 < len(text) < 80:
                author = _normalise_author(text)
                if author:
                    break

    return ExtractedArticle(url=url, title=title, body_text=body_text, author=author)
