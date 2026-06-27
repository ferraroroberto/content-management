"""Editorial tab — seed the Notion editorial-calendar day-rows.

Runs ``reporting.notion.add_editorial_dates``, which creates any missing
editorial rows for the current + next calendar month (idempotent). One run
button + the shared streamed-log panel, same as the reporting/planning tabs.
No date selector — the default current+next-month range is what runs.
"""

from __future__ import annotations

import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
)

PIPELINE_NAME = "editorial"


def run() -> None:
    st.subheader("📅 editorial — seed calendar rows")
    st.caption(
        "Adds any missing day-rows to the Notion editorial DB for the current + next "
        "calendar month (day · date · DoW). Idempotent — re-running only fills gaps."
    )

    debug = st.toggle("debug", value=False, key="editorial-debug")

    cmd = [str(VENV_PY), "-m", "reporting.notion.add_editorial_dates"]
    if debug:
        cmd.append("--debug")

    st.button(
        "▶ seed editorial rows",
        key="editorial-run",
        type="primary",
        disabled=is_running(PIPELINE_NAME),
        on_click=start_pipeline,
        args=(PIPELINE_NAME, cmd),
    )

    render_status_badge(PIPELINE_NAME)
    render_log_panel(PIPELINE_NAME)
