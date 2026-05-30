"""Newsletter pipeline tab — archive open tabs → normalize → build HTML."""

from __future__ import annotations

import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
)

PIPELINE_NAME = "newsletter"


def run() -> None:
    st.subheader("📰 newsletter — weekly archive + build")
    st.caption(
        "Step 1: archive open Chrome tabs into Notion (readability extract + Gemini topic/summary + author resolve). "
        "Step 2: normalize titles + URLs. Step 3: render the issue HTML."
    )

    cols = st.columns([2, 2, 2, 4])
    with cols[0]:
        newsletter_number = st.text_input(
            "newsletter number",
            value="",
            key="newsletter-number",
            help="e.g. 057 — required for step 3. Leave blank to be prompted on stdin (then the run will hang).",
        )
    with cols[1]:
        days = st.number_input(
            "normalize lookback (days)",
            min_value=1, max_value=90, value=14, step=1,
            key="newsletter-days",
        )
    with cols[2]:
        skip_bootstrap = st.toggle(
            "skip Chrome bootstrap",
            value=True,
            key="newsletter-skip-bootstrap",
            help="On (default): reuse the Chrome already up on :9222 — bring it up "
                 "yourself with `newsletter\\bootstrap_chrome.bat` in a console first. "
                 "Off: the pipeline kills every chrome.exe and relaunches the "
                 "dedicated profile.",
        )

    debug = st.toggle("debug", value=False, key="newsletter-debug")

    if skip_bootstrap:
        st.caption(
            "ℹ️ Chrome must already be up on :9222 — run "
            "`newsletter\\bootstrap_chrome.bat` in a console first."
        )

    cmd = [str(VENV_PY), "newsletter_pipeline.py", "--days", str(int(days))]
    if newsletter_number.strip():
        cmd.extend(["--newsletter", newsletter_number.strip()])
    cmd.append("--skip-bootstrap" if skip_bootstrap else "--no-skip-bootstrap")
    if debug:
        cmd.append("--debug")

    if not newsletter_number.strip():
        st.warning("⚠️ no newsletter number set — the pipeline will block on stdin prompt and never finish from here.")

    st.button(
        "▶ run newsletter pipeline",
        key="newsletter-run",
        type="primary",
        disabled=is_running(PIPELINE_NAME) or not newsletter_number.strip(),
        on_click=start_pipeline,
        args=(PIPELINE_NAME, cmd),
    )

    render_status_badge(PIPELINE_NAME)
    render_log_panel(PIPELINE_NAME)
