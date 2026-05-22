"""Engagement tab — scrape / classify run controls + the existing review UI."""

from __future__ import annotations

import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
)
from engagement.ui import (
    invalidate_caches,
    render_ai_tab,
    render_commenters_tab,
    render_real_tab,
    render_sidebar_filters,
    render_sidebar_metrics,
)

SCRAPE_NAME = "engagement-scrape"
CLASSIFY_NAME = "engagement-classify"


def _render_run_subtab() -> None:
    st.subheader("🛡️ engagement — scrape + classify")
    st.caption(
        "Scrape comments on your own LinkedIn posts (last N days), then classify each via "
        "whitelist/blacklist/rules. Both steps are idempotent — safe to re-run."
    )

    cols = st.columns([2, 2, 2, 4])
    with cols[0]:
        days = st.number_input(
            "scrape days", min_value=1, max_value=30, value=3, step=1,
            key="engagement-days",
        )
    with cols[1]:
        headless = st.toggle("headless", value=False, key="engagement-headless",
                             help="Run Chrome headless. Disable for first runs to watch selector iteration.")
    with cols[2]:
        dry_run = st.toggle("dry-run", value=False, key="engagement-dry-run",
                            help="Write JSON dump under results/engagement/, skip Supabase upsert.")

    scrape_cmd = [str(VENV_PY), "-m", "engagement.linkedin.scrape_comments", "--days", str(int(days))]
    if headless:
        scrape_cmd.append("--headless")
    if dry_run:
        scrape_cmd.append("--dry-run")

    classify_cmd = [str(VENV_PY), "-m", "engagement.classify.rules"]

    btn_cols = st.columns(3)
    with btn_cols[0]:
        st.button(
            "▶ scrape LinkedIn", key="engagement-scrape-btn", type="primary",
            disabled=is_running(SCRAPE_NAME),
            on_click=start_pipeline, args=(SCRAPE_NAME, scrape_cmd),
        )
    with btn_cols[1]:
        st.button(
            "🧮 classify pending", key="engagement-classify-btn",
            disabled=is_running(CLASSIFY_NAME),
            on_click=start_pipeline, args=(CLASSIFY_NAME, classify_cmd),
        )
    with btn_cols[2]:
        st.button(
            "🔄 refresh review data", key="engagement-refresh-data",
            on_click=invalidate_caches,
        )

    st.markdown("**scrape**")
    render_status_badge(SCRAPE_NAME)
    render_log_panel(SCRAPE_NAME, height=240)

    st.markdown("**classify**")
    render_status_badge(CLASSIFY_NAME)
    render_log_panel(CLASSIFY_NAME, height=160)


def run() -> None:
    # Engagement-specific filters at the top so the user has them visible
    # without the sidebar getting cluttered for the other pipeline tabs.
    with st.expander("🛡️ engagement filters + counts", expanded=False):
        col_metrics, col_filters = st.columns([3, 2])
        with col_metrics:
            render_sidebar_metrics(horizontal=True)
        with col_filters:
            platform, search = render_sidebar_filters(key_suffix="-engagement-tab")

    sub_run, sub_real, sub_ai, sub_people = st.tabs([
        "🚀 scrape + classify",
        "🧑 real comments",
        "🤖 AI triage",
        "📊 commenters",
    ])
    with sub_run:
        _render_run_subtab()
    with sub_real:
        render_real_tab(platform, search)
    with sub_ai:
        render_ai_tab(platform, search)
    with sub_people:
        render_commenters_tab(platform, search)
