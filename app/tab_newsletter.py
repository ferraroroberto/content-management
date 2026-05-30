"""Newsletter pipeline tab — bootstrap → archive → normalize → build HTML.

Each step is an independent, non-interactive subcommand of
``newsletter_pipeline.py`` (issue #59). All buttons share the single
``"newsletter"`` process slot, so the status badge / log panel / sidebar
status work unchanged. The must-read picker reads the topics sidecar that
``build`` writes, so it never blocks on stdin.
"""

from __future__ import annotations

import json

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
        "① Bootstrap Chrome → open your article tabs → ② Archive into Notion → "
        "③ Normalize titles + URLs → ④ Build HTML. Run any step alone, or ▶ for ②③④."
    )

    cols = st.columns([2, 2, 2])
    with cols[0]:
        newsletter_number = st.text_input(
            "newsletter number",
            value="",
            key="newsletter-number",
            help="e.g. 057 — required for ④ Build and ▶ Run.",
        )
    with cols[1]:
        days = st.number_input(
            "normalize lookback (days)",
            min_value=1, max_value=90, value=14, step=1,
            key="newsletter-days",
        )
    with cols[2]:
        debug = st.toggle("debug", value=False, key="newsletter-debug")

    num = newsletter_number.strip()
    has_num = bool(num)
    running = is_running(PIPELINE_NAME)

    base = [str(VENV_PY), "newsletter_pipeline.py"]
    dbg = ["--debug"] if debug else []

    # ── ① bootstrap ──────────────────────────────────────────────────
    st.button(
        "① Bootstrap Chrome",
        key="newsletter-bootstrap",
        disabled=running,
        on_click=start_pipeline,
        args=(PIPELINE_NAME, base + ["bootstrap"]),
        help="Launch the dedicated newsletter Chrome on :9222 without touching "
             "your everyday browser. Then open your article tabs in that window.",
    )
    st.caption("→ after bootstrap, open your article tabs in that Chrome window, then run ② (or ▶).")

    # ── ②③④ step buttons ─────────────────────────────────────────────
    c2, c3, c4 = st.columns(3)
    with c2:
        st.button(
            "② Archive → Notion",
            key="newsletter-archive",
            disabled=running,
            on_click=start_pipeline,
            args=(PIPELINE_NAME, base + ["archive"] + dbg),
            width="stretch",
        )
    with c3:
        st.button(
            "③ Normalize titles+URLs",
            key="newsletter-normalize",
            disabled=running,
            on_click=start_pipeline,
            args=(PIPELINE_NAME, base + ["normalize", "--days", str(int(days))] + dbg),
            width="stretch",
        )
    with c4:
        st.button(
            "④ Build HTML",
            key="newsletter-build",
            disabled=running or not has_num,
            on_click=start_pipeline,
            args=(PIPELINE_NAME, base + ["build", "--newsletter", num, "--no-must-read"] + dbg),
            width="stretch",
        )

    # ── ▶ combo ──────────────────────────────────────────────────────
    st.button(
        "▶ Run ②③④ (create newsletter)",
        key="newsletter-create",
        type="primary",
        disabled=running or not has_num,
        on_click=start_pipeline,
        args=(PIPELINE_NAME, base + ["create", "--newsletter", num, "--days", str(int(days))] + dbg),
    )

    if not has_num:
        st.caption("ℹ️ enter a newsletter number to enable ④ Build and ▶ Run.")

    render_status_badge(PIPELINE_NAME)
    render_log_panel(PIPELINE_NAME)

    _render_must_read_picker(num)


def _render_must_read_picker(newsletter_number: str) -> None:
    """Compose the must-read line from the topics sidecar a build wrote.

    Reads ``results/newsletter/N{NNN}.topics.json`` and lets the user pick which
    of the three top articles is the "must read"; the composed line is shown in
    a copyable ``st.code`` block. No subprocess, no clipboard — pure UI.
    """
    if not newsletter_number:
        return
    # Imported here (not at module load) to keep app startup light.
    from newsletter.build_newsletter import format_must_read_line, topics_sidecar_path

    try:
        path = topics_sidecar_path(newsletter_number)
    except ValueError:
        return  # not a valid number yet (e.g. "59") — nothing to show
    if not path.exists():
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st.warning("⚠️ couldn't read the topics sidecar.")
        return

    st.divider()
    st.markdown("**must-read line**")

    top_names = data.get("top_names")
    if not top_names:
        st.warning("⚠️ must-read unavailable — a topic has no articles in this issue.")
        return

    headings = data.get("headings") or []
    labels = [
        f"{i + 1}. {(headings[i] if i < len(headings) else f'topic {i + 1}')} — {name}"
        for i, name in enumerate(top_names)
    ]
    options = list(range(1, len(top_names) + 1))
    choice = st.radio(
        "which is the must-read?",
        options=options,
        format_func=lambda n: labels[n - 1],
        key=f"newsletter-mustread-{data.get('newsletter')}",
    )
    line = format_must_read_line(top_names, int(choice))
    st.code(line, language=None)
    st.caption("copy the line above ☝️")
