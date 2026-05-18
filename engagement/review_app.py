r"""Standalone Streamlit entry for the engagement review UI.

Thin wrapper around `engagement.ui` — keeps the legacy launch command
working while the unified control panel (`app/app.py`) reuses the same
render functions.

Launch:
    & .\.venv\Scripts\python.exe -m streamlit run engagement\review_app.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(REPO_ROOT))

from engagement.ui import (  # noqa: E402
    render_ai_tab,
    render_commenters_tab,
    render_real_tab,
    render_sidebar_filters,
    render_sidebar_metrics,
)

st.set_page_config(layout="wide", page_title="Engagement Triage", page_icon="🛡️")

with st.sidebar:
    st.title("🛡️ engagement")
    render_sidebar_metrics()
    st.divider()
    platform, search = render_sidebar_filters()

st.title("engagement triage")
st.caption(f"updated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

tab_real, tab_ai, tab_people = st.tabs(["🧑 real comments", "🤖 AI triage", "📊 commenters"])
with tab_real:
    render_real_tab(platform, search)
with tab_ai:
    render_ai_tab(platform, search)
with tab_people:
    render_commenters_tab(platform, search)
