"""Build the newsletter-triage history dataset and its statistics.

Ground truth for the criteria (issue #210):

* **positives** — every Notion article with an edition (``news``) relation in
  the window: title, URL, topic, author, edition number/date, star, must-read;
* **offered** — every non-noise link in every email of the Gmail label over the
  same window (sender, subject, date, anchor text, decoded/resolved URL);
* **join** — positives → the email link they came from (exact canonical URL →
  Substack slug → fuzzy anchor-vs-title), with the unmatched share reported;
* **stats** — per-sender offered/selected/hit-rate, per-domain and per-author
  caps actually observed per edition, topic mix, star rate, title patterns.

Outputs under ``results/newsletter/triage/history/`` (gitignored):
``emails.jsonl``, ``positives.jsonl``, ``matches.jsonl``, ``stats.json``,
``stats.md``, ``raw/<message_id>.html.gz``.

Usage::

    python -m newsletter.triage.history [--weeks 54] [--no-gmail] [--no-resolve]
                                        [--budget N] [--reextract] [--debug]

Incremental: already-downloaded emails are not re-fetched; ``--reextract``
re-runs link extraction from the cached HTML (after a rule change).
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rapidfuzz import fuzz, utils as rf_utils  # noqa: E402

from config.console import force_utf8_stdio  # noqa: E402
from config.loader import load_block, load_full_config  # noqa: E402
from newsletter import notion_io  # noqa: E402
from newsletter.cache import canonicalize_url  # noqa: E402
from newsletter.triage import gmail as gm  # noqa: E402

logger = logging.getLogger("newsletter_triage.history")

HISTORY_DIR = REPO_ROOT / "results" / "newsletter" / "triage" / "history"
REDIRECT_CACHE = REPO_ROOT / "results" / "newsletter" / "triage" / "redirects.json"

TOPICS = ("leadership and management", "personal development", "innovation")

# ---------------------------------------------------------------------------
# small io helpers


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with tmp.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    tmp.replace(path)
    return n


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fp:
        return [json.loads(line) for line in fp if line.strip()]


def _domain(url: str) -> str:
    host = urlsplit(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _slug(url: str) -> str:
    path = urlsplit(url or "").path.rstrip("/")
    seg = path.rsplit("/", 1)[-1] if path else ""
    return seg.lower() if len(seg) >= 12 else ""


def _norm(text: str) -> str:
    return rf_utils.default_process(text or "")


# ---------------------------------------------------------------------------
# 1. Gmail pull (incremental, cached HTML)


def _reextract_from_cache(existing: Dict[str, gm.EmailRecord], raw_dir: Path, min_anchor: int) -> None:
    """Re-run link extraction from cached HTML for every record in ``existing``
    (mutated in place). Pure local file I/O — no mailbox, no network."""
    if not existing:
        return
    logger.info("♻️ re-extracting links from %d cached HTML bodies", len(existing))
    for mid, rec in existing.items():
        gz = raw_dir / f"{mid}.html.gz"
        if not gz.exists():
            continue
        with gzip.open(gz, "rt", encoding="utf-8") as fp:
            html = fp.read()
        rec.links = gm.links_from_html(html, min_anchor_chars=min_anchor)


def reextract_cached(cfg: Dict[str, Any]) -> List[gm.EmailRecord]:
    """Re-run link extraction from already-cached HTML only — no mailbox
    build, no Gmail search, no network I/O at all.

    Split out of ``pull_emails`` (issue #246): that function always calls
    ``gm.build_mailbox(cfg)`` + ``label_search`` + ``search_ids`` even when
    called with ``limit=0``, so the documented offline path
    (``--no-gmail --reextract --budget 0``, "no new network" per the README)
    raised ``FileNotFoundError`` on a missing Gmail token instead of touching
    only the cached HTML it was meant to re-parse.
    """
    emails_path = HISTORY_DIR / "emails.jsonl"
    raw_dir = HISTORY_DIR / "raw"
    existing = {r["message_id"]: gm.EmailRecord.from_json(r) for r in _read_jsonl(emails_path)}
    min_anchor = int(cfg.get("min_anchor_chars", gm.DEFAULT_MIN_ANCHOR_CHARS))
    _reextract_from_cache(existing, raw_dir, min_anchor)
    records = sorted(existing.values(), key=lambda r: r.timestamp)
    _write_jsonl(emails_path, (r.to_json() for r in records))
    return records


def pull_emails(weeks: int, *, cfg: Dict[str, Any], reextract: bool = False,
                limit: Optional[int] = None) -> List[gm.EmailRecord]:
    emails_path = HISTORY_DIR / "emails.jsonl"
    raw_dir = HISTORY_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    existing = {r["message_id"]: gm.EmailRecord.from_json(r) for r in _read_jsonl(emails_path)}
    min_anchor = int(cfg.get("min_anchor_chars", gm.DEFAULT_MIN_ANCHOR_CHARS))

    if reextract:
        _reextract_from_cache(existing, raw_dir, min_anchor)

    tm = gm.build_mailbox(cfg)
    try:
        search = gm.label_search(tm, cfg.get("gmail_label", "newsletters"), lookback_days=weeks * 7)
        ids = tm.mailbox.search_ids(search)
        new_ids = [i for i in ids if i not in existing]
        if limit is not None:
            new_ids = new_ids[:limit]
        logger.info("📬 label '%s': %d messages in %d weeks — %d already cached, %d to fetch",
                    cfg.get("gmail_label", "newsletters"), len(ids), weeks, len(ids) - len(new_ids), len(new_ids))
        batch = 200
        for start in range(0, len(new_ids), batch):
            chunk = new_ids[start:start + batch]
            raws = tm.client.get_messages(chunk, metadata_only=False)
            for raw in raws:
                rec, html = gm.build_record(raw, min_anchor_chars=min_anchor)
                existing[rec.message_id] = rec
                with gzip.open(raw_dir / f"{rec.message_id}.html.gz", "wt", encoding="utf-8") as fp:
                    fp.write(html)
            _write_jsonl(emails_path, (r.to_json() for r in existing.values()))
            logger.info("  … %d/%d fetched", min(start + batch, len(new_ids)), len(new_ids))
    finally:
        tm.close()
    records = sorted(existing.values(), key=lambda r: r.timestamp)
    _write_jsonl(emails_path, (r.to_json() for r in records))
    return records


# ---------------------------------------------------------------------------
# 2. Notion positives


def _title(page: Dict[str, Any], prop: str) -> str:
    return notion_io._read_title(page, prop)  # noqa: SLF001 — shared reader


def _rich(page: Dict[str, Any], prop: str) -> str:
    arr = page.get("properties", {}).get(prop, {}).get("rich_text", [])
    return "".join(x.get("plain_text", "") for x in arr).strip()


def load_positives(weeks: int) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return ``(positives, editions)`` from Notion for editions dated within the window."""
    cfg = load_full_config()
    na = cfg["newsletter_archive"]
    client = notion_io.init_client(cfg["notion"]["api_token"])
    since = (date.today() - timedelta(weeks=weeks)).isoformat()

    editions: Dict[str, Dict[str, Any]] = {}
    for page in notion_io._iter_database(  # noqa: SLF001
            client, na["newsletter_db_id"],
            query_filter={"property": "Date", "date": {"on_or_after": since}},
            sorts=[{"property": "Date", "direction": "ascending"}]):
        props = page["properties"]
        d = (props.get("Date", {}).get("date") or {}).get("start")
        editions[page["id"]] = {
            "id": page["id"], "number": _title(page, "number"), "date": d,
            "title": _rich(page, "title"),
            "n_leader": notion_io._read_rollup_number(page, "n leader"),  # noqa: SLF001
            "n_innov": notion_io._read_rollup_number(page, "n innov"),  # noqa: SLF001
            "n_persdev": notion_io._read_rollup_number(page, "n persdev"),  # noqa: SLF001
            "substack": (props.get("substack", {}).get("url") or ""),
        }
    logger.info("📰 %d editions since %s", len(editions), since)

    connections: Dict[str, str] = {}
    for page in notion_io._iter_database(client, na["connections_db_id"]):  # noqa: SLF001
        connections[page["id"]] = _title(page, "name")
    logger.info("👤 %d connections", len(connections))

    created_since = (date.today() - timedelta(weeks=weeks + 6)).isoformat()
    positives: List[Dict[str, Any]] = []
    for page in notion_io._iter_database(  # noqa: SLF001
            client, na["articles_db_id"],
            query_filter={"property": "created", "created_time": {"on_or_after": created_since}}):
        props = page["properties"]
        news = [r["id"] for r in props.get("news", {}).get("relation", [])]
        eds = [editions[n] for n in news if n in editions]
        if not eds:
            continue
        ed = eds[0]
        url = props.get("link", {}).get("url") or ""
        authors = [connections.get(r["id"], "?") for r in props.get("author or source", {}).get("relation", [])]
        topic = (props.get("topic", {}).get("select") or {}).get("name")
        positives.append({
            "article_id": page["id"],
            "title": _title(page, "article"),
            "url": url,
            "canonical": canonicalize_url(gm.canonical_substack(url)),
            "domain": _domain(url),
            "topic": topic,
            "type": (props.get("type", {}).get("select") or {}).get("name"),
            "authors": authors,
            "author": authors[0] if authors else "",
            "star": bool(props.get("star", {}).get("checkbox")),
            "next": bool(props.get("next", {}).get("checkbox")),
            "edition": ed["number"],
            "edition_date": ed["date"],
            "created": page.get("created_time", "")[:10],
            "summary": _rich(page, "summary")[:400],
            "niche": [n["name"] for n in props.get("niche", {}).get("multi_select", [])],
        })
    # must-read = first sentence of the edition title (build_newsletter._MUST_READ_PERM)
    by_ed: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in positives:
        by_ed[p["edition"]].append(p)
    for ed in editions.values():
        first = (ed["title"] or "").split(". ")[0].strip().rstrip(".")
        if not first:
            continue
        best, score = None, 0.0
        for p in by_ed.get(ed["number"], []):
            s = fuzz.ratio(_norm(first), _norm(p["title"]))
            if s > score:
                best, score = p, s
        if best is not None and score >= 85:
            best["must_read"] = True
        ed["must_read_title"] = first
    for p in positives:
        p.setdefault("must_read", False)
    logger.info("✅ %d positives with an edition relation", len(positives))
    return positives, sorted(editions.values(), key=lambda e: e["date"] or "")


# ---------------------------------------------------------------------------
# 3. HTTP resolution (bounded, prioritised)


def resolve_offered(records: List[gm.EmailRecord], *, cfg: Dict[str, Any],
                    budget: Optional[int]) -> Dict[str, int]:
    cache = gm.RedirectCache(REDIRECT_CACHE)
    todo: List[gm.Link] = []
    for rec in records:
        for link in rec.links:
            if not link.resolved and not link.noise and link.via != "http-fail":
                todo.append(link)
    # priority: Substack headline posts first, then title-like anchors, then the rest
    todo.sort(key=lambda l: (0 if gm.substack_post_key(l) else 1 if len(l.label) >= 25 else 2,
                             -len(l.label)))
    logger.info("🔗 %d unresolved redirector links (cache has %d entries, budget %s)",
                len(todo), len(cache), budget)
    stats = gm.resolve_links(todo, cache,
                             workers=int(cfg.get("redirect_workers", 8)),
                             timeout=float(cfg.get("redirect_timeout_s", 10)),
                             budget=budget)
    logger.info("🔗 resolve: %s", stats)
    _write_jsonl(HISTORY_DIR / "emails.jsonl", (r.to_json() for r in records))
    return stats


# ---------------------------------------------------------------------------
# 4. join positives → offered links


def join(positives: List[Dict[str, Any]], records: List[gm.EmailRecord]) -> List[Dict[str, Any]]:
    by_canon: Dict[str, List[Tuple[gm.EmailRecord, gm.Link]]] = defaultdict(list)
    by_slug: Dict[str, List[Tuple[gm.EmailRecord, gm.Link]]] = defaultdict(list)
    all_links: List[Tuple[gm.EmailRecord, gm.Link, str]] = []
    for rec in records:
        for link in rec.links:
            if link.noise:
                continue
            if link.resolved:
                by_canon[link.canonical].append((rec, link))
                s = _slug(link.canonical)
                if s:
                    by_slug[s].append((rec, link))
            all_links.append((rec, link, _norm(link.label)))

    # domain → senders (from resolved links) for the last-resort attribution
    domain_senders: Dict[str, Counter] = defaultdict(Counter)
    for rec in records:
        for link in rec.links:
            if link.resolved and not link.noise:
                domain_senders[_domain(link.best_url)][rec.sender_address] += 1
    by_sender: Dict[str, List[gm.EmailRecord]] = defaultdict(list)
    for rec in records:
        by_sender[rec.sender_address].append(rec)

    matches: List[Dict[str, Any]] = []
    for p in positives:
        ed_date = date.fromisoformat(p["edition_date"]) if p.get("edition_date") else None
        lo = (ed_date - timedelta(days=35)).isoformat() if ed_date else ""
        hi = (ed_date + timedelta(days=2)).isoformat() if ed_date else "9999"

        def in_window(rec: gm.EmailRecord) -> bool:
            return lo <= rec.timestamp[:10] <= hi

        found: Optional[Tuple[gm.EmailRecord, gm.Link]] = None
        method = ""
        cands = [c for c in by_canon.get(p["canonical"], []) if in_window(c[0])] or \
                by_canon.get(p["canonical"], [])
        if cands:
            found, method = cands[0], "url"
        if found is None:
            s = _slug(p["canonical"])
            if s:
                cands = [c for c in by_slug.get(s, []) if in_window(c[0])] or by_slug.get(s, [])
                if cands:
                    found, method = cands[0], "slug"
        if found is None and p["title"]:
            title_n = _norm(p["title"])
            best, score = None, 0.0
            for rec, link, label_n in all_links:
                if not label_n or not in_window(rec):
                    continue
                s = fuzz.ratio(title_n, label_n)
                if s > score:
                    best, score = (rec, link), s
            if best is not None and score >= 90:
                found, method = best, "title"
            elif best is not None and score >= 80 and (
                    _domain(best[1].best_url) == p["domain"] or not best[1].resolved):
                found, method = best, "title~"
        if found is None and p["domain"] in domain_senders:
            # last resort: the sender that usually carries this domain, if it
            # mailed inside the window (attribution only — link unknown)
            for addr, _n in domain_senders[p["domain"]].most_common(3):
                recs = [r for r in by_sender.get(addr, []) if in_window(r)]
                if recs:
                    found, method = (recs[-1], gm.Link(href="", text="")), "domain"
                    break
        row = {"article_id": p["article_id"], "title": p["title"], "edition": p["edition"],
               "topic": p["topic"], "author": p["author"], "star": p["star"],
               "must_read": p["must_read"], "domain": p["domain"], "method": method or "none"}
        if found:
            rec, link = found
            row.update({"message_id": rec.message_id, "sender_name": rec.sender_name,
                        "sender_address": rec.sender_address, "email_subject": rec.subject,
                        "email_date": rec.timestamp[:10], "anchor": link.label,
                        "link_url": link.best_url})
        matches.append(row)
    return matches


# ---------------------------------------------------------------------------
# 5. statistics


_NEWS_HINT = re.compile(
    r"\b(launch|launches|launched|releases?|released|announc|introduc|unveil|new model|"
    r"gpt-?\d|claude \d|gemini \d|llama \d|openai|anthropic|deepmind|nvidia|funding|raises|"
    r"valuation|ipo|acquir|ceo steps|layoffs?|earnings)\b", re.IGNORECASE)


def compute_stats(positives: List[Dict[str, Any]], editions: List[Dict[str, Any]],
                  records: List[gm.EmailRecord], matches: List[Dict[str, Any]]) -> Dict[str, Any]:
    st: Dict[str, Any] = {}
    st["window"] = {"emails": len(records),
                    "first_email": records[0].timestamp[:10] if records else None,
                    "last_email": records[-1].timestamp[:10] if records else None,
                    "editions": len(editions), "positives": len(positives)}
    # emails per week
    per_week = Counter(datetime.fromisoformat(r.timestamp).strftime("%G-W%V") for r in records if r.timestamp)
    st["emails_per_week"] = dict(sorted(per_week.items()))
    weeks_vals = list(per_week.values())
    st["emails_per_week_summary"] = {"min": min(weeks_vals) if weeks_vals else 0,
                                     "median": sorted(weeks_vals)[len(weeks_vals) // 2] if weeks_vals else 0,
                                     "max": max(weeks_vals) if weeks_vals else 0}
    # links
    all_links = [l for r in records for l in r.links]
    st["links"] = {"kept_total": len(all_links),
                   "per_email_avg": round(len(all_links) / max(1, len(records)), 1),
                   "via": dict(Counter(l.via for l in all_links)),
                   "noise": dict(Counter(l.noise_reason for l in all_links if l.noise))}
    # match rate
    st["match"] = dict(Counter(m["method"] for m in matches))
    matched = [m for m in matches if m["method"] != "none"]
    st["match"]["rate"] = round(len(matched) / max(1, len(matches)), 3)

    # per sender: emails, offered, selected, topics, stars
    sender_emails = Counter(); sender_offered = Counter()
    sender_name: Dict[str, str] = {}
    for r in records:
        sender_emails[r.sender_address] += 1
        sender_offered[r.sender_address] += sum(1 for l in r.links if not l.noise)
        sender_name.setdefault(r.sender_address, r.sender_name)
    sender_sel = Counter(); sender_star = Counter(); sender_topics: Dict[str, Counter] = defaultdict(Counter)
    sender_eds: Dict[str, set] = defaultdict(set)
    for m in matched:
        a = m["sender_address"]
        sender_sel[a] += 1
        sender_star[a] += int(bool(m["star"]))
        sender_topics[a][m["topic"] or "?"] += 1
        sender_eds[a].add(m["edition"])
    senders = []
    for addr, n_em in sender_emails.most_common():
        senders.append({"sender": sender_name.get(addr, addr), "address": addr, "emails": n_em,
                        "offered_links": sender_offered[addr], "selected": sender_sel[addr],
                        "editions_with_pick": len(sender_eds[addr]),
                        "hit_rate_per_email": round(sender_sel[addr] / n_em, 2),
                        "hit_rate_per_link": round(sender_sel[addr] / max(1, sender_offered[addr]), 3),
                        "stars": sender_star[addr],
                        "topics": dict(sender_topics[addr].most_common())})
    senders.sort(key=lambda s: (-s["selected"], -s["emails"]))
    st["senders"] = senders
    st["senders_never_selected"] = [s for s in senders if s["selected"] == 0 and s["emails"] >= 3]

    # per domain / per author caps observed per edition
    def per_edition_max(key: str) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        counts: Dict[str, Counter] = defaultdict(Counter)
        for p in positives:
            counts[p[key]][p["edition"]] += 1
        for val, c in counts.items():
            if not val:
                continue
            total = sum(c.values())
            out[val] = {"total": total, "editions": len(c), "max_per_edition": max(c.values()),
                        "avg_per_edition": round(total / len(c), 2),
                        "per_edition_hist": dict(Counter(c.values()))}
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["total"]))
    st["domains"] = per_edition_max("domain")
    st["authors"] = per_edition_max("author")

    # topics / stars / must-read / type
    st["topics"] = dict(Counter(p["topic"] for p in positives))
    st["types"] = dict(Counter(p["type"] for p in positives))
    st["stars"] = {"total": sum(p["star"] for p in positives),
                   "per_topic": dict(Counter(p["topic"] for p in positives if p["star"])),
                   "per_edition_hist": dict(Counter(Counter(p["edition"] for p in positives if p["star"]).values()))}
    st["must_read"] = {"total": sum(p["must_read"] for p in positives),
                       "per_topic": dict(Counter(p["topic"] for p in positives if p["must_read"])),
                       "by_domain": dict(Counter(p["domain"] for p in positives if p["must_read"]).most_common(15)),
                       "by_author": dict(Counter(p["author"] for p in positives if p["must_read"]).most_common(15))}
    st["stars_by_domain"] = dict(Counter(p["domain"] for p in positives if p["star"]).most_common(20))
    st["stars_by_author"] = dict(Counter(p["author"] for p in positives if p["star"]).most_common(20))

    # per-edition composition
    ed_rows = []
    for ed in editions:
        ps = [p for p in positives if p["edition"] == ed["number"]]
        if not ps:
            continue
        doms = Counter(p["domain"] for p in ps)
        auths = Counter(p["author"] for p in ps)
        ed_rows.append({"edition": ed["number"], "date": ed["date"], "n": len(ps),
                        "topics": dict(Counter(p["topic"] for p in ps)),
                        "hbr": doms.get("hbr.org", 0),
                        "max_same_domain": max(doms.values()), "max_same_author": max(auths.values()),
                        "distinct_authors": len(auths), "stars": sum(p["star"] for p in ps),
                        "must_read_topic": next((p["topic"] for p in ps if p["must_read"]), None)})
    st["editions"] = ed_rows
    st["edition_caps"] = {
        "hbr_hist": dict(Counter(e["hbr"] for e in ed_rows)),
        "max_same_author_hist": dict(Counter(e["max_same_author"] for e in ed_rows)),
        "max_same_domain_hist": dict(Counter(e["max_same_domain"] for e in ed_rows)),
        "must_read_topic_hist": dict(Counter(e["must_read_topic"] for e in ed_rows)),
    }

    # email date → edition date lag
    lags = []
    for m in matched:
        ed = next((e for e in editions if e["number"] == m["edition"]), None)
        if ed and ed.get("date") and m.get("email_date"):
            lags.append((date.fromisoformat(ed["date"]) - date.fromisoformat(m["email_date"])).days)
    st["lag_days_email_to_edition"] = dict(Counter(lags).most_common()) if lags else {}

    # title / news-like
    titles = [p["title"] for p in positives if p["title"]]
    st["title_len"] = {"avg_chars": round(sum(map(len, titles)) / max(1, len(titles)), 1),
                       "avg_words": round(sum(len(t.split()) for t in titles) / max(1, len(titles)), 1)}
    st["news_like_selected"] = {"count": sum(1 for t in titles if _NEWS_HINT.search(t)),
                                "share": round(sum(1 for t in titles if _NEWS_HINT.search(t)) / max(1, len(titles)), 3)}
    offered_labels = [l.label for r in records for l in r.links if not l.noise and len(l.label) >= 25]
    st["news_like_offered"] = {"count": sum(1 for t in offered_labels if _NEWS_HINT.search(t)),
                               "share": round(sum(1 for t in offered_labels if _NEWS_HINT.search(t)) / max(1, len(offered_labels)), 3)}
    # innovation picks: domain + keyword flavour
    innov = [p for p in positives if p["topic"] == "innovation"]
    st["innovation_domains"] = dict(Counter(p["domain"] for p in innov).most_common(25))
    st["topic_domains"] = {t: dict(Counter(p["domain"] for p in positives if p["topic"] == t).most_common(20))
                           for t in TOPICS}
    st["topic_authors"] = {t: dict(Counter(p["author"] for p in positives if p["topic"] == t and p["author"]).most_common(20))
                           for t in TOPICS}
    st["innovation_news_like"] = {"count": sum(1 for p in innov if _NEWS_HINT.search(p["title"] or "")),
                                  "share": round(sum(1 for p in innov if _NEWS_HINT.search(p["title"] or "")) / max(1, len(innov)), 3)}
    st["unmatched_positives"] = [{"title": m["title"], "domain": m["domain"], "edition": m["edition"],
                                  "topic": m["topic"]} for m in matches if m["method"] == "none"]
    return st


