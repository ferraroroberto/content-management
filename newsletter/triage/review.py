"""Review logic behind the control-panel tab — no Streamlit here (UI code lives in ``app/tab_triage.py``).

* ``saturday_weeks()`` — the closed Saturday→Saturday windows the tab offers.
* ``review_frame(run_id)`` — the editable table: engine suggestions (picks + runners-up per topic, or every
  scored candidate) pre-filled with the owner's stored decisions for that window.
* ``apply_review(run_id, rows, comment)`` — the only write path: decisions → sender tiers → review row →
  local watermark (``state.reviewed_until``). Nothing is written before the owner clicks Apply.
* ``new_senders(run_id)`` / ``set_sender_tier()`` — manual tier for senders with no history.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from newsletter.triage import db  # noqa: E402
from newsletter.triage import feedback as fb  # noqa: E402
from newsletter.triage.criteria import OVERRIDES_PATH, load_overrides  # noqa: E402
from newsletter.triage.rank import TOPICS, WEAK_FILL  # noqa: E402
from newsletter.triage.state import load_state, save_state  # noqa: E402

logger = logging.getLogger("newsletter_triage.review")

TIERS = ("always", "usually", "review", "rarely", "never")
EDITOR_COLUMNS = ["cid", "topic", "pick", "star", "must_read", "score", "title", "url", "sender", "summary", "why",
                  "note", "suggested", "canonical", "sender_address"]


# ---------------------------------------------------------------------------
# windows


def last_saturday(today: Optional[date] = None) -> date:
    today = today or date.today()
    return today - timedelta(days=(today.weekday() - 5) % 7)


def saturday_weeks(n: int = 8, today: Optional[date] = None) -> List[Tuple[date, date, str]]:
    """Closed Saturday→Saturday windows (start inclusive, end exclusive = Gmail after:/before:), newest
    first, preceded by the current open week (last Saturday → tomorrow)."""
    today = today or date.today()
    sat = last_saturday(today)
    out: List[Tuple[date, date, str]] = [(sat, today + timedelta(days=1), f"open week {sat} → today")]
    for k in range(n):
        end = sat - timedelta(days=7 * k)
        start = end - timedelta(days=7)
        out.append((start, end, f"week {start} → {end}"))
    return out


# ---------------------------------------------------------------------------
# the editable table


def _why(c: Dict[str, Any]) -> str:
    if c.get("reason"):
        return str(c["reason"])
    for key in ("content", "meta"):
        blob = c.get(key) or {}
        if isinstance(blob, dict) and blob.get("ok") and blob.get("reason"):
            return str(blob["reason"])
    return ""


def review_frame(run_id: int, *, all_candidates: bool = False) -> pd.DataFrame:
    """Rows = suggested picks + runners-up (or every scored candidate), topic order then rank then score,
    pre-filled from ``triage_decisions`` for the run's window (else the engine's suggestion; weak fills unticked)."""
    run = db.get_run(run_id)
    if not run:
        raise ValueError(f"run {run_id} not found")
    cands = db.candidates(run_id)
    decisions = {d["canonical"]: d for d in db.load_decisions(run["window_start"], run["window_end"]) if d.get("canonical")}
    emails = {e["message_id"]: e for e in db.emails(run_id)}
    topic_rank = {t: i for i, t in enumerate(TOPICS)}
    rows: List[Dict[str, Any]] = []
    for c in cands:
        if c.get("suggested") not in ("pick", "runner") and not (all_candidates and c.get("score") is not None
                                                                  and c.get("verdict") not in ("vetoed", "duplicate")):
            continue
        d = decisions.get(c.get("canonical") or "")
        weak = (c.get("score") or 0) < WEAK_FILL
        pick = bool(d["pick"]) if d else (c.get("suggested") == "pick" and not weak)
        star = bool(d.get("star")) if d else bool(c.get("suggested_star"))
        must = bool(d.get("must_read")) if d else bool(c.get("suggested_must_read"))
        em = emails.get(c.get("message_id") or "", {})
        rows.append({
            "cid": c["cid"], "topic": c.get("topic") or "–", "pick": pick, "star": star, "must_read": must,
            "score": round(float(c["score"]), 1) if c.get("score") is not None else None,
            "title": c.get("title") or c.get("url"), "url": c.get("url"),
            "sender": em.get("sender_name") or c.get("sender_basis") or "", "summary": c.get("summary") or "",
            "why": _why(c), "note": (d or {}).get("note") or "",
            "suggested": c.get("suggested") or "", "canonical": c.get("canonical"),
            "sender_address": em.get("sender_address") or "",
            "_order": (topic_rank.get(c.get("topic") or "", 9), 0 if c.get("suggested") == "pick" else 1,
                       c.get("suggested_rank") or 999, -(c.get("score") or 0)),
        })
    rows.sort(key=lambda r: r["_order"])
    for r in rows:
        r.pop("_order", None)
    return pd.DataFrame(rows, columns=EDITOR_COLUMNS)


def topic_counts(frame: pd.DataFrame) -> Dict[str, int]:
    if frame.empty:
        return {t: 0 for t in TOPICS}
    picked = frame[frame["pick"] == True]  # noqa: E712 — pandas boolean mask
    return {t: int((picked["topic"] == t).sum()) for t in TOPICS}


# ---------------------------------------------------------------------------
# apply


def apply_review(run_id: int, rows: Sequence[Dict[str, Any]], comment: str = "", *,
                 overrides_path: Path = OVERRIDES_PATH) -> Dict[str, Any]:
    """Persist the owner's table (every row, ticked or not) for the run's window, update sender tiers from the
    whole decision history, store the review comment, advance the local watermark for live runs."""
    run = db.get_run(run_id)
    if not run:
        raise ValueError(f"run {run_id} not found")
    start, end = run["window_start"], run["window_end"]
    decisions = [{"canonical": r.get("canonical"), "cid": r.get("cid"), "sender_address": r.get("sender_address"),
                  "title": r.get("title"), "url": r.get("url"), "topic": r.get("topic"),
                  "pick": bool(r.get("pick")), "star": bool(r.get("star")), "must_read": bool(r.get("must_read")),
                  "note": r.get("note") or ""} for r in rows if r.get("canonical")]
    res = fb.apply(decisions, window=(start, end), overrides_path=overrides_path)
    n_pick = sum(1 for d in decisions if d["pick"])
    n_star = sum(1 for d in decisions if d["star"])
    db.save_review(start, end, comment=comment, n_pick=n_pick, n_star=n_star, tier_changes=res["changes"])
    watermark = None
    if run.get("kind") == "live":
        state = load_state()
        cur = (state.get("reviewed_until") or "")[:10]
        if str(end) > cur:
            state["reviewed_until"] = str(end)
            save_state(state)
        watermark = state.get("reviewed_until")
    logger.info("✅ review applied for run %s (%s → %s): %d decisions, %d picks, %d tier changes",
                run_id, start, end, len(decisions), n_pick, len(res["changes"]))
    return {"decisions": len(decisions), "picks": n_pick, "stars": n_star, "tier_changes": res["changes"],
            "reviewed_until": watermark, "window": (start, end)}


# ---------------------------------------------------------------------------
# senders


def new_senders(run_id: int) -> List[Dict[str, Any]]:
    """Senders flagged NEW in the run's emails, with their current override tier (if any)."""
    ov = load_overrides().get("senders", {})
    seen: Dict[str, Dict[str, Any]] = {}
    for e in db.emails(run_id):
        if not e.get("is_new"):
            continue
        addr = (e.get("sender_address") or "").lower()
        if not addr:
            continue
        row = seen.setdefault(addr, {"sender_address": addr, "sender_name": e.get("sender_name") or "", "emails": 0,
                                     "tier": (ov.get(addr) or {}).get("tier") or "review"})
        row["emails"] += 1
    return sorted(seen.values(), key=lambda r: (-r["emails"], r["sender_address"]))


def set_sender_tier(address: str, tier: str, *, name: str = "", reason: str = "manual (control panel)",
                    overrides_path: Path = OVERRIDES_PATH) -> None:
    """Persist an owner-set tier in ``overrides.json`` (``source: manual`` — feedback never overwrites it)."""
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    address = address.lower().strip()
    ov = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else {"senders": {}}
    senders = ov.setdefault("senders", {})
    if tier == "review":
        senders.pop(address, None)
    else:
        senders[address] = {"tier": tier, "reason": reason + (f" — {name}" if name else ""),
                            "since": datetime.now(timezone.utc).date().isoformat(), "source": "manual"}
    overrides_path.write_text(json.dumps(ov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
