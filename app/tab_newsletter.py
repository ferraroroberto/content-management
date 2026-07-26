"""Newsletter pipeline tab — bootstrap → archive → normalize → build HTML,
plus the optional Substack draft step.

Each step is an independent, non-interactive subcommand of
``newsletter_pipeline.py`` (issue #59). All buttons share the single
``"newsletter"`` process slot, so the status badge / log panel / sidebar
status work unchanged. The must-read picker reads the topics sidecar that
``build`` writes, so it never blocks on stdin.

⑤ Substack draft (issue #184) is deliberately outside the ▶ combo — it writes
to an external platform, so it stays an explicit, separately-clicked action. It
creates a **private** draft and never publishes.
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
        "③ Normalize titles + URLs → ④ Build HTML. Run any step alone, or ▶ for ②③④. "
        "⑤ pushes the same lists into a private Substack draft."
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
    # st.container(horizontal=True) rather than st.columns() — buttons with
    # on_click nested inside st.columns() under st.tabs() silently fail to
    # fire and reset the active tab on Streamlit's uvicorn/Starlette server
    # (issue #155). container(horizontal=True) doesn't have this problem.
    with st.container(horizontal=True, gap="small"):
        st.button(
            "② Archive → Notion",
            key="newsletter-archive",
            disabled=running,
            on_click=start_pipeline,
            args=(PIPELINE_NAME, base + ["archive"] + dbg),
            width="stretch",
        )
        st.button(
            "③ Normalize titles+URLs",
            key="newsletter-normalize",
            disabled=running,
            on_click=start_pipeline,
            args=(PIPELINE_NAME, base + ["normalize", "--days", str(int(days))] + dbg),
            width="stretch",
        )
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

    must_read = _render_must_read_picker(num)
    _render_substack_draft(num, has_num, running, base, dbg, must_read)


def _render_substack_draft(
    newsletter_number: str,
    has_num: bool,
    running: bool,
    base: list[str],
    dbg: list[str],
    must_read: int | None,
) -> None:
    """⑤ Create a private Substack draft edition from the built lists.

    Sits below the must-read picker so the chosen line can be folded into the
    draft as its opening paragraph. Never publishes — there is no ``--confirm``
    on this path at all (see ``newsletter/substack_draft.py``).
    """
    st.divider()
    st.markdown("**⑤ Substack draft**")

    args = base + ["substack-draft", "--newsletter", newsletter_number]
    if must_read is not None:
        args += ["--must-read", str(must_read)]
    args += dbg

    st.button(
        "⑤ Create Substack draft",
        key="newsletter-substack-draft",
        disabled=running or not has_num,
        on_click=start_pipeline,
        args=(PIPELINE_NAME, args),
        help="Build a private Substack draft edition from this newsletter's "
             "articles. Nothing is sent to subscribers — you review and publish "
             "from the Substack editor.",
    )

    if not has_num:
        st.caption("ℹ️ enter a newsletter number to enable ⑤.")
    elif must_read is not None:
        st.caption(f"→ creates a **private** draft, opening with must-read #{must_read}. Never publishes.")
    else:
        st.caption("→ creates a **private** draft (no must-read line). Never publishes.")


def _render_must_read_picker(newsletter_number: str) -> int | None:
    """Compose the must-read line from the topics sidecar a build wrote.

    Reads ``results/newsletter/N{NNN}.topics.json`` and lets the user pick which
    of the three top articles is the "must read"; the composed line is shown in
    a copyable ``st.code`` block. No subprocess, no clipboard — pure UI.

    Returns the chosen 1-based index so ⑤ can pass it to the draft step, or
    ``None`` when no sidecar exists / the line is unavailable.
    """
    if not newsletter_number:
        return None
    # Imported here (not at module load) to keep app startup light.
    from newsletter.build_newsletter import format_must_read_line, topics_sidecar_path

    try:
        path = topics_sidecar_path(newsletter_number)
    except ValueError:
        return None  # not a valid number yet (e.g. "59") — nothing to show
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st.warning("⚠️ couldn't read the topics sidecar.")
        return None

    st.divider()
    st.markdown("**must-read line**")

    top_names = data.get("top_names")
    if not top_names:
        st.warning("⚠️ must-read unavailable — a topic has no articles in this issue.")
        return None

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
    return int(choice)
