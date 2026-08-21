"""Weekly triage engine — one review window → one edition-sized shortlist + report.

    python -m newsletter.triage.run [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--days 7]
                                    [--source gmail|cache] [--no-llm] [--no-fetch] [--top-k 90]
                                    [--model claude_haiku] [--out DIR] [--debug]
    python -m newsletter.triage.run --backtest N226[,N227,…]   # offline, from the history cache

Cadence (owner rule): a run covers the window from the last watermark (or
``--since``) to now and fills ONE edition (8/8/8); longer ranges are split into
7-day windows, oldest first, one report each. Never drains the inbox into one
edition. Nothing here writes to Notion, Gmail or Chrome.

Pipeline per window: emails → non-noise links (decoded / resolved) → drop
already-in-Notion → sender/domain priors (criteria.json + overrides.json) →
stage-A metadata scoring (batched LLM) → fetch + stage-B content scoring for
the top-K → vetoes (never-tier, paywalled, promo) → caps → shortlist →
``results/newsletter/triage/triage-<start>_<end>.md``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rapidfuzz import fuzz, utils as rf_utils  # noqa: E402

from config.console import force_utf8_stdio  # noqa: E402
from config.loader import load_block, load_full_config  # noqa: E402
from newsletter import llm, notion_io  # noqa: E402
from newsletter.cache import canonicalize_url  # noqa: E402
from newsletter.triage import db  # noqa: E402
from newsletter.triage import fetch as fx  # noqa: E402
from newsletter.triage import gmail as gm  # noqa: E402
from newsletter.triage import rank as rk  # noqa: E402
from newsletter.triage import report as rp  # noqa: E402
from newsletter.triage import score as sc  # noqa: E402
from newsletter.triage.criteria import CRITERIA_PATH  # noqa: E402
from newsletter.triage.state import STATE_PATH, TRIAGE_DIR, load_state, save_state  # noqa: E402,F401

logger = logging.getLogger("newsletter_triage.run")

HISTORY_DIR = TRIAGE_DIR / "history"
NOTION_URLS_CACHE = TRIAGE_DIR / "notion_urls.json"
DEFAULT_MODEL = "claude_haiku"

_STORE_STATE: Dict[str, Any] = {"checked": False, "ok": False}


class RunExists(RuntimeError):
    """A run for this (window, kind) is already stored — re-run with ``--force`` to replace it."""


def store_ok() -> bool:
    """One probe per process: is the Supabase store reachable with the schema applied? Degrades to
    report-only (stats.store = 'unavailable') instead of failing the triage — the state is visible, not silent."""
    if not _STORE_STATE["checked"]:
        _STORE_STATE["checked"] = True
        try:
            db.ensure_schema()
            _STORE_STATE["ok"] = True
        except Exception as err:  # noqa: BLE001 — SchemaMissing, network, bad key: all → report-only
            logger.error("❌ triage store unavailable (%s) — running report-only, nothing stored", str(err)[:200])
    return bool(_STORE_STATE["ok"])


# ---------------------------------------------------------------------------
# inputs


def load_criteria() -> Dict[str, Any]:
    if not CRITERIA_PATH.exists():
        raise FileNotFoundError(f"{CRITERIA_PATH} missing — run `python -m newsletter.triage.criteria`")
    return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))


def _norm(text: str) -> str:
    return rf_utils.default_process(text or "")


def load_notion_urls(*, ttl_hours: int = 24, refresh: bool = False,
                     created_before: Optional[str] = None) -> Tuple[set, Dict[str, str]]:
    """Canonical URLs (+ normalised titles) of articles already in Notion — the dedupe set.

    ``created_before`` (YYYY-MM-DD) restricts the set to articles created before
    that day — a backtest must not "know" the picks it is trying to reproduce.
    """
    data: Optional[Dict[str, Any]] = None
    if NOTION_URLS_CACHE.exists() and not refresh:
        try:
            d = json.loads(NOTION_URLS_CACHE.read_text(encoding="utf-8"))
            if time.time() - float(d.get("fetched_at", 0)) < ttl_hours * 3600 and "created" in d:
                data = d
        except Exception:
            data = None
    if data is None:
        cfg = load_full_config()
        na = cfg["newsletter_archive"]
        client = notion_io.init_client(cfg["notion"]["api_token"])
        created: Dict[str, str] = {}
        titles: Dict[str, List[str]] = {}
        for page in notion_io._iter_database(client, na["articles_db_id"]):  # noqa: SLF001
            link = (page["properties"].get("link", {}).get("url") or "").strip()
            if not link:
                continue
            c = canonicalize_url(gm.canonical_substack(link))
            day = (page.get("created_time") or "")[:10]
            if c not in created or day < created[c]:
                created[c] = day
            t = notion_io._read_title(page, "article")  # noqa: SLF001
            if t:
                titles[_norm(t)] = [c, day]
        data = {"fetched_at": time.time(), "created": created, "titles": titles}
        NOTION_URLS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        NOTION_URLS_CACHE.write_text(json.dumps(data), encoding="utf-8")
        logger.info("🗂️ Notion dedupe set: %d article URLs", len(created))
    created = data["created"]
    titles = data["titles"]
    if created_before:
        urls = {c for c, day in created.items() if day < created_before}
        tmap = {t: v[0] for t, v in titles.items() if v[1] < created_before}
    else:
        urls = set(created)
        tmap = {t: v[0] for t, v in titles.items()}
    return urls, tmap


def emails_from_cache(start: date, end: date) -> List[gm.EmailRecord]:
    path = HISTORY_DIR / "emails.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run `python -m newsletter.triage.history` first")
    lo, hi = start.isoformat(), end.isoformat()
    out: List[gm.EmailRecord] = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            if not line.strip():
                continue
            d = json.loads(line)
            if lo <= d.get("timestamp", "")[:10] < hi:
                out.append(gm.EmailRecord.from_json(d))
    out.sort(key=lambda r: r.timestamp)
    return out


def emails_from_gmail(start: date, end: date, *, cfg: Dict[str, Any], budget: Optional[int] = None) -> List[gm.EmailRecord]:
    tm = gm.build_mailbox(cfg)
    try:
        query = f"after:{start:%Y/%m/%d} before:{end:%Y/%m/%d}"
        search = gm.label_search(tm, cfg.get("gmail_label", "newsletters"), query=query)
        raws = gm.fetch_raw_messages(tm, search)
    finally:
        tm.close()
    min_anchor = int(cfg.get("min_anchor_chars", gm.DEFAULT_MIN_ANCHOR_CHARS))
    records = [gm.build_record(r, min_anchor_chars=min_anchor)[0] for r in raws]
    records = [r for r in records if start.isoformat() <= r.timestamp[:10] < end.isoformat()]
    records.sort(key=lambda r: r.timestamp)
    cache = gm.RedirectCache(TRIAGE_DIR / "redirects.json")
    stats = gm.resolve_links([l for r in records for l in r.links], cache,
                             workers=int(cfg.get("redirect_workers", 8)),
                             timeout=float(cfg.get("redirect_timeout_s", 10)), budget=budget)
    logger.info("📬 %d emails in window, redirect resolve %s", len(records), stats)
    return records


# ---------------------------------------------------------------------------
# candidates


def build_candidates(records: Sequence[gm.EmailRecord], priors: sc.Priors, notion_urls: set,
                     notion_titles: Dict[str, str]) -> Tuple[List[rk.Candidate], Dict[str, List[rk.Candidate]]]:
    cands: List[rk.Candidate] = []
    by_email: Dict[str, List[rk.Candidate]] = defaultdict(list)
    for rec in records:
        weight, basis, is_new = priors.sender(rec.sender_address)
        seen_in_email: set = set()
        for i, link in enumerate(rec.links):
            if link.noise:
                continue
            canonical = link.canonical
            if link.resolved:
                if canonical in seen_in_email:
                    continue          # same article via two anchors (resolved after extraction-time dedupe)
                seen_in_email.add(canonical)
            dom = fx.domain_of(link.best_url)
            bonus, _ = priors.domain(dom)
            c = rk.Candidate(cid=f"{rec.message_id}:{i}", message_id=rec.message_id, sender_name=rec.sender_name,
                             sender_address=rec.sender_address, subject=rec.subject, email_ts=rec.timestamp,
                             label=link.label, url=link.best_url, canonical=canonical, domain=dom,
                             sender_weight=weight, sender_basis=basis, is_new_sender=is_new, domain_bonus=bonus,
                             topic=priors.topic_prior(rec.sender_address, dom))
            if link.resolved and canonical in notion_urls:
                c.in_notion = True
            elif link.label and _norm(link.label) in notion_titles:
                c.in_notion = True
            if not link.resolved:
                c.reason = "redirect unresolved"
            cands.append(c)
            by_email[rec.message_id].append(c)
    return cands, by_email


# ---------------------------------------------------------------------------
# one window


def run_window(start: date, end: date, *, cfg: Dict[str, Any], criteria: Dict[str, Any], source: str,
               use_llm: bool, use_fetch: bool, top_k: int, model: str, out_dir: Path,
               edition_hint: Optional[str] = None, backtest: Optional[Dict[str, Any]] = None,
               limit_links: Optional[int] = None, force: bool = False) -> Tuple[Path, rk.Selection, Dict[str, Any]]:
    """One window → report + stored run. Refuses to replace a stored (window, kind) unless ``force``
    (:class:`RunExists`). Store failures after registration mark the run ``failed`` and re-raise."""
    kind = "backtest" if backtest is not None else "live"
    run_id: Optional[int] = None
    if store_ok():
        prev = db.run_for_window(start, end, kind)
        if prev and not force:
            raise RunExists(f"window {start} → {end} ({kind}) already stored as run {prev['id']} "
                            f"({prev.get('status')}, {str(prev.get('finished_at') or prev.get('started_at'))[:16]}) "
                            f"— re-run with --force to replace it")
        if edition_hint is None and kind == "live":
            edition_hint = db.next_edition_number()
        run_id = db.register_run(start, end, kind=kind, edition=backtest.get("edition") if backtest else None,
                                 source=source, model=model, criteria_version=str(criteria.get("version", "")))
    try:
        path, sel, stats, cands, emails_view = _run_window_body(
            start, end, cfg=cfg, criteria=criteria, source=source, use_llm=use_llm, use_fetch=use_fetch,
            top_k=top_k, model=model, out_dir=out_dir, edition_hint=edition_hint or "next free edition",
            backtest=backtest, limit_links=limit_links)
    except Exception as err:
        if run_id is not None:
            db.mark_run(run_id, status="failed", stats={"error": str(err)[:500]})
        raise
    if run_id is None:
        stats["store"] = "unavailable"
        return path, sel, stats
    n_e, n_c = db.store_run_results(run_id, emails_view, db.candidate_rows(cands, sel))
    if backtest is not None:
        n_d = db.save_decisions(start, end, _truth_decisions(cands, sel, backtest))
        logger.info("🧪 stored %d truth decisions for the backtest window", n_d)
    stats["store"] = f"run {run_id}"
    db.mark_run(run_id, status="done", stats=stats, report_path=str(path))
    logger.info("🗄️ stored run %s: %d emails, %d candidates", run_id, n_e, n_c)
    return path, sel, stats


def _truth_decisions(cands: List[rk.Candidate], sel: rk.Selection, bt: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Backtest → decisions as if the owner had reviewed the table: every candidate that was a real pick
    in some edition = yes (star / must-read from the history), every other suggested pick / runner-up = no."""
    canon, titles = bt["_canon"], bt["_titles"]
    flags = bt.get("_truth_flags", {})
    shortlisted = {c.cid for t in rk.TOPICS for c in sel.picks[t] + sel.runners[t]}
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for c in cands:
        truth = _is_truth(c, canon, titles)
        if not truth and c.cid not in shortlisted:
            continue
        if c.canonical in seen:
            continue
        seen.add(c.canonical)
        st, mr = flags.get(c.canonical, (False, False))
        rows.append({"canonical": c.canonical, "cid": c.cid, "sender_address": c.sender_address, "title": c.display_title,
                     "url": c.url, "topic": c.topic, "pick": truth, "star": truth and st, "must_read": truth and mr,
                     "note": "backtest: real pick" if truth else "backtest: suggested, not picked"})
    return rows


