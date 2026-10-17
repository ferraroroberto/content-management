"""Jev pilot (#285) — replay stored, reviewed runs through TypeSafe Jev and measure it against the owner.

    python -m newsletter.triage.jev_pilot --run 17 [--run 16 …] [--repeat]   # replay through the hub
    python -m newsletter.triage.jev_pilot --shadow [--run 17 …]              # stored `both` runs, no Jev calls
    python -m newsletter.triage.jev_pilot --run 17 --legacy [--capture] [--allow-unreviewed]   # both engines (#392)

Read-only on the store: it reads ``triage_runs`` / ``triage_emails`` / ``triage_candidates`` /
``triage_decisions`` and never writes, never touches Notion, and is not wired into ``run.py``.
One artefact folder per invocation under ``tmp/jev_pilot/<UTC stamp>/``: ``summary.json``,
``summary.md`` and one ``items-run-<id>.json`` per run (titles included — the folder is
gitignored and local; the findings comment carries aggregates only).

Replay inputs are the stored Stage A items (candidates with a ``meta`` score): sender name from
``triage_emails``, anchor text = the stored display title, domain, URL path. For the ~90 links per
run that Stage B fetched, the stored title is the page title rather than the email anchor — a
small advantage for Jev on exactly the links that matter most; noted in the summary.

``--shadow`` reads the shadow file next to each run's stored report, so weekly runs are compared with
the same measures in either direction: ``<report>.jev-shadow.json`` for a ``both`` run (legacy decided)
and ``<report>.legacy-shadow.json`` for a ``jev+legacy`` run (Jev decided, #394). Each run reports
``decided_by``; on a Jev-decided run the stored answers are Jev's and legacy's come from the shadow file.

Measures (per run and pooled):

* owner agreement — of the owner's picks, share each model scored fit ≥ 3; of links each model
  scored fit ≥ 4, share the owner picked; topic agreement on picked links;
* model agreement — topic match, fit within ±1, news / promo match;
* calibration — Jev answers bucketed by confidence (<0.5, 0.5–0.7, 0.7–0.9, ≥0.9): topic vs the
  owner's topic on picked links, fit band (≥3) vs the owner's yes/no on decided links, topic vs
  legacy on all links; plus the share a confidence threshold would auto-accept;
* reliability — share of items without a usable answer (legacy ``meta.ok``; Jev ``not evaluated``);
* latency and tokens — from the hub's observability ring (``/admin/api/hub/requests/recent``,
  polled during the replay) and its per-model counters (``--legacy`` adds client timers, below);
* consistency (``--repeat``) — the same run scored twice, share of topic / fit-band answers that change.

``--legacy`` (#392) re-scores the same items with legacy Stage A too — fresh, the LLM cache bypassed,
same model and workers as ``run.py`` — interleaved with Jev (the first engine alternates per run). It
adds, per engine: calls, wall clock, ms per link, the concurrency actually reached (most calls open at
once) and tokens, all from client timers and each response's ``usage`` so both engines are measured the
same way (the ring can't tell this replay's ``/v1/messages`` calls from another client's); the owner
measures on fresh legacy answers; reproduction (fresh legacy vs the stored answers, fresh Jev vs the
run's shadow file when one exists); and Stage B entry — which links each engine's answers would send to
the fetch, against what the run fetched and the owner's picks. ``--capture`` keeps one representative
request + raw response per engine in ``capture.json`` (two extra calls). ``--allow-unreviewed`` admits a
run with no applied review (owner measures empty).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
import time
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.console import force_utf8_stdio  # noqa: E402
from config.loader import load_block  # noqa: E402
from newsletter import llm  # noqa: E402
from newsletter.triage import db  # noqa: E402
from newsletter.triage import jev as jx  # noqa: E402
from newsletter.triage import score as sc  # noqa: E402
from newsletter.triage.criteria import CRITERIA_PATH  # noqa: E402
from newsletter.triage.score import MetaScore  # noqa: E402

logger = logging.getLogger("newsletter_triage.jev_pilot")

OUT_ROOT = REPO_ROOT / "tmp" / "jev_pilot"
BUCKETS: Tuple[Tuple[str, float, float], ...] = (("<0.5", 0.0, 0.5), ("0.5–0.7", 0.5, 0.7), ("0.7–0.9", 0.7, 0.9),
                                                 ("≥0.9", 0.9, 1.01))
THRESHOLDS = (0.7, 0.9)
RING_POLL_S = 3.0

_EN = {"the", "and", "of", "to", "in", "for", "is", "how", "why", "what", "your", "with", "you", "on", "a", "are"}
_OTHER = {"el", "la", "los", "las", "de", "del", "y", "que", "en", "para", "con", "por", "una", "un", "il", "di",
          "che", "per", "le", "les", "des", "et", "est", "pour", "der", "die", "und", "das", "mit", "não", "como"}


# ---------------------------------------------------------------------------
# inputs


def lang(text: str) -> str:
    """Crude stopword vote — ``en`` / ``other`` / ``unknown`` (too few words to tell)."""
    words = re.findall(r"[a-záéíóúàèìòùâêîôûãõçñü]+", (text or "").lower())
    en, other = sum(w in _EN for w in words), sum(w in _OTHER for w in words)
    if en == other == 0:
        return "unknown"
    return "en" if en >= other else "other"


def decided_by(run: Dict[str, Any]) -> str:
    """``jev`` when the run's stored Stage A answers are Jev's (``jev`` / ``jev+legacy``), else ``legacy``."""
    return "jev" if (run.get("stats") or {}).get("stage_a_engine") in jx.JEV_DECIDES else "legacy"


def _shadow_doc(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The run's shadow file — the engine that did *not* decide: Jev's for a legacy-decided run (``both``),
    legacy's for a Jev-decided one (``jev+legacy``, #394)."""
    if not run.get("report_path"):
        return None
    report = Path(run["report_path"])
    return jx.read_shadow(jx.legacy_shadow_path(report) if decided_by(run) == "jev" else jx.shadow_path(report))


