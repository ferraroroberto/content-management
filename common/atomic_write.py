"""Atomic file writes: a reader sees the old file or the new one, never half of either.

The one place this repo calls ``os.replace`` for a write. Each write goes to a
unique ``mkstemp`` sibling of the target (two processes writing the same file
never share a temp), then ``os.replace`` swaps it in. When the write or the swap
raises, the temp is unlinked and the error re-raised, so the old content stays
intact, no orphan is left beside it, and each caller keeps its own failure
policy. The parent folder must already exist.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Union

PathLike = Union[str, "os.PathLike[str]"]


def _write(path: PathLike, payload: Union[str, bytes], encoding: str = "utf-8") -> None:
    target = Path(path)
    fd, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp")
    try:
        if isinstance(payload, bytes):
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
        else:
            with os.fdopen(fd, "w", encoding=encoding) as handle:
                handle.write(payload)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def atomic_write_text(path: PathLike, text: str, encoding: str = "utf-8") -> None:
    """Replace ``path`` with ``text``; newlines are translated as ``Path.write_text`` does."""
    _write(path, text, encoding)


def atomic_write_bytes(path: PathLike, data: bytes) -> None:
    """Replace ``path`` with ``data``."""
    _write(path, data)


__all__ = ["atomic_write_bytes", "atomic_write_text"]