def _run_window_body(start: date, end: date, *, cfg: Dict[str, Any], criteria: Dict[str, Any], source: str,
                     use_llm: bool, use_fetch: bool, top_k: int, model: str, out_dir: Path,
                     edition_hint: str, backtest: Optional[Dict[str, Any]],
                     limit_links: Optional[int]) -> Tuple[Path, rk.Selection, Dict[str, Any], List[rk.Candidate], List[Dict[str, Any]]]:
    t0 = time.monotonic()
    base_url = cfg.get("llm_hub_base_url") or load_full_config().get("newsletter_archive", {}).get("llm_hub_base_url", "http://127.0.0.1:8000")
    priors = sc.Priors(criteria)
    rules = criteria.get("rules", {})
    records = emails_from_cache(start, end) if source == "cache" else emails_from_gmail(start, end, cfg=cfg)
    notion_urls, notion_titles = load_notion_urls(created_before=start.isoformat() if backtest is not None else None)
    cands, by_email = build_candidates(records, priors, notion_urls, notion_titles)
    if limit_links:
        cands = cands[:limit_links]
    stats: Dict[str, Any] = {"emails": len(records), "links": len(cands),
                             "duplicates_in_notion": sum(1 for c in cands if c.in_notion),
                             "unresolved_links": sum(1 for c in cands if c.reason == "redirect unresolved")}
    llm_calls = 0
    llm_cache = sc.LLMCache()

    # stage A — metadata, for everything that can still be selected
    stage_a = [c for c in cands if not c.in_notion and c.sender_weight > 0]
    if use_llm and stage_a:
        if not llm.health_check(base_url=base_url, model=model):
            logger.error("❌ LLM hub unreachable at %s — producing a rule-only report", base_url)
            use_llm = False
    if use_llm and stage_a:
        items = [{"sender": c.sender_name, "label": c.label or c.url, "domain": c.domain,
                  "path": urlsplit(c.url).path} for c in stage_a]
        metas = sc.score_metadata(items, criteria, base_url=base_url, model=model,
                                  workers=int(cfg.get("llm_workers", 3)) + 1, cache=llm_cache)
        llm_calls += (llm_cache.misses + 24) // 25
        for c, m in zip(stage_a, metas):
            c.meta = m
            c.score = sc.combine(sender_weight=c.sender_weight, domain_bonus=c.domain_bonus, meta=m, content=None)
        stats["stage_a_scored"] = sum(1 for c in stage_a if c.meta and c.meta.ok)
    else:
        for c in stage_a:   # rule-only: sender weight + domain prior as a weak score
            c.score = round(min(10.0, c.sender_weight * (1 + c.domain_bonus) * 4.0), 2) if len(c.label) >= 25 else None
            c.reason = "rule-only score (no LLM)" if c.score is not None else "short anchor, no LLM"

    # stage B — fetch + content for the top-K
    ranked = sorted([c for c in stage_a if c.score is not None], key=lambda c: -(c.score or 0))
    depth = max(top_k, len(stage_a) // 12)          # a 2-week backlog window gets proportionally more depth
    stage_b = ranked[:depth]
    fetched_ok = 0
    if use_fetch and stage_b:
        cache = fx.FetchCache()
        fetched = fx.fetch_many([c.url for c in stage_b], cache, workers=int(cfg.get("fetch_workers", 8)),
                                timeout=float(cfg.get("fetch_timeout_s", 15)))
        for c in stage_b:
            f = fetched.get(c.url)
            if not f:
                continue
            c.fetched_ok, c.kind, c.paywalled = f.ok, f.kind, f.paywalled
            if f.title:
                c.title = f.title
            if f.author:
                c.author = f.author
            if f.ok:
                fetched_ok += 1
            elif f.error:
                c.reason = f"fetch: {f.error}"
            if f.final_url:
                canon = canonicalize_url(gm.canonical_substack(f.final_url))
                if canon in notion_urls:
                    c.in_notion = True
                c.domain = fx.domain_of(f.final_url) or c.domain
            if f.title and _norm(f.title) in notion_titles:
                c.in_notion = True
        stats["fetched"] = len(stage_b)
        stats["fetched_ok"] = fetched_ok
        stats["paywalled"] = sum(1 for c in stage_b if c.paywalled)
        if use_llm:
            todo = [c for c in stage_b if c.fetched_ok and not c.in_notion and not c.paywalled and c.kind == "article"]
            def work(c: rk.Candidate) -> None:
                f = cache.get(c.url)
                c.content = sc.score_content(title=c.title or c.label, author=c.author or "", sender=c.sender_name,
                                             domain=c.domain, excerpt=f.excerpt if f else "", criteria=criteria,
                                             base_url=base_url, model=model, cache=llm_cache)
            with ThreadPoolExecutor(max_workers=int(cfg.get("llm_workers", 3))) as pool:
                list(pool.map(work, todo))
            llm_cache.flush()
            llm_calls += len(todo)
            for c in todo:
                c.score = sc.combine(sender_weight=c.sender_weight, domain_bonus=c.domain_bonus, meta=c.meta, content=c.content)
                if c.content and c.content.ok:
                    c.summary = c.content.summary
            stats["stage_b_scored"] = sum(1 for c in todo if c.content and c.content.ok)
    stats["scored"] = sum(1 for c in cands if c.score is not None)
    stats["llm_calls"] = llm_calls

    multi = {a for a in priors.ranked if priors.multi_pick(a)}
    sel = rk.select(cands, rules, multi_pick_addrs=multi)
    stats["selected"] = len(sel.all_picks())
    stats["verdicts"] = dict(Counter(c.verdict for c in cands))
    stats["elapsed_s"] = round(time.monotonic() - t0, 1)

    # senders bookkeeping
    new_senders: Dict[str, Tuple[str, str, int]] = {}
    floor: Counter = Counter()
    emails_view: List[Dict[str, Any]] = []
    for rec in records:
        w, basis, is_new = priors.sender(rec.sender_address)
        if is_new:
            name, addr, n = new_senders.get(rec.sender_address, (rec.sender_name, rec.sender_address, 0))
            new_senders[rec.sender_address] = (name, addr, n + 1)
        if basis.startswith("floor"):
            floor[rec.sender_name] += 1
        emails_view.append({"message_id": rec.message_id, "sender_name": rec.sender_name,
                            "sender_address": rec.sender_address, "subject": rec.subject,
                            "timestamp": rec.timestamp, "sender_basis": basis, "is_new": is_new})

    if backtest is not None:
        backtest.update(_backtest_metrics(sel, cands, backtest))

    title = f"Newsletter triage — {start} → {end}" + (f" (backtest {backtest['edition']})" if backtest else "")
    md = rp.render_report(title=title, window_start=start.isoformat(), window_end=end.isoformat(), edition_hint=edition_hint,
                          sel=sel, emails=emails_view, cands_by_email=by_email, new_senders=list(new_senders.values()),
                          floor_senders=floor.most_common(), stats=stats, backtest=backtest)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"triage-{start}_{end}" + (f"-backtest-{backtest['edition']}" if backtest else "") + ".md"
    path = out_dir / name
    path.write_text(md, encoding="utf-8")
    logger.info("📝 report: %s — %d picks (%s) in %.0fs", path, stats["selected"],
                ", ".join(f"{t[:6]} {len(sel.picks[t])}" for t in rk.TOPICS), stats["elapsed_s"])
    return path, sel, stats, cands, emails_view


# ---------------------------------------------------------------------------
# backtest


def _truth_sets() -> Tuple[set, Dict[str, str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    positives = [json.loads(l) for l in (HISTORY_DIR / "positives.jsonl").open(encoding="utf-8") if l.strip()]
    matches = [json.loads(l) for l in (HISTORY_DIR / "matches.jsonl").open(encoding="utf-8") if l.strip()]
    canon = {p["canonical"] for p in positives if p.get("canonical")}
    titles = {_norm(p["title"]): p["canonical"] for p in positives if p.get("title")}
    return canon, titles, positives, matches


def _is_truth(c: rk.Candidate, canon: set, titles: Dict[str, str]) -> bool:
    if c.canonical in canon:
        return True
    for t in (c.title, c.label):
        if t and _norm(t) in titles:
            return True
    return False


def _backtest_metrics(sel: rk.Selection, cands: List[rk.Candidate], bt: Dict[str, Any]) -> Dict[str, Any]:
    canon, titles = bt["_canon"], bt["_titles"]
    picks = sel.all_picks()
    runners = [c for t in rk.TOPICS for c in sel.runners[t]]
    hits = sum(1 for c in picks if _is_truth(c, canon, titles))
    truth_ids = bt["_truth_msg_canon"]          # {(message_id, canonical)} of edition picks sourced in window
    truth_canon = {cn for _m, cn in truth_ids}
    truth_titles = bt["_truth_titles"]
    def recalled_by(group: List[rk.Candidate]) -> int:
        got = set()
        for c in group:
            if c.canonical in truth_canon:
                got.add(c.canonical)
            else:
                for t in (c.title, c.label):
                    if t and _norm(t) in truth_titles:
                        got.add(truth_titles[_norm(t)])
        return len(got)
    n_truth = len(truth_canon)
    rec_p = recalled_by(picks)
    rec_r = recalled_by(picks + runners)
    missed = [bt["_truth_names"].get(cn, cn) for cn in truth_canon
              if cn not in {c.canonical for c in picks + runners}][:8]
    # per-truth diagnostics: where did each real pick end up?
    by_canon: Dict[str, List[rk.Candidate]] = defaultdict(list)
    by_title: Dict[str, List[rk.Candidate]] = defaultdict(list)
    for c in cands:
        by_canon[c.canonical].append(c)
        for t in (c.title, c.label):
            if t:
                by_title[_norm(t)].append(c)
    diag: List[str] = []
    for cn in sorted(truth_canon, key=lambda x: bt["_truth_names"].get(x, x)):
        name = bt["_truth_names"].get(cn, cn)
        found = by_canon.get(cn) or by_title.get(_norm(name)) or []
        if not found:
            diag.append(f"❌ not a candidate · {name[:80]} · {cn[:70]}")
            continue
        c = max(found, key=lambda x: (x.score or -1))
        diag.append(f"{'✅' if c.verdict == 'selected' else '🟡' if c.verdict == 'runner-up' else '⚪'} "
                    f"{c.verdict:9} {c.score if c.score is not None else '–'} · {c.topic or '–'} · {name[:70]} · {c.reason[:60]}")
    return {"shortlist": len(picks), "hits": hits, "precision": hits / max(1, len(picks)),
            "truth": n_truth, "recalled": rec_p, "recall": rec_p / max(1, n_truth),
            "recall_runners": rec_r / max(1, n_truth), "missed_preview": "; ".join(missed), "diag": diag}


def backtest_edition(number: str, *, cfg: Dict[str, Any], criteria: Dict[str, Any], use_llm: bool, use_fetch: bool,
                     top_k: int, model: str, out_dir: Path, window_days: int = 14, offset_days: int = 7,
                     force: bool = False) -> Dict[str, Any]:
    editions = [json.loads(l) for l in (HISTORY_DIR / "editions.jsonl").open(encoding="utf-8") if l.strip()]
    ed = next((e for e in editions if e["number"] == number), None)
    if not ed or not ed.get("date"):
        raise SystemExit(f"edition {number} not in history/editions.jsonl")
    d = date.fromisoformat(ed["date"])
    end = d - timedelta(days=offset_days)
    start = end - timedelta(days=window_days)
    canon, titles, positives, matches = _truth_sets()
    truth_msg_canon = set()
    truth_titles: Dict[str, str] = {}
    names: Dict[str, str] = {}
    pos_by_id = {p["article_id"]: p for p in positives}
    flags = {p["canonical"]: (bool(p.get("star")), bool(p.get("must_read"))) for p in positives if p.get("canonical")}
    for m in matches:
        if m["edition"] != number or not m.get("email_date"):
            continue
        if start.isoformat() <= m["email_date"] < end.isoformat():
            p = pos_by_id.get(m["article_id"])
            if p and p.get("canonical"):
                truth_msg_canon.add((m.get("message_id"), p["canonical"]))
                truth_titles[_norm(p["title"])] = p["canonical"]
                names[p["canonical"]] = p["title"]
    bt = {"edition": number, "_canon": canon, "_titles": titles, "_truth_msg_canon": truth_msg_canon,
          "_truth_titles": truth_titles, "_truth_names": names, "_truth_flags": flags}
    logger.info("🧪 backtest %s (%s): window %s → %s, %d truth picks sourced in window", number, ed["date"], start, end,
                len({cn for _m, cn in truth_msg_canon}))
    path, sel, stats = run_window(start, end, cfg=cfg, criteria=criteria, source="cache", use_llm=use_llm,
                                  use_fetch=use_fetch, top_k=top_k, model=model, out_dir=out_dir,
                                  edition_hint=number, backtest=bt, force=force)
    return {k: v for k, v in bt.items() if not k.startswith("_")} | {"report": str(path), "stats": stats}


# ---------------------------------------------------------------------------
# CLI


def _windows(start: date, end: date, days: int = 7) -> List[Tuple[date, date]]:
    out: List[Tuple[date, date]] = []
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        if (end - nxt).days < 2 and nxt != end:   # avoid a 1-day tail window
            nxt = end
        out.append((cur, nxt))
        cur = nxt
    return out


def main(argv: Optional[List[str]] = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="window start (YYYY-MM-DD); default = state.reviewed_until or today-7")
    ap.add_argument("--until", help="window end, exclusive (YYYY-MM-DD); default = tomorrow")
    ap.add_argument("--days", type=int, default=7, help="window length when splitting a backlog")
    ap.add_argument("--source", choices=("gmail", "cache"), default="gmail")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--limit-links", type=int, default=None)
    ap.add_argument("--out", default=str(TRIAGE_DIR))
    ap.add_argument("--backtest", help="comma-separated edition numbers, e.g. N226,N227")
    ap.add_argument("--force", action="store_true", help="replace a run already stored for the same window")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    for noisy in ("httpx", "httpcore", "hpack", "notion_client", "urllib3", "googleapiclient",
                  "readability", "readability.readability"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cfg = load_block("newsletter_triage")
    criteria = load_criteria()
    model = args.model or cfg.get("llm_model", DEFAULT_MODEL)
    top_k = args.top_k or int(cfg.get("stage_b_top_k", 90))
    out_dir = Path(args.out)

    try:
        return _main_runs(args, cfg=cfg, criteria=criteria, model=model, top_k=top_k, out_dir=out_dir)
    except RunExists as err:
        logger.warning("⚠️ %s", err)
        print(f"⚠️ {err}")
        return 3


def _main_runs(args: argparse.Namespace, *, cfg: Dict[str, Any], criteria: Dict[str, Any], model: str,
               top_k: int, out_dir: Path) -> int:
    if args.backtest:
        results = []
        for num in [n.strip() for n in args.backtest.split(",") if n.strip()]:
            results.append(backtest_edition(num, cfg=cfg, criteria=criteria, use_llm=not args.no_llm,
                                            use_fetch=not args.no_fetch, top_k=top_k, model=model, out_dir=out_dir,
                                            force=args.force))
        print("\n=== backtest summary ===")
        for r in results:
            print(f"{r['edition']}: precision {r['precision']:.0%} ({r['hits']}/{r['shortlist']}) · "
                  f"recall {r['recall']:.0%} ({r['recalled']}/{r['truth']}) · +runners {r['recall_runners']:.0%} · {r['report']}")
        (out_dir / "backtest-summary.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return 0

    state = load_state()
    today = date.today()
    if args.since:
        start = date.fromisoformat(args.since)
    elif state.get("reviewed_until"):
        start = date.fromisoformat(state["reviewed_until"][:10])
    else:
        start = today - timedelta(days=7)
    end = date.fromisoformat(args.until) if args.until else today + timedelta(days=1)
    windows = _windows(start, end, args.days)
    logger.info("🗓️ %d window(s): %s", len(windows), ", ".join(f"{a}→{b}" for a, b in windows))
    reports = []
    for a, b in windows:
        path, sel, stats = run_window(a, b, cfg=cfg, criteria=criteria, source=args.source, use_llm=not args.no_llm,
                                      use_fetch=not args.no_fetch, top_k=top_k, model=model, out_dir=out_dir,
                                      limit_links=args.limit_links, force=args.force)
        reports.append({"window": [a.isoformat(), b.isoformat()], "report": str(path), "selected": stats["selected"]})
    state.setdefault("runs", []).append({"at": datetime.now(timezone.utc).isoformat(), "windows": reports})
    state["last_window_end"] = end.isoformat()
    save_state(state)
    for r in reports:
        print(f"{r['window'][0]} → {r['window'][1]}: {r['selected']} picks → {r['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
