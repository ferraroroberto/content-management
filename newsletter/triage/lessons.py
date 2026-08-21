"""Distil a review into criteria notes — proposed by the hub LLM, accepted by the owner.

    python -m newsletter.triage.lessons --run 12                 # propose 1–3 notes for a stored, reviewed run
    python -m newsletter.triage.lessons --accept 4,5             # accept proposals → newsletter/triage/lessons.json
    python -m newsletter.triage.lessons --list                   # pending + accepted

Input to the model: the current criteria brief, the owner's review comment ("why I chose these"),
and the deltas between the engine's suggestion and the decisions (suggested-but-dropped,
promoted from the runners-up / long tail, rows with notes). Output: ≤ 3 short, generalisable
rule lines — never restatements of existing rules, never sender addresses. Nothing enters the
prompt brief (``score._criteria_brief``) until ``accept`` exports it to the tracked
``lessons.json`` (text only — the engine must not depend on the DB). Model: hub alias from
``config.newsletter_triage.lessons_model`` (owner choice: ``claude_sonnet``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.console import force_utf8_stdio  # noqa: E402
from config.loader import load_block, load_full_config  # noqa: E402
from newsletter import llm  # noqa: E402
from newsletter.triage import db  # noqa: E402
from newsletter.triage.score import LESSONS_PATH, _criteria_brief, _parse_json  # noqa: E402

logger = logging.getLogger("newsletter_triage.lessons")

DEFAULT_MODEL = "claude_sonnet"
MAX_LESSONS = 3


def _short(s: Optional[str], n: int = 90) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def review_deltas(cands: Sequence[Dict[str, Any]], decisions: Sequence[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Where the owner disagreed with the engine, as short human-readable lines (no addresses)."""
    by_canon = {d["canonical"]: d for d in decisions if d.get("canonical")}
    dropped, promoted, noted, kept = [], [], [], []
    for c in cands:
        d = by_canon.get(c.get("canonical") or "")
        if d is None:
            continue
        line = f"{_short(c.get('title'))} · {c.get('domain') or ''} · topic {c.get('topic') or '–'} · score {c.get('score') or '–'}"
        why = c.get("reason") or (c.get("content") or {}).get("reason") or (c.get("meta") or {}).get("reason") or ""
        if c.get("suggested") == "pick" and not d.get("pick"):
            dropped.append(f"{line} · engine said: {_short(why, 80)}")
        elif c.get("suggested") != "pick" and d.get("pick"):
            promoted.append(f"{line} · was {c.get('suggested') or 'not shortlisted'} · engine said: {_short(why, 80)}")
        elif d.get("pick"):
            kept.append(line)
        if d.get("note"):
            noted.append(f"{_short(c.get('title'), 60)} → owner note: {_short(d['note'], 120)}")
    return {"dropped": dropped, "promoted": promoted, "noted": noted, "kept": kept}


def build_prompt(criteria: Dict[str, Any], comment: str, deltas: Dict[str, List[str]]) -> str:
    brief = _criteria_brief(criteria)

    def block(items: List[str]) -> List[str]:
        return [f"- {x}" for x in items[:15]] or ["- (none)"]

    parts = [
        "You refine the selection criteria of a weekly curated newsletter. Below: the current criteria, the owner's "
        "comment after reviewing this week's engine suggestions, and the concrete disagreements between the engine "
        "and the owner.",
        "", brief, "",
        "Owner's review comment:", comment.strip() or "(none)", "",
        f"Suggested by the engine but DROPPED by the owner ({len(deltas['dropped'])}):",
        *block(deltas["dropped"]), "",
        f"PROMOTED by the owner from runners-up / long tail ({len(deltas['promoted'])}):",
        *block(deltas["promoted"]), "",
        "Per-row owner notes:", *block(deltas["noted"]), "",
        f"Write at most {MAX_LESSONS} NEW, generalisable selection rules the engine should apply next time — each a "
        "single sentence ≤ 160 characters, about content / topic / style / source type, not about one article. "
        "Do not restate rules already in the criteria. Never mention e-mail addresses. If the review teaches "
        "nothing new, return an empty list.",
        'Return JSON only: {"lessons": ["...", "..."]}',
    ]
    return "\n".join(parts)


