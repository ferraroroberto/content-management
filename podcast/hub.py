"""LLM calls for the podcast stages — through the local hub, with usage
recorded on the active stage's ledger."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from newsletter.llm import call_with_usage
from podcast.metrics import StageRecord

logger = logging.getLogger("podcast.hub")

CHARS_PER_TOKEN = 4
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def ask(cfg: dict, rec: StageRecord, role: str, prompt: str | list, *,
        system: Optional[str] = None, max_tokens: int = 4000, timeout: int = 900) -> str:
    """One hub round-trip with the model configured for ``role``."""
    model = cfg["models"][role]
    t0 = time.perf_counter()
    text, usage = call_with_usage(base_url=cfg["llm_hub_base_url"], model=model, prompt=prompt,
                                  system=system, max_tokens=max_tokens, timeout=timeout)
    elapsed = time.perf_counter() - t0
    if not any(usage.values()):
        # The hub reports no usage for some backends (Gemini): record a
        # chars/4 estimate, flagged, rather than a zero that reads as free.
        usage = {**usage, "input_tokens": (len(str(prompt)) + len(system or "")) // CHARS_PER_TOKEN,
                 "output_tokens": len(text) // CHARS_PER_TOKEN, "estimated": True}
    rec.add_llm(model, usage, elapsed)
    logger.info("ℹ️ %s via %s: %d in / %d out tokens%s, %.1fs", role, model, usage["input_tokens"],
                usage["output_tokens"], " (estimated)" if usage.get("estimated") else "", elapsed)
    return text


def parse_json(text: str) -> Any:
    """Parse a JSON reply, tolerating a markdown fence or prose around it."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (cleaned.find("["), cleaned.find("{")) if i >= 0]
    if not starts:
        raise ValueError(f"No JSON in model reply: {text[:200]!r}")
    start = min(starts)
    end = max(cleaned.rfind("]"), cleaned.rfind("}"))
    return json.loads(cleaned[start:end + 1])


def ask_json(cfg: dict, rec: StageRecord, role: str, prompt: str | list, *,
             system: Optional[str] = None, max_tokens: int = 8000, attempts: int = 2) -> Any:
    """``ask`` + ``parse_json``, retrying once on unparseable output."""
    last: Exception | None = None
    for attempt in range(attempts):
        text = ask(cfg, rec, role, prompt, system=system, max_tokens=max_tokens)
        try:
            return parse_json(text)
        except (ValueError, json.JSONDecodeError) as exc:
            last = exc
            logger.warning("⚠️ %s reply was not valid JSON (attempt %d): %s", role, attempt + 1, exc)
    raise RuntimeError(f"{role}: no valid JSON after {attempts} attempts") from last
