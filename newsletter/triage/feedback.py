"""Ingest a reviewed triage report: ticks → sender weights (overrides.json) + a decision log.

    python -m newsletter.triage.feedback results/newsletter/triage/triage-2026-08-08_2026-08-15.md [--dry-run]

Every shortlist / runner-up line in the report ends with ``<!-- cand:ID sender:ADDR -->``.
``- [x]`` = yes, ``- [ ]`` = no. Decisions are appended to
``results/newsletter/triage/feedback.jsonl`` and the per-sender tally over the
whole log drives the tier in ``overrides.json``:

* n ≥ 3 and yes-rate ≥ 0.75 → ``usually``; n ≥ 6 and yes-rate ≥ 0.9 → ``always``
* n ≥ 5 and yes-rate == 0 → ``rarely``
* ``never`` (owner-set, e.g. paywalled) is never changed automatically; entries
  with ``"source": "manual"`` are reported, not overwritten.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.console import force_utf8_stdio  # noqa: E402
from newsletter.triage.criteria import OVERRIDES_PATH  # noqa: E402

FEEDBACK_LOG = REPO_ROOT / "results" / "newsletter" / "triage" / "feedback.jsonl"
_LINE = re.compile(r"^\s*-\s\[( |x|X)\]\s(.*?)<!--\s*cand:(\S+)\s+sender:(\S+)\s*-->\s*$")
_TITLE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def parse_report(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        ticked = m.group(1).lower() == "x"
        body, cid, sender = m.group(2), m.group(3), m.group(4).lower()
        t = _TITLE.search(body)
        out.append({"cid": cid, "sender": sender, "yes": ticked,
                    "title": t.group(1) if t else body.strip()[:120], "url": t.group(2) if t else ""})
    return out


def tally(log_rows: List[Dict[str, Any]]) -> Dict[str, Tuple[int, int]]:
    counts: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    seen = set()
    for r in log_rows:
        key = (r.get("cid"), r.get("report"))
        if key in seen:
            continue
        seen.add(key)
        c = counts[r["sender"]]
        c[0] += 1
        c[1] += int(bool(r["yes"]))
    return {k: (v[0], v[1]) for k, v in counts.items()}


def tier_for(n: int, yes: int) -> str | None:
    rate = yes / n if n else 0.0
    if n >= 6 and rate >= 0.9:
        return "always"
    if n >= 3 and rate >= 0.75:
        return "usually"
    if n >= 5 and yes == 0:
        return "rarely"
    return None


def apply(decisions: List[Dict[str, Any]], *, report_name: str, overrides_path: Path = OVERRIDES_PATH,
          log_path: Path = FEEDBACK_LOG, dry_run: bool = False) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    rows = [dict(d, report=report_name, at=now) for d in decisions]
    existing: List[Dict[str, Any]] = []
    if log_path.exists():
        existing = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    existing = [r for r in existing if r.get("report") != report_name]   # re-ingesting a report replaces its rows
    all_rows = existing + rows
    counts = tally(all_rows)
    ov = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else {"senders": {}}
    senders = ov.setdefault("senders", {})
    changes: List[str] = []
    for addr, (n, yes) in counts.items():
        new_tier = tier_for(n, yes)
        cur = senders.get(addr)
        if cur and cur.get("tier") == "never":
            continue
        if cur and cur.get("source") == "manual" and new_tier and cur.get("tier") != new_tier:
            changes.append(f"{addr}: manual tier {cur.get('tier')} kept (feedback suggests {new_tier}, {yes}/{n})")
            continue
        if new_tier and (not cur or cur.get("tier") != new_tier):
            senders[addr] = {"tier": new_tier, "reason": f"feedback {yes}/{n} yes", "since": now[:10], "source": "feedback"}
            changes.append(f"{addr}: → {new_tier} ({yes}/{n})")
    if not dry_run:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in all_rows), encoding="utf-8")
        overrides_path.write_text(json.dumps(ov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"decisions": len(rows), "yes": sum(1 for r in rows if r["yes"]), "senders_tallied": len(counts),
            "changes": changes}


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    path = Path(args.report)
    decisions = parse_report(path.read_text(encoding="utf-8"))
    if not decisions:
        print("❌ no candidate lines found — is this a triage report?")
        return 2
    res = apply(decisions, report_name=path.name, dry_run=args.dry_run)
    print(f"✅ {res['decisions']} decisions ({res['yes']} yes) from {path.name}; {res['senders_tallied']} senders tallied"
          + (" (dry run)" if args.dry_run else ""))
    for c in res["changes"]:
        print("  •", c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
