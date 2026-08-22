"""Fetch candidate article pages: title / author / body excerpt / paywall state.

``requests`` + readability-lxml (same extraction the archive step uses, minus
the Playwright page), with a persistent JSON cache so backtests and re-runs
never re-download. YouTube links use oEmbed for the title; x.com posts are not
fetched (anchor text stays the title). Every failure is its own state —
``fetched.ok`` False with ``error`` — never folded into "not relevant".

Paywall policy (owner rule, criteria §8): hard walls (Substack "for paid
subscribers", subscribe-to-read) → ``paywalled=True`` and the link is never a
candidate; metered walls on the ``METERED_OK`` domains (HBR…) stay eligible.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qsl, urlsplit

import requests
from lxml import html as lxml_html
from readability import Document

from newsletter.extractor import _AUTHOR_META_NAMES, _meta, _normalise_author

logger = logging.getLogger("newsletter_triage.fetch")

REPO_ROOT = Path(__file__).resolve().parents[2]
FETCH_CACHE = REPO_ROOT / "results" / "newsletter" / "triage" / "fetch_cache.json"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
MAX_BYTES = 3_000_000
EXCERPT_CHARS = 3000

METERED_OK = ("hbr.org", "nytimes.com", "wsj.com", "ft.com", "economist.com", "theatlantic.com",
              "newyorker.com", "wired.com", "bloomberg.com", "technologyreview.com", "inc.com", "fastcompany.com")

_PAYWALL_HARD = re.compile(
    r"(this post is for paid subscribers|for paid subscribers|already a paid subscriber\?|"
    r"subscribe to keep reading|upgrade to paid to keep reading|to continue reading, (?:please )?subscribe|"
    r"this article is for (?:paid )?(?:subscribers|members)|become a (?:paid )?(?:member|subscriber) to (?:read|continue)|"
    r"unlock this (?:article|post|story)|members[- ]only (?:content|post|article)|"
    r"the rest of this (?:post|article) is for paid)", re.IGNORECASE)
_SUBSTACK_PAYWALL_MARKUP = re.compile(r'class="[^"]*\bpaywall\b[^"]*"', re.IGNORECASE)


@dataclass
class Fetched:
    url: str
    ok: bool = False
    final_url: str = ""
    status: int = 0
    kind: str = "article"           # article | video | post | pdf
    title: str = ""
    author: Optional[str] = None
    excerpt: str = ""
    body_chars: int = 0
    paywalled: Optional[bool] = None   # None = not established
    paywall_reason: str = ""
    error: str = ""
    fetched_at: float = 0.0

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


def domain_of(url: str) -> str:
    host = urlsplit(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def detect_paywall(html: str, body_text: str, url: str) -> tuple[Optional[bool], str]:
    dom = domain_of(url)
    sentence = _PAYWALL_HARD.search(html or "")
    markup = _SUBSTACK_PAYWALL_MARKUP.search(html or "")
    if sentence or markup:
        if any(dom.endswith(m) for m in METERED_OK):
            return False, "metered-ok"
        marker = sentence.group(0).lower() if sentence else "paywall markup"
        # the explicit paid-subscriber sentence / Substack paywall block is decisive;
        # weaker CTAs only count when the extracted body is short (cut off)
        explicit = bool(markup) or any(k in marker for k in ("paid subscriber", "keep reading", "continue reading",
                                                              "members", "unlock", "subscribers"))
        if explicit or len(body_text) < 1200:
            return True, marker[:60]
    return False, ""


_YT_ID_PATH = re.compile(r"^/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{6,})")


def youtube_watch_url(url: str) -> Optional[str]:
    """Canonical ``https://www.youtube.com/watch?v=<id>`` for any video link (``watch/?v=``, ``youtu.be/<id>``,
    ``/shorts/<id>``, ``/embed/<id>``, ``/live/<id>``); None when there is no video id (playlists, channels).
    The oembed endpoint answers 404 for the ``youtube.com/watch/?v=`` form some newsletters emit (#224)."""
    parts = urlsplit(url or "")
    host = parts.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    vid = ""
    if host == "youtu.be":
        vid = parts.path.strip("/").split("/")[0]
    elif host in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        vid = dict(parse_qsl(parts.query)).get("v", "")
        if not vid:
            m = _YT_ID_PATH.match(parts.path)
            vid = m.group(1) if m else ""
    return f"https://www.youtube.com/watch?v={vid}" if vid else None


def _youtube_oembed(session: requests.Session, url: str, timeout: float) -> Fetched:
    watch = youtube_watch_url(url)
    f = Fetched(url=url, kind="video", final_url=watch or url)
    try:
        r = session.get("https://www.youtube.com/oembed", params={"url": watch or url, "format": "json"},
                        timeout=timeout)
        f.status = r.status_code
        if r.ok:
            data = r.json()
            f.title = (data.get("title") or "").strip()
            f.author = (data.get("author_name") or "").strip() or None
            f.ok = bool(f.title)
            f.paywalled = False
        else:
            f.error = f"oembed {r.status_code}"
    except requests.RequestException as exc:
        f.error = type(exc).__name__
    return f


def fetch_one(session: requests.Session, url: str, *, timeout: float = 15.0) -> Fetched:
    dom = domain_of(url)
    if dom in ("youtube.com", "youtu.be", "m.youtube.com"):
        return _youtube_oembed(session, url, timeout)
    if dom in ("x.com", "twitter.com"):
        return Fetched(url=url, kind="post", final_url=url, ok=True, paywalled=False, error="not-fetched:x.com")
    f = Fetched(url=url)
    try:
        r = session.get(url, timeout=timeout, stream=True, allow_redirects=True)
        f.status, f.final_url = r.status_code, r.url
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "pdf" in ctype or f.final_url.lower().endswith(".pdf"):
            r.close()
            f.kind, f.ok, f.paywalled = "pdf", True, False
            f.title = urlsplit(f.final_url).path.rsplit("/", 1)[-1]
            return f
        if r.status_code >= 400:
            r.close()
            f.error = f"http {r.status_code}"
            return f
        chunks, size = [], 0
        for chunk in r.iter_content(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                break
        r.close()
        raw = b"".join(chunks)
        enc = r.encoding or "utf-8"
        try:
            html = raw.decode(enc, "replace")
        except LookupError:
            html = raw.decode("utf-8", "replace")
    except requests.RequestException as exc:
        f.error = type(exc).__name__
        return f
    try:
        doc = Document(html)
        article_html = doc.summary(html_partial=True)
        f.title = (doc.short_title() or "").strip()
        tree = lxml_html.fromstring(article_html) if article_html else None
        body = ""
        if tree is not None:
            body = "\n".join(line.strip() for line in tree.text_content().splitlines() if line.strip())
        page_tree = lxml_html.fromstring(html)
        if not f.title:
            t = page_tree.xpath("//title/text()")
            f.title = (t[0] if t else "").strip()
        f.author = _normalise_author(_meta(page_tree, _AUTHOR_META_NAMES))
        f.excerpt = body[:EXCERPT_CHARS]
        f.body_chars = len(body)
        f.paywalled, f.paywall_reason = detect_paywall(html, body, f.final_url or url)
        f.ok = bool(f.title or body)
        if not f.ok:
            f.error = "empty-extraction"
    except Exception as exc:  # lxml/readability on odd markup
        f.error = f"extract:{type(exc).__name__}"
    return f


class FetchCache:
    def __init__(self, path: Path = FETCH_CACHE) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, Any]] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("⚠️ fetch cache unreadable (%s) — starting empty", exc)

    def get(self, url: str) -> Optional[Fetched]:
        d = self._data.get(url)
        return Fetched(**d) if d else None

    def put(self, f: Fetched) -> None:
        with self._lock:
            self._data[f.url] = f.to_json()

    def flush(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    def __len__(self) -> int:
        return len(self._data)


def _session(pool: int) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = _UA
    s.headers["Accept"] = "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"
    s.headers["Accept-Language"] = "en-US,en;q=0.9"
    adapter = requests.adapters.HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def fetch_many(urls: Iterable[str], cache: FetchCache, *, workers: int = 8, timeout: float = 15.0,
               retry_failed: bool = False) -> Dict[str, Fetched]:
    """Fetch every URL (cache first). Returns ``{url: Fetched}``."""
    out: Dict[str, Fetched] = {}
    todo: List[str] = []
    for u in dict.fromkeys(urls):
        hit = cache.get(u)
        if hit is not None and (hit.ok or not retry_failed):
            out[u] = hit
        else:
            todo.append(u)
    if todo:
        session = _session(max(8, workers))
        started = time.monotonic()

        def work(u: str) -> Fetched:
            f = fetch_one(session, u, timeout=timeout)
            f.fetched_at = time.time()
            return f

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(work, u): u for u in todo}
            done = 0
            for fut in as_completed(futs):
                f = fut.result()
                out[f.url] = f
                cache.put(f)
                done += 1
                if done % 100 == 0:
                    logger.info("  … %d/%d pages fetched (%.0fs)", done, len(todo), time.monotonic() - started)
        cache.flush()
    ok = sum(1 for f in out.values() if f.ok)
    logger.info("📄 fetched %d pages (%d cached, %d ok, %d paywalled, %d failed)",
                len(out), len(out) - len(todo), ok, sum(1 for f in out.values() if f.paywalled),
                sum(1 for f in out.values() if not f.ok))
    return out
