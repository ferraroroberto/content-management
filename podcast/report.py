"""``metrics.md``: one cost table per episode (stage wall time, whisper time,
hub tokens, metered-equivalent cost) plus the per-clip quality scores."""

from __future__ import annotations

import json
from pathlib import Path

from podcast.episode import Episode
from podcast.metrics import load_metrics, summarize

SCORES_FILE = "scores.json"


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.2f}" if value < 1000 else f"{value:,.0f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def stage_table(rows: list[dict]) -> str:
    head = "| stage | wall (s) | whisper (s) | LLM calls | models | input tok | output tok | cost (USD, metered equiv.) |"
    lines = [head, "|---|---:|---:|---:|---|---:|---:|---:|"]
    for r in rows:
        est = "~" if r.get("estimated") else ""
        cost = _fmt(r["cost_usd"])
        if r["cost_usd"] is None and r.get("known_cost_usd"):
            cost = f"n/a (priced stages: {_fmt(r['known_cost_usd'])})"
        lines.append(f"| {r['stage']} | {_fmt(r['wall_s'])} | {_fmt(r['whisper_s'])} | {r['llm_calls']} | "
                     f"{r['models']} | {est}{_fmt(r['in_tok'])} | {est}{_fmt(r['out_tok'])} | {cost} |")
    lines.append("\n`~` = estimated from characters (the hub reports no usage for that backend); "
                 "n/a = a model with no list price in `podcast.llm_rates_usd_per_mtok`.")
    return "\n".join(lines)


def score_table(scores: list[dict]) -> str:
    head = "| # | title | hook ≤3 s | self-contained | caption check | framing 1:1 | framing 9:16 | mean |"
    lines = [head, "|---:|---|---:|---:|---:|---:|---:|---:|"]
    for s in scores:
        lines.append(f"| {s['number']} | {s['title']} | {s.get('hook', 'n/a')} | {s.get('self_contained', 'n/a')} | "
                     f"{s.get('caption_accuracy', 'n/a')} ({s.get('caption_wer_pct', 'n/a')}% WER) | "
                     f"{s.get('framing_1x1', 'n/a')} | {s.get('framing_9x16', 'n/a')} | {s.get('mean', 'n/a')} |")
    lines.append("\nCaption check: WER between the captions (a whisper pass over the clip's mixed audio) and the "
                 "episode's independent per-track pass. Neither is ground truth, so a low score means "
                 "\"watch this clip's captions\".")
    return "\n".join(lines)


def write_report(ep: Episode, cfg: dict) -> Path:
    rows = summarize(load_metrics(ep.package), cfg.get("llm_rates_usd_per_mtok", {}))
    parts = [f"# Episode cost and quality - {ep.base_name}\n",
             "Cost is the metered-API equivalent at list prices (`podcast.llm_rates_usd_per_mtok`); "
             "the hub runs on the subscription, so nothing is billed per call.\n",
             stage_table(rows) if rows else "_No stage has run yet._"]
    scores_path = ep.package / SCORES_FILE
    if scores_path.exists():
        scores = json.loads(scores_path.read_text(encoding="utf-8"))
        parts += ["\n## Clip quality (1-5)\n", score_table(scores)]
    path = ep.package / "metrics.md"
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path
