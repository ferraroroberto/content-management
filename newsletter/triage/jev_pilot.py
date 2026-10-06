"""Jev pilot (#285) — replay stored, reviewed runs through TypeSafe Jev and measure it against the owner.

    python -m newsletter.triage.jev_pilot --run 17 [--run 16 …] [--repeat]   # replay through the hub
    python -m newsletter.triage.jev_pilot --shadow [--run 17 …]              # stored `both` runs, no Jev calls

Read-only on the store: it reads ``triage_runs`` / ``triage_emails`` / ``triage_candidates`` /
``triage_decisions`` and never writes, never touches Notion, and is not wired into ``run.py``.
One artefact folder per invocation under ``tmp/jev_pilot/<UTC stamp>/``: ``summary.json``,
``summary.md`` and one ``items-run-<id>.json`` per run (titles included — the folder is
gitignored and local; the findings comment carries aggregates only).

Replay inputs are the stored Stage A items (candidates with a ``meta`` score): sender name from
``triage_emails``, anchor text = the stored display title, domain, URL path. For the ~90 links per
run that Stage B fetched, the stored title is the page title rather than the email anchor — a
small advantage for Jev on exactly the links that matter most; noted in the summary.

``--shadow`` reads ``<report>.jev-shadow.json`` next to each run's stored report (written by
``run.py`` when ``stage_a_engine = both``), so weekly runs are compared with the same measures.

Measures (per run and pooled):

* owner agreement — of the owner's picks, share each model scored fit ≥ 3; of links each model
  scored fit ≥ 4, share the owner picked; topic agreement on picked links;
* model agreement — topic match, fit within ±1, news / promo match;
* calibration — Jev answers bucketed by confidence (<0.5, 0.5–0.7, 0.7–0.9, ≥0.9): topic vs the
  owner's topic on picked links, fit band (≥3) vs the owner's yes/no on decided links, topic vs
  legacy on all links; plus the share a confidence threshold would auto-accept;
* reliability — share of items without a usable answer (legacy ``meta.ok``; Jev ``not evaluated``);
* latency and tokens — from the hub's observability ring (``/admin/api/hub/requests/recent``,
  polled during the replay) and its per-model counters, never client-side timers;
* consistency (``--repeat``) — the same run scored twice, share of topic / fit-band answers that change.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
import time
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
from newsletter.triage import db  # noqa: E402
from newsletter.triage import jev as jx  # noqa: E402
from newsletter.triage.criteria import CRITERIA_PATH  # noqa: E402

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


def stage_a_items(run_id: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """A stored run → (run row, Stage A items with legacy answer + owner decision)."""
    run = db.get_run(run_id)
    if not run:
        raise SystemExit(f"run {run_id} not found")
    if not db.get_review(run["window_start"], run["window_end"]):
        raise SystemExit(f"run {run_id} has no applied review — nothing to compare against")
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
        items.append({
            "cid": c["cid"], "title": title, "domain": c.get("domain") or "", "suggested": c.get("suggested"),
            "input": {"sender": senders.get(c.get("message_id"), ""), "label": title, "domain": c.get("domain") or "",
                      "path": urlsplit(c.get("url") or "").path},
            "lang": lang(title),
            "legacy": {"ok": bool(meta.get("ok")), "topic": meta.get("topic"), "fit": meta.get("fit"),
                       "news": bool(meta.get("news")), "promo": bool(meta.get("promo"))},
            "owner": {"decided": d is not None, "pick": bool(d and d.get("pick")),
                      "topic": d.get("topic") if d else None},
        })
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


# ---------------------------------------------------------------------------
# modes


def replay(run_ids: Sequence[int], *, base_url: str, model: str, workers: int, repeat: bool,
           criteria: Dict[str, Any]) -> Dict[str, Any]:
    per_run: Dict[str, Any] = {}
    pooled: List[Dict[str, Any]] = []
    for rid in run_ids:
        run, items = stage_a_items(rid)
        logger.info("🧪 run %s (%s → %s, %s): %d Stage A links, %d owner picks", rid, run["window_start"],
                    run["window_end"], run.get("model"), len(items), sum(it["owner"]["pick"] for it in items))
        with RingRecorder(base_url) as ring:
            scores = jx.score_metadata_jev([it["input"] for it in items], criteria, base_url=base_url, model=model,
                                           workers=workers)
        for it, m in zip(items, scores):
            it["jev"] = jev_answer(m)
        entry = {"window": [run["window_start"], run["window_end"]], "legacy_model": run.get("model"),
                 "latency": ring.summary(len(items)), "measures": measures(items)}
        if repeat:
            again = jx.score_metadata_jev([it["input"] for it in items], criteria, base_url=base_url, model=model,
                                          workers=workers)
            entry["consistency"] = consistency([it["jev"] for it in items], [jev_answer(m) for m in again])
        per_run[str(rid)] = entry
        per_run[str(rid)]["_items"] = items
        pooled.extend(items)
    return {"mode": "replay", "runs": per_run, "pooled": measures(pooled)}


def shadow(run_ids: Sequence[int]) -> Dict[str, Any]:
    if not run_ids:
        run_ids = [r["id"] for r in db.list_runs(60) if r.get("reviewed") and (r.get("stats") or {}).get("jev_shadow")]
    per_run: Dict[str, Any] = {}
    pooled: List[Dict[str, Any]] = []
    for rid in run_ids:
        run, items = stage_a_items(rid)
        doc = jx.read_shadow(jx.shadow_path(Path(run.get("report_path") or ""))) if run.get("report_path") else None
        if doc is None:
            logger.warning("⚠️ run %s has no jev shadow file — skipped", rid)
            continue
        rows = {r["cid"]: r for r in doc["rows"]}
        for it in items:
            r = rows.get(it["cid"])
            it["jev"] = {k: v for k, v in r.items() if k not in ("cid", "probs")} if r else \
                {"status": jx.NOT_EVALUATED, "error": "not in shadow file"}
        per_run[str(rid)] = {"window": [run["window_start"], run["window_end"]], "legacy_model": run.get("model"),
                             "measures": measures(items), "_items": items}
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
    ap.add_argument("--shadow", action="store_true", help="compare stored `both` runs (no Jev calls)")
    ap.add_argument("--repeat", action="store_true", help="replay: score each run twice for consistency")
    ap.add_argument("--model", default=None)
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
        before = hub_counters(base_url)
        result = replay(args.run, base_url=base_url, model=model, repeat=args.repeat, criteria=criteria,
                        workers=int(cfg.get("jev_workers", jx.DEFAULT_WORKERS)))
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
