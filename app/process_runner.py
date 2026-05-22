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
    cols = st.columns([1, 1, 6])
    with cols[0]:
        st.button("🛑 stop", key=f"_proc_{name}_stop", on_click=stop_pipeline, args=(name,), disabled=not is_running(name))
    with cols[1]:
        st.button("🧹 clear", key=f"_proc_{name}_clear", on_click=clear_log, args=(name,), disabled=is_running(name))
    if is_running(name):
        time.sleep(autorefresh_secs)
        st.rerun()
