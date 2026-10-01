"""Per-stage cost ledger: wall time, whisper time, LLM tokens — persisted to
``<package>/metrics.json`` so a resumed run keeps earlier stages' numbers."""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

METRICS_FILE = "metrics.json"


@dataclass
class StageRecord:
    stage: str
    wall_s: float = 0.0
    whisper_s: float = 0.0
    llm: list[dict] = field(default_factory=list)

    def add_llm(self, model: str, usage: dict, seconds: float) -> None:
        self.llm.append({"model": model, "seconds": round(seconds, 2), **usage})

    def add_whisper(self, seconds: float) -> None:
        self.whisper_s += seconds


def load_metrics(package: Path) -> dict:
    path = package / METRICS_FILE
    if not path.exists():
        return {"stages": {}}
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def stage_timer(package: Path, stage: str) -> Iterator[StageRecord]:
    """Time a stage and write its record into ``metrics.json`` when it succeeds."""
    rec = StageRecord(stage)
    t0 = time.perf_counter()
    yield rec
    rec.wall_s = time.perf_counter() - t0
    data = load_metrics(package)
    data["stages"][stage] = {
        "wall_s": round(rec.wall_s, 1),
        "whisper_s": round(rec.whisper_s, 1),
        "llm": rec.llm,
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    package.mkdir(parents=True, exist_ok=True)
    (package / METRICS_FILE).write_text(json.dumps(data, indent=2), encoding="utf-8")


def llm_cost_usd(call: dict, rates: dict) -> Optional[float]:
    """Metered-equivalent USD for one hub call; None when the model has no rate."""
    rate = rates.get(call.get("model", ""))
    if not rate:
        return None
    return (call.get("input_tokens", 0) * rate.get("input", 0)
            + call.get("output_tokens", 0) * rate.get("output", 0)
            + call.get("cache_read_input_tokens", 0) * rate.get("cache_read", 0)
            + call.get("cache_creation_input_tokens", 0) * rate.get("cache_write", 0)) / 1e6


def summarize(data: dict, rates: dict) -> list[dict]:
    """One row per stage plus a total row: wall, whisper, calls, tokens, cost."""
    rows: list[dict] = []
    for stage, rec in data.get("stages", {}).items():
        calls = rec.get("llm", [])
        costs = [llm_cost_usd(c, rates) for c in calls]
        rows.append({
            "stage": stage,
            "wall_s": rec.get("wall_s", 0.0),
            "whisper_s": rec.get("whisper_s", 0.0),
            "llm_calls": len(calls),
            "models": ", ".join(sorted({c.get("model", "?") for c in calls})),
            "in_tok": sum(c.get("input_tokens", 0) + c.get("cache_read_input_tokens", 0)
                          + c.get("cache_creation_input_tokens", 0) for c in calls),
            "out_tok": sum(c.get("output_tokens", 0) for c in calls),
            "cost_usd": None if any(c is None for c in costs) else round(sum(costs), 4),
            "estimated": any(c.get("estimated") for c in calls),
        })
    if rows:
        known = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
        rows.append({
            "stage": "TOTAL",
            "wall_s": round(sum(r["wall_s"] for r in rows), 1),
            "whisper_s": round(sum(r["whisper_s"] for r in rows), 1),
            "llm_calls": sum(r["llm_calls"] for r in rows),
            "models": "",
            "in_tok": sum(r["in_tok"] for r in rows),
            "out_tok": sum(r["out_tok"] for r in rows),
            "cost_usd": round(sum(known), 4) if len(known) == len(rows) else None,
            "estimated": any(r["estimated"] for r in rows),
            "known_cost_usd": round(sum(known), 4),
        })
    return rows
