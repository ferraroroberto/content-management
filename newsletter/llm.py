"""Thin wrapper around the local-llm-hub Anthropic-shape /v1/messages endpoint."""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger("newsletter_archive.llm")

# Transient network failures worth retrying — the hub may be briefly slow
# or mid-restart. A genuine bad response (4xx/5xx) is not retried here.
_RETRYABLE = (requests.ReadTimeout, requests.ConnectionError)


def call(*, base_url: str, model: str, prompt: str, max_tokens: int = 512,
         timeout: int = 120, retries: int = 2, backoff: float = 2.0) -> str:
    """Single-turn user → assistant round-trip. Returns the assistant text.

    On a transient network error (read timeout / connection error) the call
    is retried up to ``retries`` times with exponential backoff before the
    error is re-raised — so one slow blip doesn't discard a fully-extracted
    article. Each attempt uses the full ``timeout``.
    """
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    logger.debug("LLM call: model=%s max_tokens=%d prompt_chars=%d",
                 model, max_tokens, len(prompt))
    last_exc: Exception | None = None
    for attempt in range(1, retries + 2):  # 1 initial + `retries` retries
        try:
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
        except _RETRYABLE as exc:
            last_exc = exc
            if attempt <= retries:
                wait = backoff * (2 ** (attempt - 1))
                logger.warning("⚠️ LLM call failed (%s) — attempt %d/%d, "
                               "retrying in %.0fs",
                               type(exc).__name__, attempt, retries + 1, wait)
                time.sleep(wait)
            else:
                logger.error("❌ LLM call failed after %d attempts: %s",
                              retries + 1, type(exc).__name__)
    assert last_exc is not None
    raise last_exc


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
