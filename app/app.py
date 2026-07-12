r"""Unified control-panel Streamlit app.

Sections: 📊 Reporting · 📅 Editorial · 📅 Planning · 📰 Newsletter · 🛡️ Engagement.
Routed via st.segmented_control rather than st.tabs() (issue #157 — st.tabs()
loses the active tab on any widget rerun). Each section owns its own module
(`app.tab_*`) per the project's per-tab convention (see pdf-to-markdown
sibling project + CLAUDE.md). Subprocess lifecycle + live log streaming lives
in `app/process_runner.py`.

Launch via the wrapper (recommended — applies logging filters before server start):
    .\launch_app.bat
    # or: & .\.venv\Scripts\python.exe run_app.py
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

# Belt-and-suspenders filter for direct launches (run_app.py applies these earlier).
logging.getLogger("tornado.general").addFilter(
    type("_NoInvalidHTTP", (logging.Filter,), {
        "filter": staticmethod(lambda r: "Invalid HTTP request" not in r.getMessage())
    })()
)

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
    ("editorial",          "📅 editorial"),
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


# ── Section routing ────────────────────────────────────────────────
# st.segmented_control rather than st.tabs() — st.tabs() does not preserve
# the active tab across a script rerun triggered by a widget on a
# non-default tab (it silently snaps back to the first tab and the
# triggering widget's new value never reaches the script). Confirmed
# against upstream Streamlit 1.59.1 too, so it isn't fixable by upgrading
# (issue #157; streamlit/streamlit#11160, #12554). segmented_control is a
# real widget — its selection is ordinary widget state, so it survives any
# rerun the way st.tabs()'s internal state does not.
SECTIONS = ["📊 reporting", "📅 editorial", "📅 planning", "📰 newsletter", "🛡️ engagement"]

section = st.segmented_control(
    "section",
    options=SECTIONS,
    default=SECTIONS[0],
    key="app-section",
    label_visibility="collapsed",
)
# On the very first script run of a fresh session, segmented_control can
# return None for one rerun before its frontend component echoes back the
# default (the page briefly renders with nothing selected below the nav).
# Falling back to the default here avoids a blank-body flash / e2e race.
section = section or SECTIONS[0]

if section == "📊 reporting":
    from app import tab_reporting  # noqa: PLC0415
    tab_reporting.run()
elif section == "📅 editorial":
    from app import tab_editorial  # noqa: PLC0415
    tab_editorial.run()
elif section == "📅 planning":
    from app import tab_planning  # noqa: PLC0415
    tab_planning.run()
elif section == "📰 newsletter":
    from app import tab_newsletter  # noqa: PLC0415
    tab_newsletter.run()
elif section == "🛡️ engagement":
    from app import tab_engagement  # noqa: PLC0415
    tab_engagement.run()
