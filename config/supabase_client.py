"""Single-source supabase-py client builder with the service_role -> key -> anon fallback.

The triage store (``newsletter.triage.db``) and the engagement pipeline
(``engagement.db.client``) each hand-rolled this loop and had already diverged:
one treated "table missing" as a working key and never warned on the anon
fallback, the other fell through on any error and did warn (issue #51). Both now
call :func:`build_supabase_client`, so the priority order, the probe and the
anon-fallback warning live in exactly one place.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("config.supabase_client")

KEY_PRIORITY = ("service_role_key", "key", "anon_key")


def build_supabase_client(
    cfg: dict,
    *,
    probe_table: str,
    probe_column: str,
    key_ok_despite_error: Optional[Callable[[Exception], bool]] = None,
) -> Any:
    """Return the first supabase-py client whose key survives a one-row probe.

    ``cfg`` is the ``supabase`` block of ``config.json``. Keys are tried in
    ``KEY_PRIORITY`` order; each is probed with ``select(probe_column)`` on
    ``probe_table``. A probe that raises moves on to the next key, unless
    ``key_ok_despite_error(err)`` is true — the triage store uses that to accept
    a key that answers "table not found" (the schema is simply not applied yet).
    Falling back to ``anon_key`` logs a warning (issue #51). Raises
    ``RuntimeError`` when ``supabase.url`` is missing or no key works.
    """
    from supabase import create_client  # local import — keep the dependency lazy for tests

    url = cfg.get("url")
    if not url:
        raise RuntimeError("Missing supabase.url in config.json")
    last_err: Optional[Exception] = None
    for label in KEY_PRIORITY:
        key = cfg.get(label)
        if not key:
            continue
        try:
            client = create_client(url, key)
            client.table(probe_table).select(probe_column).limit(1).execute()
        except Exception as err:  # noqa: BLE001 — expected fallback path, classified below
            last_err = err
            if key_ok_despite_error is None or not key_ok_despite_error(err):
                # Trying candidates in priority order is by design, so a failed
                # candidate is DEBUG; the terminal RuntimeError is the loud signal.
                logger.debug("🔑 supabase key %s not usable, trying next: %s", label, str(err)[:120])
                continue
        if label == "anon_key":
            logger.warning(
                "🔓 supabase client fell back to anon_key — service_role_key and key both unavailable"
            )
        logger.info("🔑 using supabase key: %s", label)
        return client
    raise RuntimeError(
        f"No working supabase key in config.supabase ({' / '.join(KEY_PRIORITY)}): {last_err}"
    )
