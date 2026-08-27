"""Owner decisions → sender weights (overrides.json). Decisions live in Supabase ``triage_decisions``.

    python -m newsletter.triage.feedback results/newsletter/triage/triage-2026-08-08_2026-08-15.md [--dry-run]

Two entry points feed the same store:

* the control panel (``newsletter.triage.review.apply_review``) saves the review table's ticks;
* this CLI parses a reviewed markdown report — every shortlist / runner-up line ends with
  ``<!-- cand:ID sender:ADDR -->``; ``- [x]`` = yes, ``- [ ]`` = no — and saves them for the
  window named in the file (``triage-<start>_<end>.md``).

The per-sender tally over **all** stored decisions drives the tier in ``overrides.json``:

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.console import force_utf8_stdio  # noqa: E402
from newsletter.cache import canonicalize_url  # noqa: E402
from newsletter.triage import db  # noqa: E402
from newsletter.triage.criteria import OVERRIDES_PATH  # noqa: E402

_LINE = re.compile(r"^\s*-\s\[( |x|X)\]\s(.*?)<!--\s*cand:(\S+)\s+sender:(\S+)\s*-->\s*$")
_TITLE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_WINDOW = re.compile(r"triage-(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})")


def parse_report(text: str) -> List[Dict[str, Any]]:
    """Tick lines → decision dicts (``cid, sender_address, pick, title, url, canonical, star, must_read``)."""
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        ticked = m.group(1).lower() == "x"
        body, cid, sender = m.group(2), m.group(3), m.group(4).lower()
        t = _TITLE.search(body)
        url = t.group(2) if t else ""
        out.append({"cid": cid, "sender_address": sender, "pick": ticked, "star": ticked and "⭐" in body,
                    "must_read": ticked and "🏆" in body, "title": t.group(1) if t else body.strip()[:120],
                    "url": url, "canonical": canonicalize_url(url) if url else ""})
    return out


def window_from_name(name: str) -> Optional[Tuple[str, str]]:
    m = _WINDOW.search(name)
    return (m.group(1), m.group(2)) if m else None


def tier_for(n: int, yes: int) -> Optional[str]:
    rate = yes / n if n else 0.0
    if n >= 6 and rate >= 0.9:
        return "always"
    if n >= 3 and rate >= 0.75:
        return "usually"
    if n >= 5 and yes == 0:
        return "rarely"
    return None


def apply_tiers(counts: Dict[str, Tuple[int, int]], *, overrides_path: Path = OVERRIDES_PATH,
                dry_run: bool = False) -> List[str]:
    """Turn a ``{sender: (n, yes)}`` tally into ``overrides.json`` tier changes (owner tiers untouched)."""
    now = datetime.now(timezone.utc).isoformat()
    ov = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else {"senders": {}}
    senders = ov.setdefault("senders", {})
    changes: List[str] = []
    for addr, (n, yes) in sorted(counts.items()):
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
    if not dry_run and changes:
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        overrides_path.write_text(json.dumps(ov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changes


def apply(decisions: List[Dict[str, Any]], *, window: Tuple[str, str], overrides_path: Path = OVERRIDES_PATH,
          dry_run: bool = False) -> Dict[str, Any]:
    """Save the decisions for ``window`` to the store, re-tally every stored decision, update tiers."""
    n = 0 if dry_run else db.save_decisions(window[0], window[1], decisions)
    counts = db.decision_tally()
    if dry_run:   # fold the unsaved decisions into the tally so the preview is honest
        for d in decisions:
            addr = (d.get("sender_address") or "").lower()
            if addr:
                c = counts.get(addr, (0, 0))
                counts[addr] = (c[0] + 1, c[1] + int(bool(d.get("pick"))))
    changes = apply_tiers(counts, overrides_path=overrides_path, dry_run=dry_run)
    return {"decisions": len(decisions), "saved": n, "yes": sum(1 for d in decisions if d.get("pick")),
            "senders_tallied": len(counts), "changes": changes}


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    path = Path(args.report)
    window = window_from_name(path.name)
    if not window:
        print("❌ cannot read the window from the file name (expected triage-<start>_<end>.md)")
        return 2
    decisions = parse_report(path.read_text(encoding="utf-8"))
    if not decisions:
        print("❌ no candidate lines found — is this a triage report?")
        return 2
    try:
        db.ensure_schema()
    except db.SchemaMissing as err:
        print(f"❌ {err}")
        return 2
    res = apply(decisions, window=window, dry_run=args.dry_run)
    print(f"✅ {res['decisions']} decisions ({res['yes']} yes) for {window[0]} → {window[1]}; "
          f"{res['senders_tallied']} senders tallied" + (" (dry run)" if args.dry_run else ""))
    for c in res["changes"]:
        print("  •", c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
