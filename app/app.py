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

# ── Theme toggle state ─────────────────────────────────────────────
# Streamlit has no public API to change theme.* options at runtime. This is
# the documented workaround (discuss.streamlit.io/t/56842): push new
# theme.* values through the private st._config.set_option, then force a
# second rerun so the frontend repaints with them before anything else
# does. THEMES["dark"] mirrors .streamlit/config.toml so the two never
# drift apart.
THEMES = {
    "dark": {
        "theme.base": "dark",
        "theme.primaryColor": "#1E88E5",
        "theme.backgroundColor": "#0E1117",
        "theme.secondaryBackgroundColor": "#262730",
        "theme.textColor": "#FAFAFA",
        "icon": "🌙",
    },
    "light": {
        "theme.base": "light",
        "theme.primaryColor": "#1E88E5",
        "theme.backgroundColor": "#FFFFFF",
        "theme.secondaryBackgroundColor": "#F0F2F6",
        "theme.textColor": "#31333F",
        "icon": "☀️",
    },
}

if "theme_name" not in st.session_state:
    st.session_state.theme_name = "dark"
    st.session_state.theme_refreshed = True


def _toggle_theme() -> None:
    next_name = "light" if st.session_state.theme_name == "dark" else "dark"
    for option, value in THEMES[next_name].items():
        if option.startswith("theme."):
            st._config.set_option(option, value)
    st.session_state.theme_name = next_name
    st.session_state.theme_refreshed = False


st.set_page_config(
    layout="wide",
    page_title="Roberto · automation control panel",
    page_icon="🎛️",
)

_current_bg = THEMES[st.session_state.theme_name]["theme.backgroundColor"]

# Hide the deploy button, tighten metric labels, reclaim the default top
# gap, and pin the section nav so scrolled content can't bleed over it.
st.markdown(
    f"""
<style>
    .stAppDeployButton {{ display: none; }}
    [data-testid="stMetricLabel"] {{ font-size: 0.75rem !important; }}

    .block-container {{ padding-top: 1.5rem !important; padding-bottom: 2rem !important; }}
    [data-testid="stSidebarContent"] {{ padding-top: 1.5rem !important; }}

    /* Streamlit wraps every element in its own stLayoutWrapper div sized to
       that element's own height. Sticking .st-key-nav-bar itself gives it
       no room to move (its wrapper is exactly its own height) — instead
       stick the wrapper, whose containing block is the page's full-height
       vertical block. top matches stHeader's height (measured: 60px) so
       the bar parks just below Streamlit's own fixed toolbar instead of
       sliding underneath it (that toolbar's z-index is ~999990). */
    div[data-testid="stLayoutWrapper"]:has(> .st-key-nav-bar) {{
        position: sticky;
        top: 60px;
        z-index: 999;
        background-color: {_current_bg};
    }}
    .st-key-nav-bar {{
        padding: 0.5rem 0 0.75rem 0;
        margin-bottom: 0.5rem;
        border-bottom: 1px solid rgba(128, 128, 128, 0.2);
    }}
    /* stColumn is flex-direction: column, so justify-content there governs
       vertical stacking, not horizontal position. The actual horizontal
       placement comes from align-items (cross-axis) on the stVerticalBlock
       that stColumn stretches to 100% width — flip that to flex-end so the
       fit-content button hugs the column's right edge. */
    .st-key-nav-bar div[data-testid="stColumn"]:last-child > div[data-testid="stVerticalBlock"] {{
        align-items: flex-end;
    }}
    .st-key-nav-bar [data-testid="stButton"] button {{
        padding: 0.25rem 0.6rem;
    }}
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

# nav-bar and the routed section content must share one containing block
# (this outer container) for position: sticky to have room to stick —
# nested directly under stVerticalBlock, nav-bar's own wrapper is only as
# tall as the bar itself, so it scrolls away instead of pinning.
with st.container():
    # Sticky container (see .st-key-nav-bar CSS above) — stays pinned under
    # the header on scroll, with the theme toggle inline on the right.
    with st.container(key="nav-bar"):
        nav_col, theme_col = st.columns([10, 1], vertical_alignment="center")
        with nav_col:
            section = st.segmented_control(
                "section",
                options=SECTIONS,
                default=SECTIONS[0],
                key="app-section",
                label_visibility="collapsed",
            )
        with theme_col:
            st.button(
                THEMES[st.session_state.theme_name]["icon"],
                key="theme-toggle",
                on_click=_toggle_theme,
                help="Switch light / dark theme",
                width="content",
            )

    # On the very first script run of a fresh session, segmented_control can
    # return None for one rerun before its frontend component echoes back
    # the default (the page briefly renders with nothing selected below the
    # nav). Falling back to the default here avoids a blank-body flash.
    section = section or SECTIONS[0]

    # Second forced rerun so the frontend picks up the theme.* options
    # pushed by _toggle_theme() above (see THEMES comment) before anything
    # paints.
    if not st.session_state.theme_refreshed:
        st.session_state.theme_refreshed = True
        st.rerun()

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
