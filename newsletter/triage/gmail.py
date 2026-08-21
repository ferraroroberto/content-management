"""Gmail-label ingestion + link extraction for the newsletter triage.

Adapter over the vendored ``gmail_readonly`` component (read-only scope). It
adds what the portable component deliberately does not have:

* raw MIME walk that keeps the HTML part (``normalize_message`` drops hrefs);
* ``<a href>`` extraction with anchor text / image alt;
* noise filtering (unsubscribe, preferences, share, social, app stores, …);
* tracking-redirect decoding — local first (ConvertKit/Kit/HBR base64 path
  segments, McKinsey host rewrite, Substack ``post_id`` dedupe), then a bounded,
  cached HTTP hop (Substack redirect, beehiiv, SendGrid, Mailchimp, …).

Nothing here writes to Gmail. Credentials / tokens come from
``config.json → newsletter_triage`` (paths under the gitignored ``auth/``).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit, urlunsplit

import requests

from config.loader import load_block
from gmail_readonly.core import GmailLabel, GmailMailbox, GmailSearch
from gmail_readonly.google_client import build_google_read_client
from newsletter.cache import canonicalize_url

logger = logging.getLogger("newsletter_triage.gmail")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MIN_ANCHOR_CHARS = 12

# ---------------------------------------------------------------------------
# records


@dataclass
class Link:
    """One anchor found in an email, progressively resolved."""

    href: str
    text: str = ""
    alt: str = ""
    target: Optional[str] = None      # decoded / resolved final URL (None = unresolved)
    via: str = "raw"                  # raw | direct | b64 | host-rewrite | http | http-fail
    noise: bool = False
    noise_reason: str = ""

    @property
    def label(self) -> str:
        return self.text or self.alt

    @property
    def best_url(self) -> str:
        return self.target or self.href

    @property
    def resolved(self) -> bool:
        return bool(self.target)

    @property
    def canonical(self) -> str:
        return canonicalize_url(canonical_substack(self.best_url))


@dataclass
class EmailRecord:
    message_id: str
    thread_id: Optional[str]
    timestamp: str                    # ISO-8601 UTC
    sender_name: str
    sender_address: str
    subject: str
    links: List[Link] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: Dict[str, Any]) -> "EmailRecord":
        return cls(message_id=d["message_id"], thread_id=d.get("thread_id"),
                   timestamp=d["timestamp"], sender_name=d.get("sender_name", ""),
                   sender_address=d.get("sender_address", ""),
                   subject=d.get("subject", ""),
                   links=[Link(**x) for x in d.get("links", [])])


# ---------------------------------------------------------------------------
# config + mailbox


def load_triage_config() -> Dict[str, Any]:
    return load_block("newsletter_triage")


@dataclass
class TriageMailbox:
    """The vendored facade plus the underlying client (for raw ``format=full`` reads)."""

    client: Any
    mailbox: GmailMailbox

    def close(self) -> None:
        self.mailbox.close()


def build_mailbox(cfg: Optional[Dict[str, Any]] = None) -> TriageMailbox:
    cfg = cfg or load_triage_config()
    token_path = REPO_ROOT / cfg.get("gmail_token_path", "auth/gmail/token.json")
    if not token_path.exists():
        raise FileNotFoundError(
            f"Gmail token not found at {token_path} — copy auth/gmail/credentials.json + "
            f"token.json from the sibling repo or run `python -m gmail_readonly.oauth` "
            f"(see newsletter/README.md)")
    client = build_google_read_client(token_path)
    return TriageMailbox(client=client, mailbox=GmailMailbox(client))


def label_search(tm: TriageMailbox, label_name: str, *,
                 lookback_days: Optional[int] = None,
                 query: str = "") -> GmailSearch:
    """Resolve ``label_name`` → a ``GmailSearch`` (fails closed if the label is missing)."""
    (source,) = tm.mailbox.resolve_sources(
        labels=(GmailLabel(name=label_name, display_name=label_name),),
        lookback_days=lookback_days,
    )
    search = source.search
    if query:
        search = GmailSearch(query=" ".join(p for p in (search.query, query) if p),
                             label_ids=search.label_ids, lookback_days=search.lookback_days)
    return search


def fetch_raw_messages(tm: TriageMailbox, search: GmailSearch, *,
                       limit: Optional[int] = None,
                       skip_ids: Optional[set] = None) -> List[Dict[str, Any]]:
    """Message ids → raw ``format=full`` payloads (batched when the client can)."""
    ids = tm.mailbox.search_ids(search)
    if skip_ids:
        ids = [i for i in ids if i not in skip_ids]
    if limit is not None:
        ids = ids[:limit]
    if not ids:
        return []
    bulk = getattr(tm.client, "get_messages", None)
    if callable(bulk):
        return bulk(ids, metadata_only=False)
    return [tm.client.get_message(i, metadata_only=False) for i in ids]


# ---------------------------------------------------------------------------
# MIME walk


def _iter_parts(part: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    yield part
    for child in part.get("parts") or []:
        yield from _iter_parts(child)


def _decode_body(data: str) -> str:
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    return raw.decode("utf-8", "replace")


def message_html(raw: Dict[str, Any]) -> Tuple[str, str]:
    """Return ``(html, plain)`` bodies of a Gmail ``format=full`` message.

    Parts with a filename (attachments) are skipped — never downloaded.
    """
    html_parts: List[str] = []
    plain_parts: List[str] = []
    for part in _iter_parts(raw.get("payload") or {}):
        if part.get("filename"):
            continue
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        mime = (part.get("mimeType") or "").lower()
        if mime == "text/html":
            html_parts.append(_decode_body(data))
        elif mime == "text/plain":
            plain_parts.append(_decode_body(data))
    return "".join(html_parts), "".join(plain_parts)


def _headers(raw: Dict[str, Any]) -> Dict[str, str]:
    return {str(h.get("name", "")).lower(): str(h.get("value", ""))
            for h in (raw.get("payload") or {}).get("headers") or [] if h.get("name")}


def _timestamp(raw: Dict[str, Any], hdr: Dict[str, str]) -> str:
    try:
        ms = int(raw.get("internalDate") or 0)
        if ms:
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    if hdr.get("date"):
        try:
            return parsedate_to_datetime(hdr["date"]).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
    return ""


# ---------------------------------------------------------------------------
# anchor extraction


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[Link] = []
        self._cur: Optional[Link] = None
        self._skip_depth = 0          # inside <style>/<script>

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag in ("style", "script"):
            self._skip_depth += 1
            return
        if tag == "a":
            self._cur = Link(href=a.get("href", "").strip())
            return
        if tag == "img" and self._cur is not None:
            alt = " ".join(a.get("alt", "").split())
            if alt and not self._cur.alt:
                self._cur.alt = alt

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "a" and self._cur is not None:
            self._cur.text = " ".join(self._cur.text.split())
            if self._cur.href:
                self.links.append(self._cur)
            self._cur = None

    def handle_data(self, data: str) -> None:
        if self._cur is not None and not self._skip_depth:
            self._cur.text += " " + data


_URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")


def extract_links(html: str, plain: str = "") -> List[Link]:
    """Anchors from the HTML part; bare URLs from the text part if there is no HTML."""
    if html:
        parser = _AnchorParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception as exc:  # malformed markup — keep what we have
            logger.debug("anchor parse stopped early: %s", exc)
        return parser.links
    return [Link(href=m.group(0).rstrip(".,;")) for m in _URL_IN_TEXT.finditer(plain or "")]


# ---------------------------------------------------------------------------
# noise filter

_NOISE_ANCHOR = re.compile(
    r"^(?:\W*)(?:unsubscribe|manage (?:your )?(?:email )?preferences|update (?:your )?preferences|"
    r"email preferences|preferences|view (?:this )?(?:email |newsletter |message )?(?:in|on) (?:your )?browser|"
    r"view online|read online|open in (?:app|browser)|view in browser|forward(?: this)?(?: email)?(?: to a friend)?|"
    r"share(?: this)?(?: post| newsletter| email)?(?: (?:on|to|via) [\w+ ]+)?|tweet|like|comment|restack|reply|"
    r"subscribe(?: now| here)?|upgrade(?: to paid| your subscription)?|become a (?:paid )?(?:member|subscriber)|sign in|log in|start writing|"
    r"privacy(?: policy)?|terms(?: of (?:service|use))?|contact(?: us)?|about(?: us)?|advertise|sponsor(?:ship)?s?|"
    r"refer a friend|leave a comment|download (?:the )?app|get the app|app store|google play|"
    r"twitter|x|facebook|linkedin|instagram|youtube|threads|tiktok|spotify|apple podcasts|"
    r"here|click here|read more|learn more|more|continue reading|keep reading|read the full (?:article|story|post)|"
    r"©.*|\d{4})\W*$",
    re.IGNORECASE,
)
# Generic CTAs ("read more") are noise *as anchor text* — the same target is
# almost always present as a titled anchor too; dedupe keeps the titled one.

_NOISE_URL = re.compile(
    r"(?:unsubscribe|/preferences|/profile\b|list-manage\.com/(?:profile|vcard|about)|"
    r"manage[_-]?(?:subscription|preferences)|/email-settings|/settings|"
    r"substack\.com/(?:app-link/(?:comment|reaction|restack|share|cancel|subscribe|profile|open-app)|"
    r"subscribe|signup|profile/|users/|redirect/ios|app-store|sign-in|account|leaderboard|notes)|"
    r"open\.substack\.com/users/|"
    r"(?:twitter|x)\.com/intent|facebook\.com/sharer|linkedin\.com/(?:share|shareArticle|company/|in/)|"
    r"apps\.apple\.com|play\.google\.com|itunes\.apple\.com|"
    r"/privacy|/terms|/legal|^mailto:|^tel:|^sms:|"
    r"^https?://(?:www\.)?(?:facebook|instagram|tiktok|pinterest|threads)\.(?:com|net)/?[^/]*/?$|"
    r"^https?://(?:www\.)?(?:twitter|x)\.com/[^/]+/?$|"
    r"^https?://(?:www\.)?youtube\.com/(?:@|channel/|c/|user/)[^/]*/?$|"
    r"^https?://(?:www\.)?linkedin\.com/?$|"
    r"beehiiv\.com/(?:login|subscribe)|"
    r"convertkit-mail\d*\.com/\w+/(?:unsubscribe|preferences)|preferences\.convertkit|"
    r"\.(?:png|jpe?g|gif|webp|svg|ico)(?:\?|$))",
    re.IGNORECASE,
)


def classify_noise(link: Link, *, min_anchor_chars: int = DEFAULT_MIN_ANCHOR_CHARS) -> None:
    """Mark ``link.noise`` in place. Rules are deliberately conservative."""
    href = link.href
    if not href.lower().startswith(("http://", "https://")):
        link.noise, link.noise_reason = True, "non-http"
        return
    if _NOISE_URL.search(href):
        link.noise, link.noise_reason = True, "url-pattern"
        return
    label = link.label
    if _NOISE_ANCHOR.match(label or ""):
        link.noise, link.noise_reason = True, "anchor-pattern"
        return
    if len(label) < min_anchor_chars:
        link.noise, link.noise_reason = True, "short-anchor"
        return


# ---------------------------------------------------------------------------
# redirect decoding — local


def _b64_url(segment: str) -> Optional[str]:
    seg = segment.strip()
    if len(seg) < 12:
        return None
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            out = decoder(seg + "=" * (-len(seg) % 4))
        except Exception:
            continue
        text = out.decode("utf-8", "ignore")
        if text.startswith(("http://", "https://")):
            return re.split(r"[\x00-\x1f\x7f]", text, 1)[0]
        if text.startswith("{"):
            url = _json_url(text)
            if url:
                return url
    return None


def _json_url(text: str) -> Optional[str]:
    """``{"e": "https://…"}`` (Substack ``redirect/2/``) and friends."""
    try:
        data = json.loads(re.split(r"[\x00-\x1f\x7f]", text, 1)[0])
    except Exception:
        return None
    if isinstance(data, dict):
        for key in ("e", "u", "url", "href", "link", "redirect"):
            val = data.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


def _jwt_url(segment: str) -> Optional[str]:
    if segment.count(".") != 2:
        return None
    try:
        payload = base64.urlsafe_b64decode(segment.split(".")[1] + "==")
        data = json.loads(payload)
    except Exception:
        return None
    if isinstance(data, dict):
        for key in ("u", "url", "href", "link", "redirect"):
            val = data.get(key)
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


_HOST_REWRITE = {
    "email.mckinsey.com": "www.mckinsey.com",
}

_SUBSTACK_POST = re.compile(r"^https?://(?:www\.)?substack\.com/app-link/post\?")
_OPEN_SUBSTACK = re.compile(r"^https?://open\.substack\.com/pub/([^/]+)/p/([^/?#]+)")

_CTA_LAST_SEGMENT_HOSTS = ("convertkit-mail", "kit-mail", "click.fourhourmail", "link.hbr.org")

# Subdomain labels that mark a tracking / click redirector (matched against the
# labels *before* the registrable domain, so ``news.ycombinator.com`` is not one
# but ``link.mail.beehiiv.com`` and ``59b90e10.click.convertkit-mail4.com`` are).
_REDIRECT_LABELS = {
    "click", "clicks", "trk", "track", "tracking", "link", "links", "lnk", "email",
    "email2", "mail", "mg", "go", "t", "ct", "cl", "url", "e", "mailer", "mta",
    "links2", "clk", "r", "redirect", "eomail", "em", "l", "c",
}
_REDIRECT_HOST_SUBSTR = (
    "sendgrid.net", "list-manage.com", "convertkit", "kit-mail", "hubspotlinks",
    "mailchi.mp", "bit.ly", "t.co", "lnkd.in", "buff.ly", "ow.ly", "r20.rs6.net",
    "mandrillapp.com", "e2ma.net", "acemlnb.com", "activehosted.com", "campaign-archive",
    "beehiiv.com", "mailgun", "sparkpostmail", "exacttarget", "rs6.net",
)
_REDIRECT_PATH_SUBSTR = ("substack.com/redirect/", "substack.com/app-link/",
                         "firstround.com/c/", "/ls/click", "/track/click", "/lt.php")


def is_redirector(url: str) -> bool:
    low = url.lower()
    parts = urlsplit(low)
    host = parts.netloc
    hostpath = host + parts.path
    if any(s in hostpath for s in _REDIRECT_PATH_SUBSTR):
        return True
    if any(s in host for s in _REDIRECT_HOST_SUBSTR):
        return True
    labels = host.split(".")
    return any(lbl in _REDIRECT_LABELS for lbl in labels[:-2])


def decode_local(link: Link) -> None:
    """Fill ``link.target`` without any network call when the href encodes it."""
    href = link.href
    parts = urlsplit(href)
    host = parts.netloc.lower()
    if host in _HOST_REWRITE:
        link.target = urlunsplit((parts.scheme, _HOST_REWRITE[host], parts.path, parts.query, ""))
        link.via = "host-rewrite"
        return
    segments = [s for s in parts.path.split("/") if s]
    query_vals = [v for vs in parse_qs(parts.query).values() for v in vs]
    ordered = list(reversed(segments)) if any(h in host for h in _CTA_LAST_SEGMENT_HOSTS) else segments
    for seg in ordered:
        url = _b64_url(seg)
        if url:
            link.target, link.via = url, "b64"
            return
    for val in query_vals:
        url = _b64_url(val) or _jwt_url(val)
        if url:
            link.target, link.via = url, "b64"
            return
    # Substack link previews (and some plain-text newsletters) print the target
    # URL as the anchor text — that *is* the destination, no network needed.
    label = (link.text or "").strip()
    if label.lower().startswith(("http://", "https://")) and " " not in label and is_redirector(href) \
            and not is_redirector(label):
        link.target, link.via = (label.split("?utm_")[0] if "?utm_" in label else label), "anchor-url"
        return
    if not is_redirector(href):
        link.target, link.via = href, "direct"


def substack_post_key(link: Link) -> Optional[str]:
    """Dedupe/cache key for Substack app-link anchors pointing at the same post."""
    if _SUBSTACK_POST.match(link.href):
        q = parse_qs(urlsplit(link.href).query)
        pid = (q.get("post_id") or [None])[0]
        if pid:
            return f"substack:post:{pid}"
    return None


def canonical_substack(url: str) -> str:
    """``open.substack.com/pub/<pub>/p/<slug>`` → ``<pub>.substack.com/p/<slug>``."""
    m = _OPEN_SUBSTACK.match(url or "")
    if m:
        return f"https://{m.group(1)}.substack.com/p/{m.group(2)}"
    return url


_SUBSTACK_SHARED_HOSTS = {"substack.com", "open.substack.com"}
_OPEN_SUBSTACK_PUB = re.compile(r"^/pub/([^/?#]+)")


def publication_domain(url: str, sender_address: str = "") -> str:
    """The source a link belongs to, for caps / priors / display — every Substack publication is its own
    domain (issue #220): ``open.substack.com/pub/<pub>/…`` → ``<pub>.substack.com``; bare ``substack.com``
    app-links / unresolved redirects → the sender's ``<pub>@substack.com`` → ``<pub>.substack.com``; any
    other host → host without ``www.``."""
    parts = urlsplit(url or "")
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host in _SUBSTACK_SHARED_HOSTS:
        m = _OPEN_SUBSTACK_PUB.match(parts.path or "")
        if m:
            return f"{m.group(1).lower()}.substack.com"
        local, _, sdom = (sender_address or "").lower().rpartition("@")
        if sdom == "substack.com" and local:
            return f"{local.split('+')[0]}.substack.com"
    return host


# ---------------------------------------------------------------------------
# redirect resolution — HTTP, cached, bounded


class RedirectCache:
    """JSON-file cache ``{href_or_key: final_url | ""}`` with atomic writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, str] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("⚠️ redirect cache unreadable (%s) — starting empty", exc)
        self._dirty = 0

    def get(self, key: str) -> Optional[str]:
        return self._data.get(key)

    def put(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._dirty += 1
            if self._dirty >= 200:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = 0

    def __len__(self) -> int:
        return len(self._data)


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def _session(pool_size: int = 16) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = _UA
    s.max_redirects = 10
    adapter = requests.adapters.HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_MAX_HOPS = 10


def _follow(session: requests.Session, method: str, url: str, *, timeout: float,
            deadline: float) -> Tuple[int, str]:
    """Follow ``Location`` hops manually — bodies are never read.

    ``requests``' own ``allow_redirects`` consumes every intermediate body, and
    a single endless / huge response (streaming endpoint, gzip bomb, bot wall
    page) then pins a CPU core forever with no timeout firing. Here each hop is
    a streamed request closed immediately, bounded by ``_MAX_HOPS`` and a wall-
    clock ``deadline``. Returns ``(status_code, final_url)``.
    """
    cur = url
    for _ in range(_MAX_HOPS):
        if time.monotonic() > deadline:
            raise requests.Timeout("resolve deadline exceeded")
        resp = session.request(method, cur, allow_redirects=False, timeout=timeout, stream=True)
        try:
            status = resp.status_code
            loc = resp.headers.get("Location")
        finally:
            resp.close()
        if status in (301, 302, 303, 307, 308) and loc:
            cur = requests.compat.urljoin(cur, loc)
            continue
        return status, cur
    return 599, cur  # too many redirects — treated as a failure


def resolve_one(session: requests.Session, href: str, *, timeout: float = 10.0) -> Tuple[str, str]:
    """Follow redirects; return ``(final_url, status)`` where status ∈ http|http-fail.

    HEAD first; GET (streamed, body never read) when HEAD is refused (405/404
    on e2ma/firstround-style redirectors) or when HEAD did not leave the
    redirector. A URL that simply answers for itself is its own target.
    """
    deadline = time.monotonic() + 3 * timeout
    try:
        status, final = _follow(session, "HEAD", href, timeout=timeout, deadline=deadline)
        if status < 400 and (final != href or not is_redirector(final)):
            return final, "http"
        status, final = _follow(session, "GET", href, timeout=timeout, deadline=deadline)
        if status < 400:
            return final, "http"
        return "", "http-fail"
    except (requests.RequestException, ValueError) as exc:
        logger.debug("resolve failed for %s: %s", href[:80], type(exc).__name__)
        return "", "http-fail"


def resolve_links(links: Iterable[Link], cache: RedirectCache, *, workers: int = 8,
                  timeout: float = 10.0, budget: Optional[int] = None) -> Dict[str, int]:
    """Resolve unresolved, non-noise links over HTTP, using/filling ``cache``.

    ``budget`` caps the number of *network* keys this call may spend; cache
    hits are free. N anchors with the same key cost one request.
    """
    stats = {"cached": 0, "resolved": 0, "failed": 0, "skipped-budget": 0}
    by_key: Dict[str, List[Link]] = {}
    for link in links:
        if link.resolved or link.noise or link.via == "http-fail":
            continue
        key = substack_post_key(link) or link.href
        hit = cache.get(key)
        if hit is not None:
            if hit:
                link.target, link.via = hit, "http"
                if _NOISE_URL.search(hit):
                    link.noise, link.noise_reason = True, "url-pattern-resolved"
            else:
                link.via = "http-fail"
            stats["cached"] += 1
            continue
        by_key.setdefault(key, []).append(link)
    keys = list(by_key)
    if budget is not None and len(keys) > budget:
        stats["skipped-budget"] = len(keys) - budget
        keys = keys[:budget]
    if not keys:
        return stats
    session = _session(pool_size=max(16, workers))
    started = time.monotonic()

    def work(key: str) -> Tuple[str, str, str]:
        final, status = resolve_one(session, by_key[key][0].href, timeout=timeout)
        return key, final, status

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, k) for k in keys]
        done = 0
        for fut in as_completed(futures):
            key, final, _status = fut.result()
            cache.put(key, final)
            for link in by_key[key]:
                if final:
                    link.target, link.via = final, "http"
                    stats["resolved"] += 1
                    if _NOISE_URL.search(final):
                        link.noise, link.noise_reason = True, "url-pattern-resolved"
                else:
                    link.via = "http-fail"
                    stats["failed"] += 1
            done += 1
            if done % 500 == 0:
                logger.info("  … %d/%d redirect keys resolved (%.0fs)", done, len(keys),
                            time.monotonic() - started)
    cache.flush()
    return stats