def _stored_jev(meta: Dict[str, Any]) -> Dict[str, Any]:
    """A Jev-decided run's stored ``meta`` (``asdict(JevMetaScore)``) → the Jev row shape."""
    names = {f.name for f in fields(jx.JevMetaScore)}
    return jev_answer(jx.JevMetaScore(**{k: v for k, v in meta.items() if k in names}))


def stage_a_items(run_id: int, *, require_review: bool = True) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """A stored run → (run row, Stage A items with legacy answer + owner decision).

    Direction-aware (#394): on a legacy-decided run ``legacy`` is the stored answer; on a Jev-decided run it
    comes from the legacy shadow file (``not evaluated`` without one) and the stored answer is ``jev_stored``.
    The run row gains ``decided_by`` and ``shadow`` (the shadow file, or None).
    ``require_review=False`` admits a run with no applied review: every owner measure is then empty.
    """
    run = db.get_run(run_id)
    if not run:
        raise SystemExit(f"run {run_id} not found")
    if not db.get_review(run["window_start"], run["window_end"]):
        if require_review:
            raise SystemExit(f"run {run_id} has no applied review — nothing to compare against")
        logger.warning("⚠️ run %s has no applied review — owner measures stay empty", run_id)
    run["decided_by"], run["shadow"] = decided_by(run), _shadow_doc(run)
    legacy_rows = {r["cid"]: r for r in (run["shadow"] or {}).get("rows", [])} if run["decided_by"] == "jev" else {}
    senders = {e["message_id"]: e.get("sender_name") or "" for e in db.emails(run_id)}
    decisions = {d["canonical"]: d for d in db.load_decisions(run["window_start"], run["window_end"])
                 if d.get("canonical")}
    items: List[Dict[str, Any]] = []
    for c in db.candidates(run_id):
        meta = c.get("meta")
        if meta is None:
            continue
        d = decisions.get(c.get("canonical") or "")
        title = c.get("title") or c.get("url") or ""
        if run["decided_by"] == "jev":
            row = legacy_rows.get(c["cid"]) or {"status": jx.NOT_EVALUATED}
            legacy = {"ok": row["status"] == "ok", "topic": row.get("topic"), "fit": row.get("fit"),
                      "news": bool(row.get("news")), "promo": bool(row.get("promo"))}
        else:
            legacy = {"ok": bool(meta.get("ok")), "topic": meta.get("topic"), "fit": meta.get("fit"),
                      "news": bool(meta.get("news")), "promo": bool(meta.get("promo"))}
        item = {
            "cid": c["cid"], "title": title, "domain": c.get("domain") or "", "suggested": c.get("suggested"),
            "sender_weight": float(c.get("sender_weight") or 0.0), "fetched": c.get("fetched_ok") is not None,
            "input": {"sender": senders.get(c.get("message_id"), ""), "label": title, "domain": c.get("domain") or "",
                      "path": urlsplit(c.get("url") or "").path},
            "lang": lang(title),
            "legacy": legacy,
            "owner": {"decided": d is not None, "pick": bool(d and d.get("pick")),
                      "topic": d.get("topic") if d else None},
        }
        if run["decided_by"] == "jev":
            item["jev_stored"] = _stored_jev(meta)
        items.append(item)
    return run, items


