"""Where the reporting pipeline reads and writes its raw and processed results.

The config values (``folder_results_raw``, ``folder_results_processed``) are relative to
the repository root, e.g. ``results/raw``. The pipeline used to join them onto
``reporting/``, which put live output inside a code folder; the first call for a folder
moves what is still there into the repo-level folder.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from common.runtime_files import REPO_ROOT, fold_legacy_folder


@lru_cache(maxsize=None)
def results_folder(relative: str) -> Path:
    """``<repo>/<relative>``, after folding in any files left under ``reporting/<relative>``."""
    parts = relative.split("/")
    folder = REPO_ROOT.joinpath(*parts)
    fold_legacy_folder(REPO_ROOT / "reporting" / Path(*parts), folder)
    return folder
