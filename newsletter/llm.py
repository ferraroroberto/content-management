"""Thin wrapper around the local-llm-hub Anthropic-shape /v1/messages endpoint."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger("newsletter_archive.llm")


def call(*, base_url: str, model: str, prompt: str, max_tokens: int = 512,
         timeout: int = 120) -> str:
    """Single-turn user → assistant round-trip. Returns the assistant text."""
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    logger.debug("LLM call: model=%s max_tokens=%d prompt_chars=%d",
                 model, max_tokens, len(prompt))
    resp = requests.post(
        f"{base_url.rstrip('/')}/v1/messages",
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    for part in data.get("content", []):
        if part.get("type") == "text":
            return (part.get("text") or "").strip()
    raise RuntimeError(f"Unexpected /v1/messages response shape: {data!r}")
