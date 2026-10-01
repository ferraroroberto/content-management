"""Thin wrapper around the local-llm-hub Anthropic-shape /v1/messages endpoint.

Backed by the `anthropic` SDK (global CLAUDE.md "Don't duplicate hub
functionality in downstream apps") rather than a hand-rolled `requests.post`
+ retry loop — the SDK already implements retry/backoff on transient
network errors and response parsing, so this module only adapts the SDK
call to this project's `call()`/`health_check()` shape.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from anthropic import Anthropic

logger = logging.getLogger("newsletter_archive.llm")


@lru_cache(maxsize=None)
def _client(base_url: str) -> Anthropic:
    return Anthropic(api_key="local-dummy", base_url=base_url)


def call(*, base_url: str, model: str, prompt: str, max_tokens: int = 512,
         timeout: int = 120, retries: int = 2) -> str:
    """Single-turn user → assistant round-trip. Returns the assistant text.

    Transient network errors (read timeout / connection error) are retried
    by the SDK's own backoff up to ``retries`` times before the error is
    re-raised — so one slow blip doesn't discard a fully-extracted article.
    """
    text, _usage = call_with_usage(base_url=base_url, model=model, prompt=prompt,
                                   max_tokens=max_tokens, timeout=timeout, retries=retries)
    return text


def call_with_usage(*, base_url: str, model: str, prompt: str | list,
                    system: Optional[str] = None, max_tokens: int = 512,
                    timeout: int = 120, retries: int = 2) -> tuple[str, dict]:
    """Like :func:`call`, plus an optional system prompt, content blocks
    (``prompt`` may be a list, e.g. image + text) and the hub's token usage.

    Returns ``(text, usage)`` where ``usage`` carries ``input_tokens``,
    ``output_tokens`` and the two cache counters (0 when absent).
    """
    logger.debug("LLM call: model=%s max_tokens=%d prompt_chars=%d",
                 model, max_tokens, len(str(prompt)))
    client = _client(base_url.rstrip("/"))
    kwargs: dict = {"model": model, "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}]}
    if system:
        kwargs["system"] = system
    try:
        msg = client.with_options(
            timeout=float(timeout), max_retries=retries,
        ).messages.create(**kwargs)
    except Exception as exc:
        logger.error("❌ LLM call failed after %d retries: %s: %s",
                      retries, type(exc).__name__, exc)
        raise
    usage = {
        key: int(getattr(msg.usage, key, 0) or 0)
        for key in ("input_tokens", "output_tokens",
                    "cache_read_input_tokens", "cache_creation_input_tokens")
    }
    for part in msg.content or []:
        if getattr(part, "type", None) == "text":
            return (part.text or "").strip(), usage
    raise RuntimeError(f"Unexpected /v1/messages response shape: {msg!r}")


def health_check(*, base_url: str, model: str, timeout: int = 30) -> bool:
    """Fast pre-flight probe of the LLM hub.

    Makes one tiny generation call so a dead or wedged hub fails in seconds
    instead of after the first full-length article timeout. Returns True
    only if the hub answers with usable text.
    """
    try:
        text = call(
            base_url=base_url, model=model,
            prompt="Reply with: ok", max_tokens=4,
            timeout=timeout, retries=0,
        )
    except Exception as exc:  # noqa: BLE001 — any failure means "not healthy"
        logger.error("❌ LLM hub health check failed: %s: %s",
                      type(exc).__name__, exc)
        return False
    return bool(text)
