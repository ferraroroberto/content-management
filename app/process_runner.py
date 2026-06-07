"""Subprocess runner with live log streaming for the control-panel app.

Each pipeline (reporting / planning / newsletter / engagement-scrape /
engagement-classify) is launched via subprocess.Popen. A background thread
reads stdout line-by-line into a deque kept on st.session_state, so the
Streamlit tab can render the rolling tail without blocking the UI thread.

Status badges, run timestamps, and stop buttons are all derived from
session state — no need for the pipeline scripts to know they're being
controlled.

Usage from a tab module:

    from app.process_runner import (
        start_pipeline, render_log_panel, is_running, stop_pipeline,
    )

    if st.button("▶ run reporting", disabled=is_running("reporting")):
        start_pipeline("reporting", [
            str(VENV_PY), "reporting_pipeline.py", "--date", today, "--yes",
        ])
    render_log_panel("reporting")
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
LOG_TAIL_MAX = 800  # lines kept in memory per pipeline

logger = logging.getLogger("app.process_runner")


# ---------- session-state helpers ----------

def _ss_key(name: str, field: str) -> str:
    return f"_proc_{name}_{field}"


def _get_lines(name: str) -> deque:
    key = _ss_key(name, "lines")
    if key not in st.session_state:
        st.session_state[key] = deque(maxlen=LOG_TAIL_MAX)
    return st.session_state[key]


def _set_proc(name: str, proc: Optional[subprocess.Popen]) -> None:
    st.session_state[_ss_key(name, "proc")] = proc


def _get_proc(name: str) -> Optional[subprocess.Popen]:
    return st.session_state.get(_ss_key(name, "proc"))


def _set_meta(name: str, **kv) -> None:
    cur = st.session_state.get(_ss_key(name, "meta"), {})
    cur.update(kv)
    st.session_state[_ss_key(name, "meta")] = cur


def _get_meta(name: str) -> dict:
    return st.session_state.get(_ss_key(name, "meta"), {})


# ---------- public API ----------

def is_running(name: str) -> bool:
    proc = _get_proc(name)
    return proc is not None and proc.poll() is None


def exit_code(name: str) -> Optional[int]:
    proc = _get_proc(name)
    if proc is None:
        return None
    return proc.poll()


def start_pipeline(name: str, cmd: list[str], *, cwd: Optional[Path] = None) -> None:
    if is_running(name):
        logger.warning("pipeline %s already running — ignoring start", name)
        return
    lines = _get_lines(name)
    lines.clear()
    lines.append(f"$ {' '.join(cmd)}")
    lines.append(f"# started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")

    creationflags = 0
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP so we can send CTRL_BREAK_EVENT for graceful stop.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=creationflags,
    )
    _set_proc(name, proc)
    _set_meta(name, started_at=datetime.now().isoformat(timespec="seconds"), cmd=cmd)

    # Thread reads stdout into the deque. The deque is on st.session_state but
    # we don't touch the rest of session state from here, which is safe.
    def _pump():
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.append(line.rstrip("\n"))
        except Exception as err:
            lines.append(f"[pump error] {err}")
        finally:
            rc = proc.wait()
            lines.append("")
            lines.append(f"# exit code {rc}  ·  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    threading.Thread(target=_pump, daemon=True, name=f"pump-{name}").start()


def start_skill_console(name: str, skill_cmd: str, *, model: str = "claude-opus-4-8") -> None:
    """Launch the ``/schedule-autoheal`` skill in a **visible, detached console**.

    Unlike :func:`start_pipeline` (which PIPEs the child into an in-app panel),
    this spawns ``app/autoheal_console.py`` with ``CREATE_NEW_CONSOLE`` so the
    agent's live ``--verbose`` stream appears in its own OS window — the user
    watches it directly. We can't also PIPE a new-console child, so the wrapper
    tees its output to a log file and a background thread tails that file into
    the same deque ``render_log_panel`` reads, mirroring the window in-app.

    The ``Popen`` handle is kept (detached ≠ untracked) so ``stop_pipeline`` and
    the status badges keep working.
    """
    if is_running(name):
        logger.warning("skill console %s already running — ignoring start", name)
        return

    ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    log_path = REPO_ROOT / "results" / "planning" / f"autoheal-{ts}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Create the file now, before the tail thread starts. The wrapper opens it
    # with "wb" only after its own (new-console) Python startup, so if that child
    # dies early the file would never exist and the tail's open() would raise
    # FileNotFoundError — masking the child's real failure. touch() closes that race.
    log_path.touch()

    wrapper = REPO_ROOT / "app" / "autoheal_console.py"
    cmd = [
        str(VENV_PY), str(wrapper),
        "--skill-cmd", skill_cmd,
        "--log", str(log_path),
        "--model", model,
    ]

    lines = _get_lines(name)
    lines.clear()
    lines.append(f"$ {skill_cmd}")
    lines.append(f"# visible console launched {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"# live output streams in the new window; mirrored here from {log_path.name}")
    lines.append("")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")

    creationflags = 0
    if sys.platform == "win32":
        # CREATE_NEW_CONSOLE → a real, visible window that outlives this app.
        creationflags = subprocess.CREATE_NEW_CONSOLE

    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        creationflags=creationflags,
    )
    _set_proc(name, proc)
    _set_meta(name, started_at=datetime.now().isoformat(timespec="seconds"),
              cmd=cmd, log_path=str(log_path))

    def _tail():
        # Wait for the wrapper to create the log, then stream new lines into the
        # deque until the process exits and the file is fully drained.
        while not log_path.exists() and proc.poll() is None:
            time.sleep(0.2)
        if not log_path.exists():
            # Child exited before producing a log (and the pre-touch is gone).
            # Surface its exit code instead of crashing on a missing-file open().
            rc = proc.poll()
            lines.append(f"[autoheal] console exited (code {rc}) before writing any log output.")
            return
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as fh:
                while True:
                    line = fh.readline()
                    if line:
                        lines.append(line.rstrip("\n"))
                        continue
                    if proc.poll() is not None:
                        for rest in fh.read().splitlines():
                            lines.append(rest)
                        break
                    time.sleep(0.3)
        except Exception as err:  # noqa: BLE001 — tail must never crash the app
            lines.append(f"[tail error] {err}")

    threading.Thread(target=_tail, daemon=True, name=f"tail-{name}").start()


def stop_pipeline(name: str) -> None:
    proc = _get_proc(name)
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.terminate()
    except Exception as err:
        logger.warning("stop %s: %s", name, err)


def clear_log(name: str) -> None:
    _get_lines(name).clear()


# ---------- rendering ----------

def render_status_badge(name: str) -> None:
    if is_running(name):
        st.info(f"⏳ running  ·  started {_get_meta(name).get('started_at', '')}")
        return
    rc = exit_code(name)
    if rc is None:
        st.caption("idle  ·  no run yet this session")
    elif rc == 0:
        st.success("✅ last run finished cleanly")
    else:
        st.error(f"❌ last run failed (exit {rc})")


def render_log_panel(name: str, *, height: int = 380, autorefresh_secs: float = 1.0) -> None:
    """Render the rolling log tail. While the subprocess is alive, sleep + rerun
    once so the user sees a live stream without clicking refresh.

    Uses a scrollable container + st.code (display element, not a widget) so
    the body actually updates on every rerun. A keyed st.text_area caches its
    first-render value in session_state and ignores subsequent `value=` args,
    which left the panel stuck on "(no output yet)" while the deque filled up.
    """
    lines = list(_get_lines(name))
    body = "\n".join(lines) if lines else "(no output yet)"
    with st.container(height=height, border=True, autoscroll=True):
        st.code(body, language=None, wrap_lines=False)
    with st.container(horizontal=True, gap="small"):
        st.button("🛑 stop", key=f"_proc_{name}_stop", on_click=stop_pipeline, args=(name,), disabled=not is_running(name))
        st.button("🧹 clear", key=f"_proc_{name}_clear", on_click=clear_log, args=(name,), disabled=is_running(name))
    if is_running(name):
        time.sleep(autorefresh_secs)
        st.rerun()


def render_combined_status_badge(names: list[str]) -> None:
    """One badge for a group of pipelines that share a UI section. Shows which
    pipeline is currently running (if any) and the worst exit code from the
    last completed runs (a single nonzero exit wins)."""
    running = [n for n in names if is_running(n)]
    if running:
        meta = _get_meta(running[0])
        st.info(f"⏳ {running[0]} running  ·  started {meta.get('started_at', '')}")
        return
    codes = [exit_code(n) for n in names]
    if all(c is None for c in codes):
        st.caption("idle  ·  no run yet this session")
    elif any(c is not None and c != 0 for c in codes):
        failed = [n for n, c in zip(names, codes) if c not in (None, 0)]
        st.error(f"❌ last run failed: {', '.join(failed)}")
    else:
        st.success("✅ last run finished cleanly")


def render_combined_log_panel(
    names: list[str],
    *,
    height: int = 480,
    autorefresh_secs: float = 1.0,
) -> None:
    """One scrollable panel that concatenates the logs of multiple pipelines.

    Each pipeline's lines are already framed by `$ ...` / `# started ...` /
    `# exit code ...` markers from `start_pipeline`, so concatenation alone is
    enough — no extra section headers needed. A blank line is inserted between
    deques only when both have content, to keep the boundary visible.

    Stop/clear act on whichever pipeline is currently running (only one at a
    time in this group), or clear all deques.
    """
    parts: list[str] = []
    for n in names:
        block = "\n".join(_get_lines(n))
        if block:
            parts.append(block)
    body = "\n\n".join(parts) if parts else "(no output yet)"
    with st.container(height=height, border=True, autoscroll=True):
        st.code(body, language=None, wrap_lines=False)

    running_now = next((n for n in names if is_running(n)), None)
    any_running = running_now is not None

    def _clear_all() -> None:
        for n in names:
            clear_log(n)

    with st.container(horizontal=True, gap="small"):
        st.button(
            "🛑 stop", key=f"_proc_combined_{'+'.join(names)}_stop",
            on_click=stop_pipeline, args=(running_now,) if running_now else (names[0],),
            disabled=not any_running,
        )
        st.button(
            "🧹 clear", key=f"_proc_combined_{'+'.join(names)}_clear",
            on_click=_clear_all, disabled=any_running,
        )
    if any_running:
        time.sleep(autorefresh_secs)
        st.rerun()
