"""Markdown report — the review surface for one window → one edition.

Layout: ① shortlist per topic (ticked = suggested pick; runners-up unticked;
⭐ / 🏆 suggestions flagged as suggestions), ② new senders to classify,
③ one section per email in inbox order with every link and its verdict,
④ stats footer. Each candidate line carries a hidden ``<!-- cand:ID -->`` so
``feedback.py`` can read the owner's ticks back.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from newsletter.triage.rank import TOPICS, WEAK_FILL, Candidate, Selection

VERDICT_ICON = {"selected": "✅", "runner-up": "🟡", "candidate": "⚪", "low": "⚪", "vetoed": "⛔",
                "duplicate": "♻️", "unknown": "❔", "pending": "❔"}


def _cand_line(c: Candidate, *, checked: bool, star: bool = False, must_read: bool = False) -> str:
    box = "[x]" if checked else "[ ]"
    flags = ("⭐ " if star else "") + ("🏆 " if must_read else "")
    score = f"{c.score:.1f}" if c.score is not None else "?"
    new = " **NEW sender**" if c.is_new_sender else ""
    summ = f" — {c.summary}" if c.summary else ""
    why = f" _({c.reason})_" if c.reason else ""
    return (f"- {box} {flags}**{score}** [{c.display_title}]({c.url}) · {c.sender_name}{new}"
            f"{summ}{why} <!-- cand:{c.cid} sender:{c.sender_address} -->")


def _link_line(c: Candidate) -> str:
    icon = VERDICT_ICON.get(c.verdict, "❔")
    score = f"{c.score:.1f}" if c.score is not None else "–"
    topic = c.topic or "–"
    why = c.reason or (c.content.reason if c.content and c.content.ok else (c.meta.reason if c.meta and c.meta.ok else ""))
    pay = " 🔒" if c.paywalled else ""
    return f"  - {icon} `{c.verdict:9}` {score:>4} · {topic[:12]:12} · [{c.display_title[:90]}]({c.url}){pay} — {why}"


def render_report(*, title: str, window_start: str, window_end: str, edition_hint: str, sel: Selection,
                  emails: Sequence[Dict[str, Any]], cands_by_email: Dict[str, List[Candidate]],
                  new_senders: Sequence[Tuple[str, str, int]], floor_senders: Sequence[Tuple[str, int]],
                  stats: Dict[str, Any], backtest: Optional[Dict[str, Any]] = None) -> str:
    L: List[str] = []
    L.append(f"# {title}")
    L.append("")
    L.append(f"Window **{window_start} → {window_end}** · target edition **{edition_hint}** · "
             f"{stats.get('emails', 0)} emails · {stats.get('links', 0)} links · {stats.get('scored', 0)} scored · "
             f"{stats.get('fetched', 0)} fetched · {stats.get('llm_calls', 0)} LLM calls")
    L.append("")
    L.append("Tick = keep, untick = drop. ⭐ = suggested star, 🏆 = suggested must-read (suggestions, not decisions). "
             "Run `python -m newsletter.triage.feedback <this file>` after reviewing to teach the sender weights.")
    L.append("")
    if backtest:
        L.append(f"> **Backtest vs {backtest.get('edition')}** — precision@shortlist {backtest.get('precision', 0):.0%} "
                 f"({backtest.get('hits', 0)}/{backtest.get('shortlist', 0)} were real picks in some edition), "
                 f"recall of {backtest.get('edition')}'s picks from this window {backtest.get('recall', 0):.0%} "
                 f"({backtest.get('recalled', 0)}/{backtest.get('truth', 0)}; +runners-up {backtest.get('recall_runners', 0):.0%}). "
                 f"Missed: {backtest.get('missed_preview', '')}")
        L.append("")
        if backtest.get("diag"):
            L.append("<details><summary>where each real pick ended up</summary>")
            L.append("")
            for d in backtest["diag"]:
                L.append(f"- {d}")
            L.append("")
            L.append("</details>")
            L.append("")
    L.append("## Shortlist")
    L.append("")
    for t in TOPICS:
        picks, runners = sel.picks[t], sel.runners[t]
        short = sel.short.get(t, 0)
        L.append(f"### {t} — {len(picks)}/8" + (f" ⚠️ short by {short} (backfill from `next` / classics)" if short else ""))
        L.append("")
        for c in picks:
            weak = (c.score or 0) < WEAK_FILL
            if weak and not c.reason:
                c.reason = "weak fill — tick only if you agree"
            L.append(_cand_line(c, checked=not weak, star=sel.stars.get(t) is c, must_read=sel.must_read is c))
        if runners:
            L.append("")
            L.append("<details><summary>runners-up</summary>")
            L.append("")
            for c in runners:
                L.append(_cand_line(c, checked=False))
            L.append("")
            L.append("</details>")
        L.append("")
    if new_senders:
        L.append("## New senders — set a tier in `overrides.json` (always / usually / rarely / never / review)")
        L.append("")
        for name, addr, n in new_senders:
            L.append(f"- `{addr}` — {name} ({n} email{'s' if n != 1 else ''} in window)")
        L.append("")
    L.append("## Emails (inbox order)")
    L.append("")
    for em in emails:
        cands = cands_by_email.get(em["message_id"], [])
        n_sel = sum(1 for c in cands if c.verdict == "selected")
        n_run = sum(1 for c in cands if c.verdict == "runner-up")
        head = f"{'✅' if n_sel else ('🟡' if n_run else '▫️')} **{em['sender_name']}** — {em['subject'][:100]} · {em['timestamp'][:10]}"
        tier = em.get("sender_basis") or ""
        L.append(f"### {head}")
        L.append(f"_{len(cands)} link{'s' if len(cands) != 1 else ''} · sender prior: {tier}_" + (" · **NEW**" if em.get("is_new") else ""))
        L.append("")
        for c in cands:
            L.append(_link_line(c))
        if not cands:
            L.append("  - (no candidate links)")
        L.append("")
    if floor_senders:
        L.append("<details><summary>Floor senders in this window (0 picks in 54 weeks — listed, ranked last)</summary>")
        L.append("")
        for name, n in floor_senders:
            L.append(f"- {name} ({n})")
        L.append("")
        L.append("</details>")
        L.append("")
    L.append("## Stats")
    L.append("")
    for k, v in stats.items():
        L.append(f"- {k}: {v}")
    L.append("")
    return "\n".join(L)
