r"""Unified control-panel Streamlit app.

Tabs: 📊 Reporting · 📅 Planning · 📰 Newsletter · 🛡️ Engagement.
Each tab owns its own module (`app.tab_*`) per the project's per-tab
convention (see pdf-to-markdown sibling project + CLAUDE.md). Subprocess
lifecycle + live log streaming lives in `app/process_runner.py`.

Launch:
    .\launch_app.bat
    # or directly:
    & .\.venv\Scripts\python.exe -m streamlit run app\app.py
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

st.set_page_config(
    layout="wide",
    page_title="Roberto · automation control panel",
    page_icon="🎛️",
)

# Hide the deploy button + tighten metric labels (same trick as pdf-to-markdown).
st.markdown(
    """
<style>
    .stAppDeployButton { display: none; }
    [data-testid="stMetricLabel"] { font-size: 0.75rem !important; }
</style>
""",
    unsafe_allow_html=True,
)

# ── Sidebar ─────────────────────────────────────────────────────────
from app.process_runner import exit_code, is_running  # noqa: E402

PIPELINES = [
    ("reporting",          "📊 reporting"),
    ("planning",           "📅 planning"),
    ("newsletter",         "📰 newsletter"),
    ("engagement-scrape",  "🛡️ engagement (scrape)"),
    ("engagement-classify","🛡️ engagement (classify)"),
]


def _status_emoji(name: str) -> str:
    if is_running(name):
        return "⏳"
    rc = exit_code(name)
    if rc is None:
        return "·"
    return "✅" if rc == 0 else "❌"


with st.sidebar:
    st.title("🎛️ control panel")
    st.caption(f"updated {datetime.now().strftime('%H:%M:%S')}")
    st.divider()
    st.markdown("**pipeline status**")
    for key, label in PIPELINES:
        st.markdown(f"{_status_emoji(key)}  {label}")
    st.divider()
    st.caption("project root:")
    st.code(str(REPO_ROOT), language=None)


# ── Tabs ─────────────────────────────────────────────────────────────
tab_rep, tab_plan, tab_news, tab_eng = st.tabs([
    "📊 reporting",
    "📅 planning",
    "📰 newsletter",
    "🛡️ engagement",
])

with tab_rep:
    from app import tab_reporting  # noqa: PLC0415
    tab_reporting.run()

with tab_plan:
    from app import tab_planning  # noqa: PLC0415
    tab_planning.run()

with tab_news:
    from app import tab_newsletter  # noqa: PLC0415
    tab_newsletter.run()

with tab_eng:
    from app import tab_engagement  # noqa: PLC0415
    tab_engagement.run()