def propose(run_id: int, *, model: Optional[str] = None, base_url: Optional[str] = None,
            criteria: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Ask the hub for ≤ 3 notes for a reviewed run and store them as pending lessons. Returns the rows."""
    run = db.get_run(run_id)
    if not run:
        raise ValueError(f"run {run_id} not found")
    start, end = run["window_start"], run["window_end"]
    review = db.get_review(start, end)
    if not review:
        raise ValueError(f"run {run_id} has no applied review yet — apply the review first")
    cfg = load_block("newsletter_triage")
    model = model or cfg.get("lessons_model", DEFAULT_MODEL)
    base_url = base_url or cfg.get("llm_hub_base_url") or load_full_config().get("newsletter_archive", {}).get(
        "llm_hub_base_url", "http://127.0.0.1:8000")
    if criteria is None:
        from newsletter.triage.criteria import CRITERIA_PATH  # noqa: PLC0415
        criteria = json.loads(CRITERIA_PATH.read_text(encoding="utf-8")) if CRITERIA_PATH.exists() else {}
    deltas = review_deltas(db.candidates(run_id), db.load_decisions(start, end))
    prompt = build_prompt(criteria, review.get("comment") or "", deltas)
    logger.info("🧠 distilling run %s (%s → %s) with %s — %d dropped, %d promoted, %d notes", run_id, start, end, model,
                len(deltas["dropped"]), len(deltas["promoted"]), len(deltas["noted"]))
    text = llm.call(base_url=base_url, model=model, prompt=prompt, max_tokens=600, timeout=180)
    data = _parse_json(text) or {}
    items = data.get("lessons", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    texts = [str(x).strip() for x in items if str(x).strip()][:MAX_LESSONS]
    if not texts:
        logger.info("🧠 model proposed nothing new")
        return []
    return db.add_lessons(texts, run_id=run_id, start=start, end=end, model=model)


def export(path: Optional[Path] = None) -> int:
    """Rewrite the tracked ``lessons.json`` from every accepted lesson (text + window + date, no ids/addresses)."""
    path = path or LESSONS_PATH
    rows = db.lessons(accepted=True)
    data = {"_readme": "Owner-accepted criteria notes distilled from weekly reviews (newsletter/triage/lessons.py). "
                       "Appended to the scoring prompt brief. Edit by accepting/rejecting in the control panel.",
            "lessons": [{"text": r["text"], "window": f"{r.get('window_start')}→{r.get('window_end')}",
                         "accepted": (r.get("accepted_at") or "")[:10]} for r in rows]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(rows)


def accept(ids: Sequence[int], *, accepted: bool = True) -> int:
    n = db.accept_lessons(ids, accepted=accepted)
    export()
    return n


def main(argv: Optional[List[str]] = None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=int, help="stored run id to distil")
    ap.add_argument("--accept", help="comma-separated lesson ids to accept")
    ap.add_argument("--reject", help="comma-separated lesson ids to un-accept")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--model", default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    try:
        db.ensure_schema()
    except db.SchemaMissing as err:
        print(f"❌ {err}")
        return 2
    if args.run:
        rows = propose(args.run, model=args.model)
        print(f"🧠 {len(rows)} proposal(s):" if rows else "🧠 nothing new proposed")
        for r in rows:
            print(f"  [{r['id']}] {r['text']}")
    if args.accept:
        n = accept([int(x) for x in args.accept.split(",") if x.strip()])
        print(f"✅ {n} accepted → {LESSONS_PATH}")
    if args.reject:
        n = accept([int(x) for x in args.reject.split(",") if x.strip()], accepted=False)
        print(f"↩️ {n} un-accepted → {LESSONS_PATH}")
    if args.list or not (args.run or args.accept or args.reject):
        for r in db.lessons():
            print(f"  [{r['id']}] {'✅' if r.get('accepted') else '⏳'} {r['text']}  ({r.get('window_start')}→{r.get('window_end')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
