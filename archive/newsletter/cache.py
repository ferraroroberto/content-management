"""In-memory caches for connections and article URLs.

Hydrated once per run from Notion (the source of truth). Append-only thereafter
as new rows are created during the same run, so subsequent articles in the
same batch see them.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rapidfuzz import fuzz

logger = logging.getLogger("newsletter_archive.cache")

TRACKING_PARAM_PREFIXES = ("utm_", "mc_", "_hsenc", "_hsmi")
TRACKING_PARAM_EXACT = {
    "ref", "gclid", "fbclid", "yclid", "msclkid",
    "ref_src", "ref_url", "triedredirect", "source",
}


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    cleaned = []
    for k, v in parse_qsl(parts.query, keep_blank_values=False):
        key = k.lower()
        if key.startswith(TRACKING_PARAM_PREFIXES):
            continue
        if key in TRACKING_PARAM_EXACT:
            continue
        cleaned.append((k, v))
    cleaned.sort()
    query = urlencode(cleaned)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_name(name: str) -> str:
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", no_accents.lower()).strip()


@dataclass
class Connection:
    page_id: str
    name: str
    topic: Optional[str]


@dataclass
class CacheState:
    connections: List[Connection] = field(default_factory=list)
    connections_by_norm: Dict[str, Connection] = field(default_factory=dict)
    article_urls: Set[str] = field(default_factory=set)
    fuzzy_threshold: int = 88

    def find_author(self, name: str) -> Optional[Connection]:
        norm = normalize_name(name)
        if not norm:
            return None
        exact = self.connections_by_norm.get(norm)
        if exact:
            return exact
        best: Optional[Connection] = None
        best_score = 0
        for conn in self.connections:
            score = fuzz.token_sort_ratio(norm, normalize_name(conn.name))
            if score > best_score:
                best_score = score
                best = conn
        if best and best_score >= self.fuzzy_threshold:
            logger.info("🔎 Fuzzy author match '%s' → '%s' (score=%d)",
                        name, best.name, best_score)
            return best
        return None

    def find_article(self, url: str) -> bool:
        return canonicalize_url(url) in self.article_urls

    def register_connection(self, conn: Connection) -> None:
        self.connections.append(conn)
        self.connections_by_norm[normalize_name(conn.name)] = conn

    def register_article(self, url: str) -> None:
        self.article_urls.add(canonicalize_url(url))
