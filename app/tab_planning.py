"""Planning pipeline tab — drives LI/IG/TW/TH/videos schedulers.

Two run paths:
- **plain run** — the scheduler pipeline streamed into the in-app log panel.
- **run + autoheal** — launches the ``/schedule-autoheal`` skill in a visible
  console window (see ``app/autoheal_console.py``); on a UI-drift failure the
  agent self-heals end-to-end. The tab mirrors the window's output in-app and
  shows the last run's machine-readable outcome.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
    start_skill_console,
)

PIPELINE_NAME = "planning"
AUTOHEAL_NAME = "planning-autoheal"
_ACTIVE_KEY = "planning-active-runner"
RESULT_PATH = Path(__file__).resolve().parent.parent / "results" / "planning" / "latest-result.json"


def _launch_plain(cmd: list[str]) -> None:
    st.session_state[_ACTIVE_KEY] = PIPELINE_NAME
    start_pipeline(PIPELINE_NAME, cmd)


def _launch_autoheal(skill_cmd: str) -> None:
    st.session_state[_ACTIVE_KEY] = AUTOHEAL_NAME
    start_skill_console(AUTOHEAL_NAME, skill_cmd)

_KIND_BADGE = {
    "ui-drift": "🔧 UI-drift (heal-eligible)",
    "login-required": "🔑 login required (human)",
    "data-error": "📝 data error (human)",
    "other": "❓ unclassified (human)",
}


def _render_last_outcome() -> None:
    """Show the last run's machine-readable result + per-platform failures."""
    if not RESULT_PATH.exists():
        return
    try:
        result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    st.divider()
    verdict = result.get("verdict", "?")
    header = f"last run · {result.get('mode', '?')} · {result.get('finished_at', '')}"
    if verdict == "clean":
        st.success(f"✅ {header} — clean, nothing failed.")
        return

    st.warning(f"⚠️ {header} — failures present.")
    for plat in result.get("platforms", []):
        fails = [r for r in plat.get("rows", []) if r.get("failure_kind", "none") != "none"]
        if not fails:
            continue
        st.markdown(f"**{plat['platform']}**")
        for row in fails:
            kind = _KIND_BADGE.get(row.get("failure_kind", "other"), row.get("failure_kind", ""))
            st.markdown(f"- `{row.get('day', '')}` — {kind} — {row.get('detail', '')}")
            shot = row.get("screenshot", "")
            if shot and Path(shot).exists():
                st.image(shot, caption=shot, width="stretch")


def _skill_command(mode: str, debug: bool, skips: dict[str, bool]) -> str:
    parts = ["/schedule-autoheal", "all", f"--{mode}"]
    if debug:
        parts.append("--debug")
    parts.extend(f"--skip-{plat}" for plat, val in skips.items() if val)
    return " ".join(parts)


def run() -> None:
    st.subheader("📅 planning — weekly content scheduler")
    st.caption(
        "Walks every WIP-* row in the Notion editorial DB across LinkedIn, Instagram (+Meta planner story+post), "
        "Twitter, Threads, and the weekly video clip. Each platform drives its own real-Chrome session."
    )

    cols = st.columns([2, 2, 2, 4])
    with cols[0]:
        mode = st.radio(
            "mode",
            options=["dry-run", "live"],
            horizontal=True,
            key="planning-mode",
            help="Dry-run rehearses without scheduling. Live posts into each platform's native scheduler.",
        )
    with cols[1]:
        debug = st.toggle("debug", value=False, key="planning-debug")
    with cols[2]:
        st.caption(" ")  # spacer

    skip_cols = st.columns(5)
    skips = {}
    for i, plat in enumerate(("linkedin", "instagram", "twitter", "threads", "videos")):
        with skip_cols[i]:
            skips[plat] = st.toggle(f"skip {plat[:2].upper()}", value=False, key=f"planning-skip-{plat}")

    cmd = [str(VENV_PY), "planning_pipeline.py", f"--{mode}"]
    if debug:
        cmd.append("--debug")
    for plat, val in skips.items():
        if val:
            cmd.append(f"--skip-{plat}")

    if mode == "live":
        st.warning("⚠️ LIVE mode will write real schedules into LI/IG/TW/TH. Make sure your WIP-* checkboxes are correct.")

    run_cols = st.columns([3, 4])
    with run_cols[0]:
        st.button(
            "▶ run planning pipeline",
            key="planning-run",
            type="primary",
            disabled=is_running(PIPELINE_NAME) or is_running(AUTOHEAL_NAME),
            on_click=_launch_plain,
            args=(cmd,),
        )
    with run_cols[1]:
        st.button(
            "🔧 run + autoheal (console)",
            key="planning-autoheal-run",
            disabled=is_running(PIPELINE_NAME) or is_running(AUTOHEAL_NAME),
            on_click=_launch_autoheal,
            args=(_skill_command(mode, debug, skips),),
            help="Opens a visible console running /schedule-autoheal. On a UI-drift failure the agent "
                 "self-heals end-to-end (issue → fix → dry-run → PR → merge) or pings you on Slack.",
        )

    # Show the panel for the runner the user last launched (set in the button
    # callbacks). A running runner always wins, so the live stream is never
    # hidden; once idle the panel stays put instead of clearing on completion.
    if is_running(AUTOHEAL_NAME):
        active = AUTOHEAL_NAME
    elif is_running(PIPELINE_NAME):
        active = PIPELINE_NAME
    else:
        active = st.session_state.get(_ACTIVE_KEY, PIPELINE_NAME)
    render_status_badge(active)
    render_log_panel(active)

    _render_last_outcome()
