"""Locked, atomically-flushed JSON-file cache: the base of the triage caches.

``FetchCache`` (fetch.py), ``LLMCache`` (score.py) and ``RedirectCache``
(gmail.py) differ only in their get/put keys and value types; the load-with-a-
warning, lock and tmp-file-plus-replace flush live here once.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("newsletter_triage.json_cache")


class JsonFileCache:
    """``{key: value}`` persisted as one JSON file; ``flush`` replaces it atomically."""

    def __init__(self, path: Path, *, label: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("⚠️ %s unreadable (%s) — starting empty", label, exc)

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def __len__(self) -> int:
        return len(self._data)