def jev_answer(m: jx.JevMetaScore) -> Dict[str, Any]:
    row = jx.shadow_rows([""], [m], model="")[0]
    row.pop("cid")
    row.pop("probs")
    return row


# ---------------------------------------------------------------------------
# hub observability ring


class RingRecorder:
    """Polls the hub's request ring (200 entries) while a replay runs and keeps every ``/v1/systemone``
    record exactly once — the ring is the source for wall time, latency and tokens."""

    def __init__(self, base_url: str) -> None:
        self.url = base_url.rstrip("/") + "/admin/api/hub/requests/recent?limit=200"
        self.records: Dict[Tuple[float, str], Dict[str, Any]] = {}
        self.polls_failed = 0
        self._started = time.time()      # the ring stamps ``ts`` with time.time() when a request completes
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hub-ring")

    def __enter__(self) -> "RingRecorder":
        self._started = time.time()
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=10)
        self._poll()

    def _loop(self) -> None:
        while not self._stop.wait(RING_POLL_S):
            self._poll()

    def _poll(self) -> None:
        try:
            recs = requests.get(self.url, timeout=5).json().get("requests", [])
        except (requests.RequestException, ValueError):
            self.polls_failed += 1
            return
        for r in recs:
            if r.get("path") == jx.ROUTE and r.get("ts", 0) >= self._started:
                self.records[(r["ts"], r.get("trace_id") or "")] = r

    def summary(self, n_links: int) -> Dict[str, Any]:
        recs = list(self.records.values())
        if not recs:
            return {"source": "hub ring", "requests": 0, "polls_failed": self.polls_failed}
        lat = sorted(r.get("latency_ms", 0.0) for r in recs)
        first = min(r["ts"] - r.get("latency_ms", 0.0) / 1000 for r in recs)
        last = max(r["ts"] for r in recs)
        ok = [r for r in recs if r.get("status") == 200]
        return {"source": "hub ring", "requests": len(recs), "ok": len(ok), "links": n_links,
                "wall_s": round(last - first, 1), "wall_s_per_1000_links": round((last - first) / max(1, n_links) * 1000, 1),
                "latency_ms_p50": lat[len(lat) // 2], "latency_ms_p95": lat[min(len(lat) - 1, int(len(lat) * 0.95))],
                "input_tokens": sum(r.get("in_tok", 0) for r in recs),
                "input_tokens_per_link": round(sum(r.get("in_tok", 0) for r in recs) / max(1, len(ok)), 1),
                "output_tokens": sum(r.get("out_tok", 0) for r in recs),
                "served_models": sorted({r.get("served_model") or "" for r in recs}),
                "polls_failed": self.polls_failed,
                "complete": len(recs) >= n_links}


def hub_counters(base_url: str) -> Dict[str, Dict[str, Any]]:
    try:
        rows = requests.get(base_url.rstrip("/") + "/admin/api/hub/counters", timeout=5).json().get("counters", [])
    except (requests.RequestException, ValueError):
        return {}
    return {r["key"]: r for r in rows}


# ---------------------------------------------------------------------------
# measures


def _share(n: int, d: int) -> Optional[float]:
    return round(n / d, 3) if d else None


def _bucket(conf: Optional[float]) -> Optional[str]:
    if conf is None:
        return None
    for name, lo, hi in BUCKETS:
        if lo <= conf < hi:
            return name
    return None


def _calibration(pairs: Iterable[Tuple[Optional[float], bool]]) -> Dict[str, Any]:
    """(confidence, agreed) pairs → per-bucket n + agreement, plus auto-accept shares."""
    pairs = [(c, a) for c, a in pairs if c is not None]
    out: Dict[str, Any] = {"n": len(pairs), "buckets": {}}
    for name, lo, hi in BUCKETS:
        sel = [a for c, a in pairs if lo <= c < hi]
        out["buckets"][name] = {"n": len(sel), "agreement": _share(sum(sel), len(sel))}
    for t in THRESHOLDS:
        acc = [a for c, a in pairs if c >= t]
        out[f"auto_accept@{t}"] = {"share": _share(len(acc), len(pairs)), "agreement": _share(sum(acc), len(acc))}
    return out


def measures(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Every measure over items carrying ``legacy``, ``jev`` (``status`` ok / not evaluated) and ``owner``."""
    n = len(items)
    jev_ok = [it for it in items if it["jev"]["status"] == "ok"]
    leg_ok = [it for it in items if it["legacy"]["ok"] and it["legacy"]["fit"] is not None]
    both = [it for it in jev_ok if it["legacy"]["ok"] and it["legacy"]["fit"] is not None]
    picked = [it for it in items if it["owner"]["pick"]]

    def owner_agreement(model: str, pool: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        in_pool = {id(it) for it in pool}
        ev_picked = [it for it in picked if id(it) in in_pool]
        hi = [it for it in pool if it[model]["fit"] >= 4]
        topic_n = [it for it in ev_picked if it["owner"]["topic"]]
        return {"picked_evaluated": len(ev_picked), "picked_not_evaluated": len(picked) - len(ev_picked),
                "recall_fit_ge3": _share(sum(it[model]["fit"] >= 3 for it in ev_picked), len(ev_picked)),
                "fit_ge4": len(hi), "precision_fit_ge4": _share(sum(it["owner"]["pick"] for it in hi), len(hi)),
                "topic_agreement_on_picks": _share(sum(it[model]["topic"] == it["owner"]["topic"] for it in topic_n),
                                                   len(topic_n))}

    disagreements = [{"cid": it["cid"], "title": it["title"][:120], "legacy": it["legacy"], "jev": it["jev"],
                      "owner_pick": it["owner"]["pick"]}
                     for it in both if it["legacy"]["topic"] != it["jev"]["topic"]
                     or abs(it["legacy"]["fit"] - it["jev"]["fit"]) > 1]
    decided = [it for it in jev_ok if it["owner"]["decided"]]
    langs: Dict[str, Any] = {}
    for lg in ("en", "other", "unknown"):
        sub = [it for it in both if it["lang"] == lg]
        langs[lg] = {"n": len(sub), "topic_match": _share(sum(it["legacy"]["topic"] == it["jev"]["topic"] for it in sub),
                                                         len(sub))}
    return {
        "links": n, "owner_picks": len(picked), "owner_decided": sum(it["owner"]["decided"] for it in items),
        "owner_agreement": {"legacy": owner_agreement("legacy", leg_ok), "jev": owner_agreement("jev", jev_ok)},
        "model_agreement": {
            "n": len(both),
            "topic_match": _share(sum(it["legacy"]["topic"] == it["jev"]["topic"] for it in both), len(both)),
            "fit_within_1": _share(sum(abs(it["legacy"]["fit"] - it["jev"]["fit"]) <= 1 for it in both), len(both)),
            "news_match": _share(sum(it["legacy"]["news"] == it["jev"]["news"] for it in both), len(both)),
            "promo_match": _share(sum(it["legacy"]["promo"] == it["jev"]["promo"] for it in both), len(both)),
            "by_language": langs, "disagreements": len(disagreements)},
        "calibration": {
            "topic_vs_owner_on_picks": _calibration((it["jev"]["topic_conf"], it["jev"]["topic"] == it["owner"]["topic"])
                                                    for it in jev_ok if it["owner"]["pick"] and it["owner"]["topic"]),
            "fit_vs_owner_on_decided": _calibration((it["jev"]["fit_conf"], (it["jev"]["fit"] >= 3) == it["owner"]["pick"])
                                                    for it in decided),
            "topic_vs_legacy_all": _calibration((it["jev"]["topic_conf"], it["jev"]["topic"] == it["legacy"]["topic"])
                                                for it in both),
        },
        "reliability": {"legacy_unusable": _share(n - len(leg_ok), n), "jev_not_evaluated": _share(n - len(jev_ok), n),
                        "jev_errors": _errors(items)},
        "_disagreements": disagreements,
    }


def _errors(items: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for it in items:
        if it["jev"]["status"] != "ok":
            k = it["jev"].get("error") or jx.NOT_EVALUATED
            out[k] = out.get(k, 0) + 1
    return out


def consistency(a: Sequence[Dict[str, Any]], b: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    pairs = [(x, y) for x, y in zip(a, b) if x["status"] == "ok" and y["status"] == "ok"]
    return {"n": len(pairs), "topic_changed": _share(sum(x["topic"] != y["topic"] for x, y in pairs), len(pairs)),
            "fit_band_changed": _share(sum(x["fit"] != y["fit"] for x, y in pairs), len(pairs)),
            "news_changed": _share(sum(x["news"] != y["news"] for x, y in pairs), len(pairs)),
            "promo_changed": _share(sum(x["promo"] != y["promo"] for x, y in pairs), len(pairs))}


def as_answer(legacy: Dict[str, Any]) -> Dict[str, Any]:
    """A legacy answer in the Jev row shape ``consistency`` reads (``status`` ok / not evaluated)."""
    ok = bool(legacy.get("ok")) and legacy.get("fit") is not None
    return {"status": "ok" if ok else jx.NOT_EVALUATED, "topic": legacy.get("topic"), "fit": legacy.get("fit"),
            "news": bool(legacy.get("news")), "promo": bool(legacy.get("promo"))}


def legacy_answer(m: MetaScore) -> Dict[str, Any]:
    return {"ok": bool(m.ok), "topic": m.topic, "fit": m.fit, "news": bool(m.news), "promo": bool(m.promo)}


def perf(calls: Sequence[Dict[str, Any]], *, wall_s: float, n_links: int) -> Dict[str, Any]:
    """Client-side timing + the responses' token usage for one engine pass (``calls`` from the scorer's sink).

    ``max_in_flight`` is the most calls open at one instant — the concurrency the pass actually reached.
    """
    if not calls:
        return {"calls": 0, "links": n_links, "wall_s": round(wall_s, 1)}
    lat = sorted(c["end"] - c["start"] for c in calls)
    edges = sorted([(c["start"], 1) for c in calls] + [(c["end"], -1) for c in calls], key=lambda e: (e[0], e[1]))
    open_, peak = 0, 0
    for _t, step in edges:
        open_ += step
        peak = max(peak, open_)

    def tok(key: str) -> int:
        return sum(int((c.get("usage") or {}).get(key, 0)) for c in calls)

    fresh, cache_r, cache_w = tok("input_tokens"), tok("cache_read_input_tokens"), tok("cache_creation_input_tokens")
    return {"source": "client timer + response usage", "calls": len(calls), "failed_calls": sum(not c["ok"] for c in calls),
            "links": n_links, "wall_s": round(wall_s, 1), "ms_per_link": round(wall_s / max(1, n_links) * 1000, 1),
            "call_s_p50": round(lat[len(lat) // 2], 2), "call_s_p95": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 2),
            "max_in_flight": peak, "input_tokens": fresh + cache_r + cache_w, "input_tokens_uncached": fresh,
            "cache_read_tokens": cache_r, "cache_write_tokens": cache_w, "output_tokens": tok("output_tokens"),
            "input_tokens_per_link": round((fresh + cache_r + cache_w) / max(1, n_links), 1),
            "output_tokens_per_link": round(tok("output_tokens") / max(1, n_links), 1),
            "calls_without_usage": sum(not c.get("usage") for c in calls)}


def stage_b_entry(items: Sequence[Dict[str, Any]], model: str, priors: sc.Priors, depth: int) -> Dict[str, Any]:
    """Which links would reach Stage B (the fetch) on this engine's Stage A answers, ranked the way ``run.py``
    ranks them; compared with the links the original run actually fetched and with the owner's picks.

    The domain bonus is recomputed from the stored domain, which ``run.py`` rewrites after a fetch — a small
    drift on exactly the fetched links; ``legacy_stored`` (the run's own answers) is the baseline for it.
    """
    scored = []
    for it in items:
        a = it[model]
        ok = a.get("status") == "ok" if "status" in a else bool(a.get("ok"))
        if not ok or a.get("fit") is None:
            continue
        meta = MetaScore(topic=a.get("topic"), fit=int(a["fit"]), news=bool(a.get("news")), ok=True)
        s = sc.combine(sender_weight=it["sender_weight"], domain_bonus=priors.domain(it["domain"])[0], meta=meta,
                       content=None)
        if s is not None:
            scored.append((s, it))
    top = [it for _s, it in sorted(scored, key=lambda x: -x[0])[:depth]]
    original = [it for it in items if it.get("fetched")]
    picks = [it for it in items if it["owner"]["pick"]]
    top_ids = {it["cid"] for it in top}
    return {"depth": depth, "original_fetched": len(original),
            "overlap_with_original": _share(sum(it["cid"] in top_ids for it in original), len(original)),
            "owner_picks": len(picks), "owner_picks_reached": sum(it["cid"] in top_ids for it in picks),
            "owner_picks_reached_share": _share(sum(it["cid"] in top_ids for it in picks), len(picks))}


# ---------------------------------------------------------------------------
# request capture — one representative request and its raw response per engine


def capture_requests(items: Sequence[Dict[str, Any]], criteria: Dict[str, Any], *, base_url: str, jev_model: str,
                     legacy_model: str, batch: int) -> Dict[str, Any]:
    """Send one legacy batch (the first ``batch`` items) and one Jev request (the first item) exactly as the
    engines build them, and keep request + raw response. Local artefact only: it carries link metadata."""
    out: Dict[str, Any] = {}
    chunk = [it["input"] for it in items[:batch]]
    prompt, max_tokens = sc.meta_request(sc._criteria_brief(criteria), chunk)
    t0 = time.monotonic()
    try:
        text, usage = llm.call_with_usage(base_url=base_url, model=legacy_model, prompt=prompt, max_tokens=max_tokens,
                                          timeout=180)
        resp: Dict[str, Any] = {"text": text, "usage": usage}
    except Exception as exc:  # noqa: BLE001 — a capture failure is recorded, never fatal
        resp = {"error": type(exc).__name__}
    out["legacy"] = {"endpoint": "POST /v1/messages (anthropic SDK)", "links": len(chunk),
                     "request": {"model": legacy_model, "max_tokens": max_tokens,
                                 "messages": [{"role": "user", "content": prompt}]},
                     "response": resp, "latency_s": round(time.monotonic() - t0, 2)}
    body = {"model": jev_model, "state": jx.build_state(items[0]["input"]), "questions": jx.build_questions(criteria)}
    t0 = time.monotonic()
    try:
        r = requests.post(base_url.rstrip("/") + jx.ROUTE, json=body, timeout=jx.DEFAULT_TIMEOUT_S)
        jresp: Dict[str, Any] = {"status": r.status_code, "body": r.json()}
    except (requests.RequestException, ValueError) as exc:
        jresp = {"error": type(exc).__name__}
    out["jev"] = {"endpoint": f"POST {jx.ROUTE}", "links": 1, "request": body, "response": jresp,
                  "latency_s": round(time.monotonic() - t0, 2)}
    return out


# ---------------------------------------------------------------------------
# modes


def replay(run_ids: Sequence[int], *, base_url: str, model: str, workers: int, repeat: bool,
           criteria: Dict[str, Any], legacy: Optional[Dict[str, Any]] = None, require_review: bool = True,
           ) -> Dict[str, Any]:
    """Jev over each stored run. ``legacy`` (``{"model", "workers", "top_k"}``) also re-scores the same items
    with legacy Stage A — fresh, LLM cache bypassed — the two engines interleaved (the first engine alternates
    per run, so both see the same box load), each with client timing and the responses' token usage (#392).
    ``legacy["top_k"]`` sets the Stage B depth the way ``run.py`` does: ``max(top_k, links // 12)``."""
    per_run: Dict[str, Any] = {}
    pooled: List[Dict[str, Any]] = []
    fresh_pooled: List[Dict[str, Any]] = []
    priors = sc.Priors(criteria) if legacy else None
    for n_run, rid in enumerate(run_ids):
        run, items = stage_a_items(rid, require_review=require_review)
        logger.info("🧪 run %s (%s → %s, %s): %d Stage A links, %d owner picks", rid, run["window_start"],
                    run["window_end"], run.get("model"), len(items), sum(it["owner"]["pick"] for it in items))
        inputs = [it["input"] for it in items]
        engines: Dict[str, Any] = {}
        order = ("jev",) if not legacy else (("legacy", "jev") if n_run % 2 == 0 else ("jev", "legacy"))
        for engine in order:
            calls: List[Dict[str, Any]] = []
            t0 = time.monotonic()
            if engine == "jev":
                with RingRecorder(base_url) as ring:
                    scores = jx.score_metadata_jev(inputs, criteria, base_url=base_url, model=model, workers=workers,
                                                   calls=calls)
                wall = time.monotonic() - t0
                for it, m in zip(items, scores):
                    it["jev"] = jev_answer(m)
            else:
                metas = sc.score_metadata(inputs, criteria, base_url=base_url, model=legacy["model"],
                                          workers=legacy["workers"], cache=None, calls=calls)
                wall = time.monotonic() - t0
                for it, m in zip(items, metas):
                    it["legacy_fresh"] = legacy_answer(m)
            engines[engine] = perf(calls, wall_s=wall, n_links=len(items))
            logger.info("  ⏱️ %s: %.0fs, %d calls, max %d in flight", engine, wall, len(calls),
                        engines[engine].get("max_in_flight", 0))
        entry = {"window": [run["window_start"], run["window_end"]], "legacy_model": run.get("model"),
                 "decided_by": run["decided_by"], "latency": ring.summary(len(items)), "measures": measures(items)}
        if legacy:
            fresh = [dict(it, legacy=it["legacy_fresh"]) for it in items]
            depth = max(int(legacy["top_k"]), len(items) // 12)
            entry["engines"] = {"order": list(order), **engines}
            entry["fresh_legacy_measures"] = measures(fresh)
            entry["reproduction"] = {
                "legacy_fresh_vs_stored": consistency([as_answer(it["legacy"]) for it in items],
                                                      [as_answer(it["legacy_fresh"]) for it in items]),
                "stage_b_entry": {"legacy_stored": stage_b_entry(items, "legacy", priors, depth),
                                  "legacy_fresh": stage_b_entry(items, "legacy_fresh", priors, depth),
                                  "jev": stage_b_entry(items, "jev", priors, depth)}}
            if run["decided_by"] == "jev":
                entry["reproduction"]["jev_fresh_vs_stored"] = consistency(
                    [it["jev_stored"] for it in items], [it["jev"] for it in items])
            elif run["shadow"] is not None:
                prior = {r["cid"]: r for r in run["shadow"]["rows"]}
                entry["reproduction"]["jev_fresh_vs_shadow"] = consistency(
                    [prior.get(it["cid"]) or {"status": jx.NOT_EVALUATED} for it in items], [it["jev"] for it in items])
            fresh_pooled.extend(fresh)
        if repeat:
            again = jx.score_metadata_jev([it["input"] for it in items], criteria, base_url=base_url, model=model,
                                          workers=workers)
            entry["consistency"] = consistency([it["jev"] for it in items], [jev_answer(m) for m in again])
        per_run[str(rid)] = entry
        per_run[str(rid)]["_items"] = items
        pooled.extend(items)
    result = {"mode": "replay", "runs": per_run, "pooled": measures(pooled)}
    if legacy:
        result["mode"] = "replay + legacy"
        result["pooled_fresh_legacy"] = measures(fresh_pooled)
    return result


def shadow(run_ids: Sequence[int]) -> Dict[str, Any]:
    """Stored runs with a shadow pass, either direction: ``both`` (legacy decided, Jev in ``.jev-shadow.json``)
    and ``jev+legacy`` (Jev decided, legacy in ``.legacy-shadow.json``, #394). No model calls."""
    if not run_ids:
        run_ids = [r["id"] for r in db.list_runs(60) if r.get("reviewed")
                   and ((r.get("stats") or {}).get("jev_shadow") or (r.get("stats") or {}).get("legacy_shadow"))]
    per_run: Dict[str, Any] = {}
    pooled: List[Dict[str, Any]] = []
    for rid in run_ids:
        run, items = stage_a_items(rid)
        if run["shadow"] is None:
            logger.warning("⚠️ run %s (%s decided) has no shadow file — skipped", rid, run["decided_by"])
            continue
        if run["decided_by"] == "jev":
            for it in items:
                it["jev"] = it["jev_stored"]
        else:
            rows = {r["cid"]: r for r in run["shadow"]["rows"]}
            for it in items:
                r = rows.get(it["cid"])
                it["jev"] = {k: v for k, v in r.items() if k not in ("cid", "probs")} if r else \
                    {"status": jx.NOT_EVALUATED, "error": "not in shadow file"}
        per_run[str(rid)] = {"window": [run["window_start"], run["window_end"]], "legacy_model": run.get("model"),
                             "decided_by": run["decided_by"], "measures": measures(items), "_items": items}
        pooled.extend(items)
    return {"mode": "shadow", "runs": per_run, "pooled": measures(pooled) if pooled else None}


# ---------------------------------------------------------------------------
# artefact


def _pct(v: Optional[float]) -> str:
    return "–" if v is None else f"{v:.0%}"


def render_md(result: Dict[str, Any]) -> str:
    p = result.get("pooled")
    lines = [f"# Jev pilot — {result['mode']} ({result['generated_at']})", ""]
    if not p:
        return "\n".join(lines + ["No runs with Jev answers."])
    lines += [f"Runs: {', '.join(result['runs'])} · links {p['links']} · owner picks {p['owner_picks']}", "",
              "| | legacy | jev |", "|---|---|---|"]
    oa = p["owner_agreement"]
    for key, label in (("recall_fit_ge3", "picks scored fit ≥ 3"), ("precision_fit_ge4", "fit ≥ 4 that were picked"),
                       ("topic_agreement_on_picks", "topic = owner's on picks")):
        lines.append(f"| {label} | {_pct(oa['legacy'][key])} | {_pct(oa['jev'][key])} |")
    rel = p["reliability"]
    lines.append(f"| no usable answer | {_pct(rel['legacy_unusable'])} | {_pct(rel['jev_not_evaluated'])} |")
    ma = p["model_agreement"]
    lines += ["", f"Model agreement (n={ma['n']}): topic {_pct(ma['topic_match'])} · fit ±1 {_pct(ma['fit_within_1'])}"
              f" · news {_pct(ma['news_match'])} · promo {_pct(ma['promo_match'])}", ""]
    for name, cal in p["calibration"].items():
        lines.append(f"Calibration — {name} (n={cal['n']}): " + " · ".join(
            f"{b} {_pct(v['agreement'])} (n={v['n']})" for b, v in cal["buckets"].items()))
    fl = result.get("pooled_fresh_legacy")
    if fl:
        lines += ["", "Fresh legacy (re-scored now, cache bypassed) vs the owner: " + " · ".join(
            f"{label} {_pct(fl['owner_agreement']['legacy'][key])}" for key, label in (
                ("recall_fit_ge3", "picks fit ≥ 3"), ("precision_fit_ge4", "fit ≥ 4 picked"),
                ("topic_agreement_on_picks", "topic on picks"))),
                  "", "| run | engine | calls | wall s | ms/link | max in flight | in tok/link | out tok/link |",
                  "|---|---|---|---|---|---|---|---|"]
        for rid, r in result["runs"].items():
            for eng in r.get("engines", {}).get("order", []):
                e = r["engines"][eng]
                lines.append(f"| {rid} | {eng} | {e['calls']} | {e['wall_s']} | {e.get('ms_per_link', '–')} | "
                             f"{e.get('max_in_flight', '–')} | {e.get('input_tokens_per_link', '–')} | "
                             f"{e.get('output_tokens_per_link', '–')} |")
        for rid, r in result["runs"].items():
            lines.append(f"Run {rid} reproduction: {r.get('reproduction')}")
    for rid, r in result["runs"].items():
        if "latency" in r:
            lt = r["latency"]
            lines.append(f"Run {rid} latency: {lt}")
        if "consistency" in r:
            lines.append(f"Run {rid} consistency: {r['consistency']}")
    return "\n".join(lines) + "\n"


def write_artefact(result: Dict[str, Any], out_root: Path = OUT_ROOT) -> Path:
    out = out_root / result["generated_at"].replace(":", "")
    out.mkdir(parents=True, exist_ok=True)
    for rid, r in result["runs"].items():
        items = r.pop("_items")
        (out / f"items-run-{rid}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in result["runs"].values():
        r["measures"].pop("_disagreements")
        (r.get("fresh_legacy_measures") or {}).pop("_disagreements", None)
    (result.get("pooled_fresh_legacy") or {}).pop("_disagreements", None)
    if "capture" in result:
        (out / "capture.json").write_text(json.dumps(result.pop("capture"), ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    pooled = result.get("pooled") or {}
    (out / "disagreements.json").write_text(json.dumps(pooled.pop("_disagreements", []), ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / "summary.md").write_text(render_md(result), encoding="utf-8")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=int, action="append", default=[], help="stored, reviewed run id (repeatable)")
    ap.add_argument("--shadow", action="store_true", help="compare stored runs with a shadow pass, `both` or `jev+legacy` (no model calls)")
    ap.add_argument("--repeat", action="store_true", help="replay: score each run twice for consistency")
    ap.add_argument("--model", default=None)
    ap.add_argument("--legacy", action="store_true",
                    help="replay: also re-score each run with legacy Stage A (fresh, cache bypassed), interleaved")
    ap.add_argument("--allow-unreviewed", action="store_true", help="replay: admit runs without an applied review")
    ap.add_argument("--capture", action="store_true",
                    help="replay: keep one representative request + raw response per engine (capture.json)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    for noisy in ("httpx", "httpcore", "hpack", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = load_block("newsletter_triage")
    base_url = cfg.get("llm_hub_base_url", "http://127.0.0.1:8000")
    model = args.model or cfg.get("jev_model", jx.DEFAULT_MODEL)
    try:
        db.ensure_schema()
    except db.SchemaMissing as err:
        print(f"❌ {err}")
        return 2
    if args.shadow:
        result = shadow(args.run)
    else:
        if not args.run:
            ap.error("--run is required for a replay")
        criteria = json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))
        legacy_model = cfg.get("llm_model", "claude_haiku")
        legacy = {"model": legacy_model, "workers": int(cfg.get("llm_workers", 3)) + 1,      # as run.py sets it
                  "top_k": int(cfg.get("stage_b_top_k", 90))} if args.legacy else None
        before = hub_counters(base_url)
        result = replay(args.run, base_url=base_url, model=model, repeat=args.repeat, criteria=criteria,
                        workers=int(cfg.get("jev_workers", jx.DEFAULT_WORKERS)), legacy=legacy,
                        require_review=not args.allow_unreviewed)
        if args.capture:
            _run, first = stage_a_items(args.run[0], require_review=not args.allow_unreviewed)
            result["capture"] = capture_requests(first, criteria, base_url=base_url, jev_model=model,
                                                 legacy_model=legacy_model, batch=25)
        after = hub_counters(base_url)
        result["hub_counters"] = {k: {"before": before.get(k), "after": v} for k, v in after.items()
                                  if k.startswith("jev") or k == cfg.get("llm_model", "claude_haiku")}
    result["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = write_artefact(result)
    print(render_md(result))
    print(f"📁 artefact: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
