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

⓪ Schedule editions (issue #230) sits before ① because ② Archive can only file
an article against a *future* newsletter row: with the buffer drained it aborts
outright. The block reads the buffer state for its caption through a cached
helper so a Streamlit rerun doesn't hit the Notion API on every keystroke.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import streamlit as st

if TYPE_CHECKING:  # annotation only — the real import stays lazy (app startup)
    from newsletter.schedule_editions import BufferState

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
)

PIPELINE_NAME = "newsletter"

#: Tracks the pipeline slot's running state across reruns so the cached buffer
#: read can be dropped the moment a run finishes (see :func:`_sync_buffer_cache`).
_WAS_RUNNING_KEY = "newsletter-schedule-was-running"


def run() -> None:
    st.subheader("📰 newsletter — weekly archive + build")
    st.caption(
        "⓪ Keep future editions stocked in Notion → ① Bootstrap Chrome → open your "
        "article tabs → ② Archive into Notion → ③ Normalize titles + URLs → "
        "④ Build HTML. Run any step alone, or ▶ for ②③④. "
        "⑤ pushes the same lists into a private Substack draft."
    )
    st.info(
        "**Order matters for ⑤:** run ④ Build HTML (or ▶) first — it writes the "
        "topics sidecar the must-read picker below reads. Then pick which article "
        "leads: that choice becomes the draft's **title** and headlines the "
        "**\"one must read\"** section. Only after that, click ⑤."
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

    _sync_buffer_cache(running)
    _render_schedule_editions(running, base, dbg)
    st.divider()

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


@st.cache_data(ttl=120, show_spinner="⏬ reading the newsletter buffer from Notion…")
def _read_buffer_state() -> tuple[BufferState | None, str]:
    """Cached Notion read behind the ⓪ caption: ``(state, error)``.

    Short TTL rather than no cache at all — every widget interaction in this tab
    triggers a rerun, and an uncached read would hit the Notion API each time.
    The spinner is not decoration: the uncached read pages the whole newsletter
    DB and takes tens of seconds, and without it the ⓪ block renders as a bare
    heading over empty space for that whole time.
    """
    from newsletter.schedule_editions import read_buffer_state

    try:
        return read_buffer_state(), ""
    except Exception as exc:  # noqa: BLE001 — surfaced to the user, never raised
        return None, f"{type(exc).__name__}: {exc}"


def _sync_buffer_cache(running: bool) -> None:
    """Drop the cached buffer read when the pipeline slot goes running → idle.

    The log panel reruns roughly once a second while a step is in flight, so
    this transition is observed and the caption refreshes on its own once the
    ⓪ run finishes — no manual refresh button needed.
    """
    if st.session_state.get(_WAS_RUNNING_KEY, False) and not running:
        _read_buffer_state.clear()
    st.session_state[_WAS_RUNNING_KEY] = running


def _render_schedule_editions(running: bool, base: list[str], dbg: list[str]) -> None:
    """⓪ Top the buffer of future newsletter rows in Notion back up.

    The count is only a *default* taken from the cached read — the subprocess
    re-reads the maximum edition number at write time, so a stale caption can
    never produce a duplicate number.
    """
    from newsletter.schedule_editions import DEFAULT_TARGET

    st.markdown("**⓪ Schedule future editions**")

    state, error = _read_buffer_state()
    # 0 is a real, reachable value: it is what a full buffer asks for, and it
    # disables the button rather than creating a ninth edition nobody wanted.
    default_count = state.shortfall(DEFAULT_TARGET) if state is not None else 1

    with st.container(horizontal=True, gap="small"):
        count = int(st.number_input(
            "editions to create",
            min_value=0, max_value=52, value=default_count, step=1,
            key="newsletter-schedule-count",
            help=f"Defaults to the shortfall against a {DEFAULT_TARGET}-edition "
                 f"buffer. 0 means there is nothing to do.",
        ))
        st.button(
            "⓪ Schedule editions",
            key="newsletter-schedule",
            disabled=running or count == 0,
            on_click=start_pipeline,
            args=(PIPELINE_NAME, base + ["schedule", "--count", str(count)] + dbg),
            width="stretch",
        )

    if error:
        st.warning(f"⚠️ couldn't read the newsletter buffer from Notion — {error}")
        st.caption("→ the step still works; it does its own read. The count above is a guess.")
    elif state is None:
        st.warning("⚠️ no newsletter edition in Notion carries both a number and a Date — "
                   "the sequence has nothing to continue from.")
    else:
        shortfall = state.shortfall(DEFAULT_TARGET)
        tail = f"{shortfall} to add" if shortfall else f"buffer full (target {DEFAULT_TARGET})"
        st.caption(
            f"latest {state.latest_label} · {state.latest_date.isoformat()} · "
            f"{state.future_count} future edition"
            f"{'' if state.future_count == 1 else 's'} — {tail}"
        )


def _render_substack_draft(
    newsletter_number: str,
    has_num: bool,
    running: bool,
    base: list[str],
    dbg: list[str],
    must_read: int | None,
) -> None:
    """⑤ Create a private Substack draft edition from the built lists.

    Sits below the must-read picker so the chosen selection becomes the draft
    title (the joined must-read line) and the featured "one must read" article
    + summary at the top of the body. Never publishes — there is no
    ``--confirm`` on this path at all (see ``newsletter/substack_draft.py``).
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
        st.caption(f"→ creates a **private** draft titled with the must-read line, "
                   f"leading with must-read #{must_read}, and opens it in the browser. "
                   f"Never publishes.")
    else:
        st.caption("→ creates a **private** draft titled with the newsletter number "
                   "(no must-read pick above) and opens it in the browser. Never publishes.")


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
        st.divider()
        st.info("ℹ️ run ④ Build HTML (or ▶) first — the must-read picker needs the "
                 "topics it writes, and ⑤ needs your pick for the draft title.")
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
    st.caption("pick which article leads — it becomes ⑤'s draft **title** and the "
               "featured **\"one must read\"** article:")
    choice = st.radio(
        "which is the must-read?",
        options=options,
        format_func=lambda n: labels[n - 1],
        key=f"newsletter-mustread-{data.get('newsletter')}",
    )
    line = format_must_read_line(top_names, int(choice))
    st.code(line, language=None)
    st.caption("↑ this line becomes the Substack draft title.")
    return int(choice)
