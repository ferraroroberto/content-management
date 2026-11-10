"""Run state of the planning pipeline's result record.

``planning_pipeline.py`` writes a ``running`` record when a run starts and before
each stage, then replaces it with the final record. A process that is killed
cannot write anything, so a record still saying ``running`` whose process is gone
is the only trace such a run leaves: ``effective_verdict`` reads it as
``interrupted`` instead of letting the previous clean run stand in for it.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from common.atomic_write import atomic_write_text
from common.runtime_files import ensure_folder

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "planning"
LATEST_NAME = "latest-result.json"

# A real run takes minutes; a ``running`` record older than this is a dead run
# whose pid the OS has since handed to an unrelated process.
MAX_RUN_AGE_S = 6 * 3600

_WIN_STILL_ACTIVE = 259
_WIN_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_ACCESS_DENIED = 5


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid is running; never signals it."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(_WIN_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # Access denied still proves the process exists; anything else means it is gone.
        return ctypes.get_last_error() == _WIN_ACCESS_DENIED
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _WIN_STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def running_record(*, mode: str, started_at: datetime, stage: Optional[str],
                   completed: list[str]) -> dict:
    """The in-flight record: the stage under way and the stages already finished."""
    return {
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": None,
        "mode": mode,
        "exit_code": None,
        "verdict": "running",
        "pid": os.getpid(),
        "stage": stage,
        "completed_stages": completed,
        "summary_path": None,
        "platforms": [],
    }


def write_result(result: dict, started_at: datetime, results_dir: Optional[Path] = None) -> Path:
    """Write the timestamped record plus the stable ``latest-result.json`` pointer.

    Both writes are atomic: the planning tab and the autoheal skill read the
    pointer while a run is rewriting it between stages.
    """
    out_dir = ensure_folder(results_dir or RESULTS_DIR)
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    out_path = out_dir / f"{started_at.strftime('%Y-%m-%d-%H%M%S')}-result.json"
    atomic_write_text(out_path, payload)
    atomic_write_text(out_dir / LATEST_NAME, payload)
    return out_path


def effective_verdict(result: dict, *, now: Optional[datetime] = None) -> str:
    """The record's verdict, with a dead ``running`` run read as ``interrupted``."""
    verdict = str(result.get("verdict", "?"))
    if verdict != "running":
        return verdict
    pid = result.get("pid")
    if not isinstance(pid, int):
        return "interrupted"
    try:
        age_s = ((now or datetime.now()) - datetime.fromisoformat(result["started_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        return "interrupted"
    if age_s > MAX_RUN_AGE_S:
        return "interrupted"
    return "running" if pid_alive(pid) else "interrupted"


def write_summary(markdown: str, started_at: datetime, results_dir: Optional[Path] = None) -> Path:
    """Write the run's markdown summary beside its result record."""
    out_dir = ensure_folder(results_dir or RESULTS_DIR)
    out_path = out_dir / f"{started_at.strftime('%Y-%m-%d-%H%M%S')}-summary.md"
    atomic_write_text(out_path, markdown)
    return out_path