# ---------------------------------------------------------------------------
# per-email pipeline


def dedupe_links(links: List[Link]) -> List[Link]:
    """Keep one anchor per target, preferring the one with the longest label."""
    best: Dict[str, Link] = {}
    order: List[str] = []
    for link in links:
        if link.resolved:
            key = link.canonical
        else:
            key = substack_post_key(link) or (f"text:{link.label.lower()}" if link.label else link.href)
        cur = best.get(key)
        if cur is None:
            best[key] = link
            order.append(key)
        elif len(link.label) > len(cur.label):
            best[key] = link
    return [best[k] for k in order]


def build_record(raw: Dict[str, Any], *,
                 min_anchor_chars: int = DEFAULT_MIN_ANCHOR_CHARS) -> Tuple[EmailRecord, str]:
    """Gmail ``format=full`` message → ``EmailRecord`` (+ the HTML for caching).

    Links are extracted, noise-classified, locally decoded and deduped; HTTP
    resolution is a separate, explicit step (``resolve_links``).
    """
    hdr = _headers(raw)
    html, plain = message_html(raw)
    name, addr = parseaddr(hdr.get("from", ""))
    rec = EmailRecord(message_id=str(raw.get("id") or ""), thread_id=raw.get("threadId"),
                      timestamp=_timestamp(raw, hdr), sender_name=name or addr,
                      sender_address=addr.lower(), subject=(hdr.get("subject") or "").strip())
    rec.links = links_from_html(html, plain, min_anchor_chars=min_anchor_chars)
    return rec, html


def links_from_html(html: str, plain: str = "", *,
                    min_anchor_chars: int = DEFAULT_MIN_ANCHOR_CHARS) -> List[Link]:
    """Extract → noise-classify → local-decode → dedupe. Pure; no network."""
    links = extract_links(html, plain)
    for link in links:
        classify_noise(link, min_anchor_chars=min_anchor_chars)
        if not link.noise:
            decode_local(link)
            if link.target and link.target != link.href and _NOISE_URL.search(link.target):
                link.noise, link.noise_reason = True, "url-pattern-decoded"
    return dedupe_links([l for l in links if not l.noise])
