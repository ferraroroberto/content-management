"""File writes at a path the caller supplies.

The pipelines write into folders they are handed: an episode or demo folder the
operator names (outside the repo tree), a ``results/`` sub-folder passed down
from a CLI ``--out`` flag, a caller-chosen log file. The runtime-write ratchet
cannot resolve such a path statically, so each raw write would need its own
exempt marker. These helpers are the one place that write for a caller, so the
reason is stated once, here, and the call sites stay marker-free.

The marker-free claim holds because every entry point refuses, before any write,
a path that resolves (links and ``..`` followed) under one of the code folders
the runtime-write ratchet guards. Runtime output goes under ``results/``, ``data/``,
the runtime root or an operator-named folder; gitignored live state elsewhere in
the checkout stays writable. The runtime probe in the gate
(``scripts/runtime_write_ratchet.py --probe``) backstops the test runs.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import IO, Optional, Union

PathLike = Union[str, Path]

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The folders ``[runtime_write_ratchet] code_folders`` in ``.fleet.toml`` guards;
#: a test keeps the two lists equal.
CODE_FOLDERS = ("src", "scripts", "config", "app", "check_ip", "demo_video", "engagement",
                "newsletter", "planning", "podcast", "recorder", "reporting")


def _key(path: Path) -> Path:
    # Windows paths compare case-insensitively, and a not-yet-created tail is not case-normalised by resolve().
    return Path(os.path.normcase(path))


def _checked(path: PathLike) -> Path:
    """``path`` resolved, or ``ValueError`` when it sits under a code folder."""
    resolved = Path(path).expanduser().resolve()
    target = _key(resolved)
    for name in CODE_FOLDERS:
        folder = _key(REPO_ROOT / name)
        if target == folder or folder in target.parents:
            logger.info("⚠️ runtime write refused: target is under the %s/ code folder", name)
            raise ValueError(
                f"Refusing to write under the {name}/ code folder. "
                "Runtime output belongs in results/, data/ or a folder outside the checkout."
            )
    return resolved


def ensure_folder(path: PathLike, *, parents: bool = True) -> Path:
    """Create ``path`` (and, by default, its parents) when absent; return it."""
    folder = Path(path)
    target = _checked(folder)
    # runtime-write-exempt: creates the folder the caller names; _checked() above raises when it is under a code folder
    target.mkdir(parents=parents, exist_ok=True)
    return folder


def write_text_at(path: PathLike, text: str, *, encoding: str = "utf-8", newline: Optional[str] = None) -> int:
    """Write ``text`` to ``path``, replacing it; return the character count."""
    target = _checked(path)
    # runtime-write-exempt: writes the file the caller names; _checked() above raises when it is under a code folder
    return target.write_text(text, encoding=encoding, newline=newline)


def open_for_write(path: PathLike, mode: str = "wb", *, encoding: Optional[str] = None) -> IO:
    """Open ``path`` for writing; the caller closes it."""
    target = _checked(path)
    # runtime-write-exempt: opens the file the caller names; _checked() above raises when it is under a code folder
    return target.open(mode, encoding=encoding)


def copy_file_to(src: PathLike, dst: PathLike) -> Path:
    """Copy ``src`` to ``dst`` keeping its timestamps; return the destination."""
    target = _checked(dst)
    # runtime-write-exempt: copies to the destination the caller names; _checked() above raises when it is under a code folder
    return Path(shutil.copy2(src, target))


def fold_legacy_folder(legacy: PathLike, target: PathLike) -> int:
    """Move every file under ``legacy`` that ``target`` lacks into ``target``; return how many moved.

    For output that used to land in a code folder and now lands under ``results/``: the
    old files stay readable without a manual step. A name already in ``target`` is left
    alone, and a moved file is gone from ``legacy`` so a file deleted from ``target``
    later does not come back.
    """
    source = Path(legacy)
    if not source.is_dir():
        return 0
    moved = 0
    for old in sorted(p for p in source.rglob("*") if p.is_file()):
        new = Path(target) / old.relative_to(source)
        if new.exists():
            continue
        ensure_folder(new.parent)
        copy_file_to(old, new)
        old.unlink()
        moved += 1
    if moved:
        logger.info("✅ moved %d runtime file(s) from a code folder to %s", moved, Path(target).name)
    return moved


__all__ = ["CODE_FOLDERS", "copy_file_to", "ensure_folder", "fold_legacy_folder", "open_for_write", "write_text_at"]