def render_stats_md(st: Dict[str, Any]) -> str:
    L: List[str] = []
    w = st["window"]
    L.append("# Newsletter triage — history stats\n")
    L.append(f"- emails: **{w['emails']}** ({w['first_email']} → {w['last_email']}), per week min/median/max "
             f"{st['emails_per_week_summary']['min']}/{st['emails_per_week_summary']['median']}/{st['emails_per_week_summary']['max']}")
    L.append(f"- editions: **{w['editions']}**, positives (selected articles): **{w['positives']}**")
    lk = st["links"]
    L.append(f"- kept links: {lk['kept_total']} ({lk['per_email_avg']}/email), via {lk['via']}")
    L.append(f"- positives → email match: **{st['match']['rate']:.1%}** — {st['match']}\n")
    L.append("## Per-edition caps observed\n")
    L.append(f"- HBR per edition histogram: {st['edition_caps']['hbr_hist']}")
    L.append(f"- max same author per edition: {st['edition_caps']['max_same_author_hist']}")
    L.append(f"- max same domain per edition: {st['edition_caps']['max_same_domain_hist']}")
    L.append(f"- must-read topic: {st['edition_caps']['must_read_topic_hist']}")
    L.append(f"- stars per topic: {st['stars']['per_topic']} · must-read by domain: {st['must_read']['by_domain']}\n")
    L.append(f"- topics: {st['topics']} · types: {st['types']}")
    L.append(f"- title length: {st['title_len']} · news-like selected {st['news_like_selected']} vs offered {st['news_like_offered']}")
    L.append(f"- innovation news-like: {st['innovation_news_like']}")
    for t, doms in st["topic_domains"].items():
        L.append(f"- **{t}** domains: {doms}")
        L.append(f"  authors: {st['topic_authors'][t]}")
    L.append(f"- email→edition lag (days: count): {st['lag_days_email_to_edition']}\n")
    L.append("## Senders (sorted by selected)\n")
    L.append("| sender | emails | offered | selected | eds w/ pick | hit/email | hit/link | stars | topics |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for s in st["senders"][:80]:
        L.append(f"| {s['sender'][:40]} | {s['emails']} | {s['offered_links']} | {s['selected']} | {s['editions_with_pick']} | "
                 f"{s['hit_rate_per_email']} | {s['hit_rate_per_link']} | {s['stars']} | {s['topics']} |")
    L.append("\n### Senders with ≥3 emails and 0 picks\n")
    L.append(", ".join(f"{s['sender']} ({s['emails']})" for s in st["senders_never_selected"][:80]) or "(none)")
    L.append("\n## Domains of selected articles\n")
    L.append("| domain | total | editions | max/edition | avg/edition |")
    L.append("|---|---:|---:|---:|---:|")
    for d, v in list(st["domains"].items())[:40]:
        L.append(f"| {d} | {v['total']} | {v['editions']} | {v['max_per_edition']} | {v['avg_per_edition']} |")
    L.append("\n## Authors of selected articles\n")
    L.append("| author | total | editions | max/edition | avg/edition |")
    L.append("|---|---:|---:|---:|---:|")
    for a, v in list(st["authors"].items())[:50]:
        L.append(f"| {a} | {v['total']} | {v['editions']} | {v['max_per_edition']} | {v['avg_per_edition']} |")
    L.append(f"\n- stars by domain: {st['stars_by_domain']}")
    L.append(f"- stars by author: {st['stars_by_author']}")
    L.append(f"- must-read by author: {st['must_read']['by_author']}")
    L.append("\n## Editions\n")
    L.append("| edition | date | n | HBR | max same author | max same domain | distinct authors | stars | must-read topic |")
    L.append("|---|---|---:|---:|---:|---:|---:|---:|---|")
    for e in st["editions"]:
        L.append(f"| {e['edition']} | {e['date']} | {e['n']} | {e['hbr']} | {e['max_same_author']} | {e['max_same_domain']} | "
                 f"{e['distinct_authors']} | {e['stars']} | {e['must_read_topic']} |")
    L.append(f"\n## Unmatched positives ({len(st['unmatched_positives'])})\n")
    for u in st["unmatched_positives"][:200]:
        L.append(f"- {u['edition']} · {u['topic']} · {u['domain']} · {u['title']}")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# CLI


def main(argv: Optional[List[str]] = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weeks", type=int, default=None)
    ap.add_argument("--no-gmail", action="store_true", help="use cached emails.jsonl only")
    ap.add_argument("--no-resolve", action="store_true", help="skip HTTP redirect resolution")
    ap.add_argument("--budget", type=int, default=None, help="max HTTP resolutions this run")
    ap.add_argument("--reextract", action="store_true", help="re-run link extraction from cached HTML")
    ap.add_argument("--limit", type=int, default=None, help="fetch at most N new emails (smoke test)")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    logging.getLogger("googleapiclient").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("notion_client").setLevel(logging.WARNING)

    cfg = load_block("newsletter_triage")
    weeks = args.weeks or int(cfg.get("history_weeks", 54))
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    if args.no_gmail:
        if args.reextract:
            records = reextract_cached(cfg)
        else:
            records = [gm.EmailRecord.from_json(r) for r in _read_jsonl(HISTORY_DIR / "emails.jsonl")]
        logger.info("📬 %d cached emails", len(records))
    else:
        records = pull_emails(weeks, cfg=cfg, reextract=args.reextract, limit=args.limit)

    positives, editions = load_positives(weeks)
    _write_jsonl(HISTORY_DIR / "positives.jsonl", positives)
    _write_jsonl(HISTORY_DIR / "editions.jsonl", editions)

    if not args.no_resolve:
        budget = args.budget if args.budget is not None else int(cfg.get("redirect_budget_history", 6000))
        resolve_offered(records, cfg=cfg, budget=budget)

    matches = join(positives, records)
    _write_jsonl(HISTORY_DIR / "matches.jsonl", matches)
    st = compute_stats(positives, editions, records, matches)
    (HISTORY_DIR / "stats.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    (HISTORY_DIR / "stats.md").write_text(render_stats_md(st), encoding="utf-8")
    logger.info("📊 match rate %.1f%% (%s) — stats at %s", st["match"]["rate"] * 100, st["match"],
                HISTORY_DIR / "stats.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
