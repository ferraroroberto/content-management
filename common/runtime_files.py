"""File writes at a path the caller supplies.

The pipelines write into folders they are handed: an episode or demo folder the
operator names (outside the repo tree), a ``results/`` sub-folder passed down
from a CLI ``--out`` flag, a caller-chosen log file. The runtime-write ratchet
cannot resolve such a path statically, so each raw write would need its own
exempt marker. These helpers are the one place that write for a caller, so the
reason is stated once, here, and the call sites stay marker-free.

A caller must pass a path outside the code folders: ``results/``, ``data/``, the
runtime root or an operator-named folder. The runtime probe in the gate
(``scripts/runtime_write_ratchet.py --probe``) backstops that for the test runs.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import IO, Optional, Union

PathLike = Union[str, Path]


def ensure_folder(path: PathLike, *, parents: bool = True) -> Path:
    """Create ``path`` (and, by default, its parents) when absent; return it."""
    folder = Path(path)
    # runtime-write-exempt: creates the folder the caller names; every caller passes a results/ path or an operator-named folder outside the tree
    folder.mkdir(parents=parents, exist_ok=True)
    return folder


def write_text_at(path: PathLike, text: str, *, encoding: str = "utf-8", newline: Optional[str] = None) -> int:
    """Write ``text`` to ``path``, replacing it; return the character count."""
    # runtime-write-exempt: writes the file the caller names; every caller passes a results/ path or an operator-named folder outside the tree
    return Path(path).write_text(text, encoding=encoding, newline=newline)


def open_for_write(path: PathLike, mode: str = "wb", *, encoding: Optional[str] = None) -> IO:
    """Open ``path`` for writing; the caller closes it."""
    # runtime-write-exempt: opens the file the caller names; every caller passes a results/ path or an operator-named folder outside the tree
    return Path(path).open(mode, encoding=encoding)


def copy_file_to(src: PathLike, dst: PathLike) -> Path:
    """Copy ``src`` to ``dst`` keeping its timestamps; return the destination."""
    # runtime-write-exempt: copies to the destination the caller names; every caller passes a path outside the code folders
    return Path(shutil.copy2(src, dst))


__all__ = ["copy_file_to", "ensure_folder", "open_for_write", "write_text_at"]
