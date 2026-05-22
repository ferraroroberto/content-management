"""Planning pipeline tab — drives LI/IG/TW/TH/videos schedulers."""

from __future__ import annotations

import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
)

PIPELINE_NAME = "planning"


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

    st.button(
        "▶ run planning pipeline",
        key="planning-run",
        type="primary",
        disabled=is_running(PIPELINE_NAME),
        on_click=start_pipeline,
        args=(PIPELINE_NAME, cmd),
    )

    render_status_badge(PIPELINE_NAME)
    render_log_panel(PIPELINE_NAME)
