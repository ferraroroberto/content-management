"""Triage store — Supabase / Postgres ``triage_*`` tables, one module for every query.

    python -m newsletter.triage.db ensure-schema      # probe the tables, print the apply recipe if missing
    python -m newsletter.triage.db import-history     # editions + picks from results/newsletter/triage/history/
    python -m newsletter.triage.db stats              # row counts per table

Schema: ``newsletter/triage/schema.sql`` — applied once by the owner in the Supabase SQL
editor (PostgREST cannot run DDL). The client is supabase-py from ``config.supabase``
(service_role_key → key → anon_key, same priority as the engagement pipeline). The UI
(``app/tab_triage.py``) never touches the client: it calls ``newsletter.triage.review``.

Tests inject a fake client with ``set_client()``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.console import force_utf8_stdio  # noqa: E402
from config.loader import load_full_config  # noqa: E402

logger = logging.getLogger("newsletter_triage.db")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
HISTORY_DIR = REPO_ROOT / "results" / "newsletter" / "triage" / "history"
TABLES = ("triage_runs", "triage_emails", "triage_candidates", "triage_decisions", "triage_reviews",
          "triage_lessons", "triage_editions", "triage_picks")
PAGE = 1000          # PostgREST's default max rows per request — every read pages through this
CHUNK = 500          # rows per upsert request

_client: Any = None


class SchemaMissing(RuntimeError):
    """The ``triage_*`` tables are not in the project DB yet."""


# ---------------------------------------------------------------------------
# client


def _table_missing(err: Exception) -> bool:
    msg = str(err)
    return "PGRST205" in msg or "42P01" in msg or "does not exist" in msg or "Could not find the table" in msg


def _bad_key(err: Exception) -> bool:
    msg = str(err).lower()
    return "invalid api key" in msg or "jwt" in msg or "401" in msg or "unauthorized" in msg


def client() -> Any:
    """Cached supabase-py client from ``config.supabase``. Keys are tried in priority order
    (service_role_key → key → anon_key) and each is probed against ``triage_runs``: a key rejected by the
    API moves on to the next; a key that answers "table not found" is a working key (the schema is simply
    not applied yet — ``ensure_schema`` reports that). Same fallback shape as the engagement pipeline."""
    global _client
    if _client is not None:
        return _client
    from supabase import create_client  # local import — keep the dependency lazy for tests

    cfg = load_full_config().get("supabase") or {}
    url = cfg.get("url")
    if not url:
        raise RuntimeError("Missing supabase.url in config.json")
    last_err: Optional[Exception] = None
    for label in ("service_role_key", "key", "anon_key"):
        key = cfg.get(label)
        if not key:
            continue
        cand = create_client(url, key)
        try:
            cand.table("triage_runs").select("id").limit(1).execute()
        except Exception as err:  # noqa: BLE001 — classify: bad key → next; missing table → key is fine
            last_err = err
            if _bad_key(err) and not _table_missing(err):
                logger.debug("🔑 supabase key %s rejected, trying next: %s", label, str(err)[:120])
                continue
        logger.debug("🔑 supabase key: %s", label)
        _client = cand
        return _client
    raise RuntimeError(f"No working supabase key in config.supabase (service_role_key / key / anon_key): {last_err}")


def set_client(c: Any) -> None:
    """Inject a client (tests) or reset with ``None``."""
    global _client
    _client = c


def _t(name: str) -> Any:
    return client().table(name)


def ensure_schema() -> None:
    """Probe ``triage_runs``; raise :class:`SchemaMissing` with the apply recipe when absent."""
    try:
        _t("triage_runs").select("id").limit(1).execute()
    except Exception as err:  # noqa: BLE001 — classify, never swallow
        if _table_missing(err):
            raise SchemaMissing(
                f"triage_* tables not found in the Supabase project — apply {SCHEMA_PATH} once in the SQL editor "
                f"(idempotent), then retry. ({str(err)[:120]})") from err
        raise


def _fetch_all(name: str, select: str = "*", *, filters: Optional[Dict[str, Any]] = None,
               order: Optional[Tuple[str, bool]] = None, in_: Optional[Tuple[str, Sequence[Any]]] = None) -> List[Dict[str, Any]]:
    """Page through every matching row (PostgREST caps a request at ``PAGE`` rows)."""
    out: List[Dict[str, Any]] = []
    start = 0
    while True:
        q = _t(name).select(select)
        for col, val in (filters or {}).items():
            q = q.eq(col, val)
        if in_:
            q = q.in_(in_[0], list(in_[1]))
        if order:
            q = q.order(order[0], desc=order[1])
        rows = q.range(start, start + PAGE - 1).execute().data or []
        out.extend(rows)
        if len(rows) < PAGE:
            return out
        start += PAGE


def _upsert(name: str, rows: Sequence[Dict[str, Any]], *, on_conflict: str) -> int:
    rows = [r for r in rows if r]
    for i in range(0, len(rows), CHUNK):
        _t(name).upsert(list(rows[i:i + CHUNK]), on_conflict=on_conflict).execute()
    return len(rows)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _d(v: Any) -> Optional[str]:
    return v.isoformat() if isinstance(v, date) else (str(v)[:10] if v else None)


# ---------------------------------------------------------------------------
# runs


def run_for_window(start: Any, end: Any, kind: str = "live") -> Optional[Dict[str, Any]]:
    rows = _t("triage_runs").select("*").eq("window_start", _d(start)).eq("window_end", _d(end)) \
        .eq("kind", kind).limit(1).execute().data or []
    return rows[0] if rows else None


def register_run(start: Any, end: Any, *, kind: str = "live", edition: Optional[str] = None, source: str,
                 model: str, criteria_version: str) -> int:
    """Replace any stored run for (window, kind) — children cascade — and insert a fresh ``running`` row."""
    prev = run_for_window(start, end, kind)
    if prev:
        _t("triage_runs").delete().eq("id", prev["id"]).execute()
        logger.info("♻️ replaced stored run %s (%s → %s, %s)", prev["id"], _d(start), _d(end), kind)
    row = {"window_start": _d(start), "window_end": _d(end), "kind": kind, "edition": edition, "status": "running",
           "started_at": _now(), "source": source, "model": model, "criteria_version": criteria_version}
    data = _t("triage_runs").insert(row).execute().data or []
    return int(data[0]["id"])


def mark_run(run_id: int, *, status: str, stats: Optional[Dict[str, Any]] = None,
             report_path: Optional[str] = None) -> None:
    patch: Dict[str, Any] = {"status": status, "finished_at": _now()}
    if stats is not None:
        patch["stats"] = json.loads(json.dumps(stats, default=str))
    if report_path:
        patch["report_path"] = report_path
    _t("triage_runs").update(patch).eq("id", run_id).execute()


def get_run(run_id: int) -> Optional[Dict[str, Any]]:
    rows = _t("triage_runs").select("*").eq("id", run_id).limit(1).execute().data or []
    return rows[0] if rows else None


def list_runs(limit: int = 60) -> List[Dict[str, Any]]:
    """Newest first, with ``reviewed`` (a review row exists for the window) and ``picks`` (stats.selected)."""
    runs = _t("triage_runs").select("*").order("window_end", desc=True).order("kind", desc=False) \
        .order("started_at", desc=True).limit(limit).execute().data or []
    reviews = {(r["window_start"], r["window_end"]) for r in _fetch_all("triage_reviews", "window_start,window_end")}
    for r in runs:
        r["reviewed"] = (r["window_start"], r["window_end"]) in reviews
        r["picks"] = (r.get("stats") or {}).get("selected")
    return runs


def candidate_rows(cands: Sequence[Any], sel: Any) -> List[Dict[str, Any]]:
    """Flatten ``rank.Candidate`` objects + the ``Selection`` into ``triage_candidates`` rows."""
    suggested: Dict[str, Tuple[str, int]] = {}
    for t, picks in sel.picks.items():
        for i, c in enumerate(picks):
            suggested[c.cid] = ("pick", i + 1)
    for t, runners in sel.runners.items():
        for i, c in enumerate(runners):
            suggested.setdefault(c.cid, ("runner", i + 1))
    stars = {c.cid for c in sel.stars.values() if c is not None}
    must = sel.must_read.cid if sel.must_read is not None else None
    rows: List[Dict[str, Any]] = []
    for c in cands:
        s = suggested.get(c.cid)
        rows.append({
            "cid": c.cid, "message_id": c.message_id, "url": c.url, "canonical": c.canonical, "domain": c.domain,
            "title": c.display_title, "author": c.author, "kind": c.kind, "topic": c.topic, "score": c.score,
            "verdict": c.verdict, "reason": c.reason or None, "summary": c.summary or None,
            "sender_weight": c.sender_weight, "sender_basis": c.sender_basis, "is_new_sender": bool(c.is_new_sender),
            "paywalled": c.paywalled, "in_notion": bool(c.in_notion), "fetched_ok": c.fetched_ok,
            "meta": asdict(c.meta) if c.meta is not None else None,
            "content": asdict(c.content) if c.content is not None else None,
            "suggested": s[0] if s else None, "suggested_rank": s[1] if s else None,
            "suggested_star": c.cid in stars, "suggested_must_read": c.cid == must,
        })
    return rows


def store_run_results(run_id: int, emails: Sequence[Dict[str, Any]], candidates: Sequence[Dict[str, Any]]) -> Tuple[int, int]:
    """Write the emails + candidate rows of a run (replaces any previous rows of that run)."""
    _t("triage_emails").delete().eq("run_id", run_id).execute()
    _t("triage_candidates").delete().eq("run_id", run_id).execute()
    e_rows = [{"run_id": run_id, "message_id": e["message_id"], "sender_name": e.get("sender_name"),
               "sender_address": e.get("sender_address"), "subject": e.get("subject"), "ts": e.get("timestamp"),
               "sender_basis": e.get("sender_basis"), "is_new": bool(e.get("is_new"))} for e in emails]
    c_rows = [dict(c, run_id=run_id) for c in candidates]
    n_e = _upsert("triage_emails", e_rows, on_conflict="run_id,message_id")
    n_c = _upsert("triage_candidates", c_rows, on_conflict="run_id,cid")
    return n_e, n_c


def candidates(run_id: int) -> List[Dict[str, Any]]:
    return _fetch_all("triage_candidates", filters={"run_id": run_id})


def emails(run_id: int) -> List[Dict[str, Any]]:
    return _fetch_all("triage_emails", filters={"run_id": run_id}, order=("ts", False))


# ---------------------------------------------------------------------------
# decisions / reviews


def load_decisions(start: Any, end: Any) -> List[Dict[str, Any]]:
    return _fetch_all("triage_decisions", filters={"window_start": _d(start), "window_end": _d(end)})


def save_decisions(start: Any, end: Any, rows: Sequence[Dict[str, Any]]) -> int:
    """Upsert decision rows for a window. Each row: canonical (required), cid, sender_address, title, url,
    topic, pick, star, must_read, note."""
    now = _now()
    out = []
    for r in rows:
        if not r.get("canonical"):
            continue
        out.append({"window_start": _d(start), "window_end": _d(end), "canonical": r["canonical"],
                    "cid": r.get("cid"), "sender_address": (r.get("sender_address") or "").lower() or None,
                    "title": r.get("title"), "url": r.get("url"), "topic": r.get("topic"),
                    "pick": bool(r.get("pick")), "star": bool(r.get("star")), "must_read": bool(r.get("must_read")),
                    "note": r.get("note") or None, "decided_at": now})
    return _upsert("triage_decisions", out, on_conflict="window_start,window_end,canonical")


def decision_tally() -> Dict[str, Tuple[int, int]]:
    """``{sender_address: (n_decisions, n_yes)}`` over every stored decision — feeds ``feedback.tier_for``.
    One vote per article: the same canonical URL decided in two windows (overlapping backtest windows,
    a link offered twice) counts once, latest decision wins."""
    latest: Dict[str, Dict[str, Any]] = {}
    for r in _fetch_all("triage_decisions", "canonical,sender_address,pick,decided_at", order=("decided_at", False)):
        if r.get("canonical"):
            latest[r["canonical"]] = r
    counts: Dict[str, List[int]] = {}
    for r in latest.values():
        addr = (r.get("sender_address") or "").lower()
        if not addr:
            continue
        c = counts.setdefault(addr, [0, 0])
        c[0] += 1
        c[1] += int(bool(r.get("pick")))
    return {k: (v[0], v[1]) for k, v in counts.items()}


def save_review(start: Any, end: Any, *, comment: str, n_pick: int, n_star: int,
                tier_changes: Optional[List[str]] = None) -> None:
    _upsert("triage_reviews", [{"window_start": _d(start), "window_end": _d(end), "comment": comment or None,
                                "reviewed_at": _now(), "n_pick": n_pick, "n_star": n_star,
                                "tier_changes": tier_changes or []}], on_conflict="window_start,window_end")


def get_review(start: Any, end: Any) -> Optional[Dict[str, Any]]:
    rows = _t("triage_reviews").select("*").eq("window_start", _d(start)).eq("window_end", _d(end)) \
        .limit(1).execute().data or []
    return rows[0] if rows else None


# ---------------------------------------------------------------------------
# lessons


def add_lessons(texts: Sequence[str], *, run_id: Optional[int], start: Any, end: Any, model: str) -> List[Dict[str, Any]]:
    rows = [{"run_id": run_id, "window_start": _d(start), "window_end": _d(end), "text": t.strip(), "model": model,
             "proposed_at": _now(), "accepted": False} for t in texts if t and t.strip()]
    if not rows:
        return []
    return _t("triage_lessons").insert(rows).execute().data or []


def lessons(*, accepted: Optional[bool] = None, start: Any = None, end: Any = None) -> List[Dict[str, Any]]:
    filters: Dict[str, Any] = {}
    if accepted is not None:
        filters["accepted"] = accepted
    if start and end:
        filters["window_start"], filters["window_end"] = _d(start), _d(end)
    return _fetch_all("triage_lessons", filters=filters, order=("id", False))


def accept_lessons(ids: Sequence[int], *, accepted: bool = True) -> int:
    if not ids:
        return 0
    patch = {"accepted": accepted, "accepted_at": _now() if accepted else None}
    _t("triage_lessons").update(patch).in_("id", [int(i) for i in ids]).execute()
    return len(ids)


# ---------------------------------------------------------------------------
# history knowledge base


def import_history(history_dir: Path = HISTORY_DIR) -> Dict[str, int]:
    """Load ``editions.jsonl`` + ``positives.jsonl`` (written by ``newsletter.triage.history``) into
    ``triage_editions`` / ``triage_picks``. Idempotent upsert."""
    ed_path, pos_path = history_dir / "editions.jsonl", history_dir / "positives.jsonl"
    if not ed_path.exists() or not pos_path.exists():
        raise FileNotFoundError(f"history files missing under {history_dir} — run `python -m newsletter.triage.history` first")
    editions = [json.loads(l) for l in ed_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    positives = [json.loads(l) for l in pos_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    e_rows = [{"number": e["number"], "date": e.get("date"), "title": e.get("title"),
               "must_read_title": e.get("must_read_title"), "substack_url": e.get("substack"),
               "n_leader": e.get("n_leader"), "n_innov": e.get("n_innov"), "n_persdev": e.get("n_persdev")}
              for e in editions if e.get("number")]
    known = {e["number"] for e in e_rows}
    p_rows = [{"article_id": p["article_id"], "edition": p.get("edition") if p.get("edition") in known else None,
               "title": p.get("title"), "url": p.get("url"), "canonical": p.get("canonical"), "domain": p.get("domain"),
               "topic": p.get("topic"), "author": p.get("author"), "star": bool(p.get("star")),
               "must_read": bool(p.get("must_read")), "created": p.get("created"), "summary": (p.get("summary") or "")[:2000] or None}
              for p in positives if p.get("article_id")]
    n_e = _upsert("triage_editions", e_rows, on_conflict="number")
    n_p = _upsert("triage_picks", p_rows, on_conflict="article_id")
    logger.info("📚 history imported: %d editions, %d picks", n_e, n_p)
    return {"editions": n_e, "picks": n_p}


def next_edition_number() -> Optional[str]:
    """``N<max+1>`` from ``triage_editions`` (the next free edition), or None when the table is empty."""
    rows = _t("triage_editions").select("number").order("number", desc=True).limit(1).execute().data or []
    if not rows:
        return None
    num = rows[0]["number"]
    try:
        return f"N{int(num.lstrip('N')) + 1}"
    except ValueError:
        return None


def table_counts() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for name in TABLES:
        res = _t(name).select("*", count="exact").limit(1).execute()
        out[name] = int(getattr(res, "count", None) or 0)
    return out


# ---------------------------------------------------------------------------
# CLI


def main(argv: Optional[List[str]] = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("ensure-schema", "import-history", "stats"))
    ap.add_argument("--history-dir", default=str(HISTORY_DIR))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    for noisy in ("httpx", "hpack", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        ensure_schema()
    except SchemaMissing as err:
        print(f"❌ {err}")
        return 2
    if args.command == "ensure-schema":
        print("✅ triage_* tables present")
        return 0
    if args.command == "import-history":
        res = import_history(Path(args.history_dir))
        print(f"✅ imported {res['editions']} editions, {res['picks']} picks")
        return 0
    for name, n in table_counts().items():
        print(f"{name:20} {n:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
