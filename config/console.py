"""Shared console helpers.

Single source of truth for making stdout/stderr safe to write UTF-8 (emoji,
em-dashes) on a Windows console that defaults to cp1252. Every entry point
that logs emoji must call :func:`force_utf8_stdio` once at startup instead of
re-inlining the reconfigure loop.
"""

from __future__ import annotations

import sys


def force_utf8_stdio() -> None:
    """Reconfigure ``sys.stdout`` / ``sys.stderr`` to UTF-8 with ``errors="replace"``.

    On a default Windows console ``sys.stdout`` is cp1252, so any non-cp1252
    glyph (e.g. an emoji in a log message) raises ``UnicodeEncodeError`` on
    write and the logging machinery prints a ``--- Logging error ---``
    traceback instead of the message. Reconfiguring with ``errors="replace"``
    makes a non-encodable glyph degrade to a replacement char instead of
    crashing. Guarded with ``hasattr`` because not every stream (e.g. a pytest
    capture buffer) supports ``reconfigure``.
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


__all__ = ["force_utf8_stdio"]
