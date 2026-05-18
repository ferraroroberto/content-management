"""Reporting pipeline tab — daily metrics → Supabase → Notion → Substack."""

from __future__ import annotations

from datetime import date

import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
)

PIPELINE_NAME = "reporting"


def run() -> None:
    st.subheader("📊 reporting — daily numbers")
    st.caption("Pulls today's metrics from social APIs → Supabase → Notion → Substack Note + followers.")

    cols = st.columns([2, 2, 2, 4])
    with cols[0]:
        run_date = st.date_input(
            "date",
            value=date.today(),
            key="reporting-date",
            help="Reference date (YYYYMMDD). The pipeline processes the day BEFORE this.",
        )
    with cols[1]:
        skip_substack = st.toggle(
            "skip substack",
            value=False,
            key="reporting-skip-substack",
            help="Skip the Substack Note publish + follower scrape (the only Playwright step).",
        )
    with cols[2]:
        debug = st.toggle("debug", value=False, key="reporting-debug")

    cmd = [str(VENV_PY), "reporting_pipeline.py", "--date", run_date.strftime("%Y%m%d"), "--yes"]
    if skip_substack:
        cmd.append("--skip-substack")
    if debug:
        cmd.append("--debug")

    run_disabled = is_running(PIPELINE_NAME)
    st.button(
        "▶ run reporting pipeline",
        key="reporting-run",
        type="primary",
        disabled=run_disabled,
        on_click=start_pipeline,
        args=(PIPELINE_NAME, cmd),
    )

    render_status_badge(PIPELINE_NAME)
    render_log_panel(PIPELINE_NAME)
