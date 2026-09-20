"""Check-IP tab — triage where my illustrations are being reused (issue #286).

Reverse-image search results land in a SQLite store (``check_ip/``); this tab
is where they get judged. Filter down to a slice, read what the screening skill
proposed, set the verdict and the follow-up trail, click Apply.

UI only: every read and write goes through ``check_ip.review`` / ``.screen``.
Nothing is saved until Apply — the data editor's edits sit in session state
until then, the same contract as the triage tab.

The search run is a subprocess in the shared ``"check-ip"`` process slot, so
the status badge, live log panel and sidebar behave like the other pipelines.
It spends real money (one SerpAPI call per image), so the live run sits behind
an explicit toggle and defaults to a dry run.
"""

from __future__ import annotations

from contextlib import closing

import pandas as pd
import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
)
from check_ip import db, review, screen

PIPELINE_NAME = "check-ip"


# ---------------------------------------------------------------------------
# cached reads — the store is local and fast, but the table redraws on every
# widget interaction, so a short TTL still saves a lot of pointless queries.


@st.cache_data(ttl=5, show_spinner=False)
def _overview() -> dict:
    with closing(db.connect()) as conn:
        return review.overview(conn)


@st.cache_data(ttl=5, show_spinner=False)
def _breakdown() -> pd.DataFrame:
    with closing(db.connect()) as conn:
        return review.source_breakdown(conn)


@st.cache_data(ttl=5, show_spinner=False)
def _images(source: str | None) -> list[dict]:
    with closing(db.connect()) as conn:
        return review.images(conn, source=source)


@st.cache_data(ttl=5, show_spinner=False)
def _frame(source: str | None, image: str | None, status: str, canonical: bool, limit: int) -> pd.DataFrame:
    with closing(db.connect()) as conn:
        return review.results_frame(conn, source=source, image=image, status=status,
                                    canonical_only=canonical, limit=limit)


@st.cache_data(ttl=5, show_spinner=False)
def _queue_stats(source: str | None) -> dict:
    with closing(db.connect()) as conn:
        return screen.stats(conn, source=source)


def _invalidate() -> None:
    for fn in (_overview, _breakdown, _images, _frame, _queue_stats):
        fn.clear()


# ---------------------------------------------------------------------------
# callbacks — on_click, never `if st.button(...)`


def _apply(editor_key: str) -> None:
    base: pd.DataFrame = st.session_state[f"{editor_key}-base"]
    edits = (st.session_state.get(editor_key) or {}).get("edited_rows", {})
    rows = base.to_dict("records")
    for idx, patch in edits.items():
        rows[int(idx)].update(patch)
    try:
        with closing(db.connect()) as conn:
            st.session_state["check-ip-last-apply"] = review.apply_decisions(conn, rows)
    except Exception as err:  # noqa: BLE001 — surfaced as st.error below
        st.session_state["check-ip-last-apply"] = {"error": str(err)}
    _invalidate()


def _launch(cmd: list[str]) -> None:
    _invalidate()
    start_pipeline(PIPELINE_NAME, cmd)


# ---------------------------------------------------------------------------
# sections


def _render_header() -> None:
    ov = _overview()
    cols = st.columns(6)
    cols[0].metric("illustrations", f"{ov['images']:,}")
    cols[1].metric("links found", f"{ov['results']:,}")
    cols[2].metric("canonical", f"{ov['canonical']:,}",
                   help="Unique findings — the duplicates of a link found under several images are folded away.")
    cols[3].metric("decided", f"{ov['decided']:,}")
    cols[4].metric("infringements", f"{ov['infringements']:,}",
                   help="Rows you marked ok = 0.")
    cols[5].metric("screened", f"{ov['screened']:,}",
                   help="Rows the check-ip skill has proposed a verdict for.")
    if ov.get("last_search"):
        st.caption(f"last reverse-image search: {ov['last_search']}")


def _render_run_controls() -> None:
    with st.expander("🔍 run a reverse-image search", expanded=False):
        st.caption(
            "One Google Lens search per illustration that is past its re-search window "
            "— **this costs money** (a SerpAPI call each, plus Imgur uploads for images "
            "not yet hosted). Dry run first; it reports what it *would* search and "
            "spends nothing."
        )
        running = is_running(PIPELINE_NAME)
        with st.container(horizontal=True, gap="small"):
            limit = st.number_input("limit images", min_value=0, max_value=5000, value=0, step=10,
                                    key="check-ip-limit",
                                    help="0 = every image that is due. Anything else caps the run.")
            live = st.toggle("live run (spends API credit)", value=False, key="check-ip-live")
            force = st.toggle("ignore re-search windows", value=False, key="check-ip-force",
                              help="Search every image even if it was searched recently.")

        cmd = [str(VENV_PY), "-m", "check_ip.run"]
        if not live:
            cmd.append("--dry-run")
        if limit:
            cmd += ["--limit", str(int(limit))]
        if force:
            cmd.append("--force")

        st.button(
            "▶ Run live search" if live else "▶ Dry run",
            key="check-ip-run",
            type="primary" if live else "secondary",
            disabled=running,
            on_click=_launch,
            args=(cmd,),
        )
        render_status_badge(PIPELINE_NAME)
        render_log_panel(PIPELINE_NAME)


def _render_breakdown() -> None:
    frame = _breakdown()
    if frame.empty:
        return
    with st.expander("📊 by platform", expanded=False):
        st.dataframe(frame, hide_index=True, width="stretch",
                     column_config={
                         "source": st.column_config.TextColumn("platform"),
                         "canonical": st.column_config.NumberColumn("canonical", format="%d"),
                         "pending": st.column_config.NumberColumn("undecided", format="%d"),
                         "infringements": st.column_config.NumberColumn("flagged", format="%d"),
                         "screened": st.column_config.NumberColumn("screened", format="%d"),
                     })


def _render_table() -> None:
    st.markdown("#### triage")
    with st.container(horizontal=True, gap="small"):
        source = st.selectbox("platform", list(review.SOURCES) + [review.OPEN_WEB, "any"],
                              index=0, key="check-ip-source")
        status = st.selectbox("show", list(review.STATUS_FILTERS), index=0, key="check-ip-status")
        limit = st.selectbox("rows", [100, 250, 500, 1000], index=1, key="check-ip-limit-rows")

    # None means "every platform"; review.OPEN_WEB means "no recognised
    # platform", which db.source_clause turns into a NULL test.
    source_arg = None if source == "any" else source

    image_options = ["(every illustration)"] + [r["filename"] for r in _images(source_arg)]
    image = st.selectbox("illustration", image_options, index=0, key="check-ip-image")
    image_arg = None if image == image_options[0] else image

    frame = _frame(source_arg, image_arg, status, True, int(limit))
    if frame.empty:
        st.info("nothing matches these filters")
        return

    editor_key = f"check-ip-editor-{source}-{status}-{image}-{limit}"
    st.session_state[f"{editor_key}-base"] = frame

    qs = _queue_stats(source_arg)
    st.caption(
        f"{qs['pending']:,} undecided of {qs['canonical']:,} canonical on this platform · "
        f"{qs['screened']:,} screened · {qs['proposed_infringement']:,} proposed as infringement. "
        "Showing the most-reused illustrations first. Nothing saves until you click Apply."
    )

    # Wrapped in a keyed container so the docs-capture mask can target this
    # grid alone (`.st-key-check-ip-grid`). Masking every [data-testid="stDataFrame"]
    # also hits the collapsed by-platform expander, which paints a phantom box
    # over the page — and that table is aggregate counts needing no mask.
    with st.container(key="check-ip-grid"):
        st.data_editor(
            frame,
            key=editor_key,
            hide_index=True,
            num_rows="fixed",
            width="stretch",
            height=min(900, 80 + 36 * len(frame)),
            column_order=["ok", "found_link", "local_image", "screen_verdict", "screen_reason",
                          "poster_url", "person", "chat", "report", "fixed", "title",
                          "post_date", "match_type", "source", "search_date"],
            disabled=["id", "local_image", "found_link", "title", "source", "match_type",
                      "post_date", "search_date", "duplicate", "screen_verdict",
                      "screen_reason", "poster_url", "screened_at"],
            column_config={
                "ok": st.column_config.SelectboxColumn(
                    "verdict", options=[0, 1], width="small",
                    help="0 = infringement, act on it · 1 = reviewed, acceptable use"),
                "found_link": st.column_config.LinkColumn("where it appears", display_text="open ↗",
                                                          width="small"),
                "local_image": st.column_config.TextColumn("illustration", width="medium"),
                "screen_verdict": st.column_config.TextColumn("skill says", width="small",
                                                              help="Proposed by the check-ip skill — never a decision."),
                "screen_reason": st.column_config.TextColumn("why", width="large"),
                "poster_url": st.column_config.LinkColumn("who posted", display_text="profile ↗",
                                                          width="small"),
                "person": st.column_config.TextColumn("contact", width="medium"),
                "chat": st.column_config.TextColumn("outreach", width="medium"),
                "report": st.column_config.TextColumn("report", width="small"),
                "fixed": st.column_config.CheckboxColumn("fixed", width="small"),
                "title": st.column_config.TextColumn("page title", width="large"),
                "post_date": st.column_config.TextColumn("posted", width="small"),
                "match_type": st.column_config.TextColumn("match", width="small"),
                "source": st.column_config.TextColumn("platform", width="small"),
                "search_date": st.column_config.TextColumn("found", width="small"),
            },
        )

    st.button("✅ Apply decisions", key=f"check-ip-apply-{editor_key}", type="primary",
              on_click=_apply, args=(editor_key,),
              help="Writes your verdict and follow-up trail for every edited row.")

    last = st.session_state.pop("check-ip-last-apply", None)
    if last:
        if "error" in last:
            st.error(f"apply failed: {last['error']}")
        elif last["changed"] == 0:
            st.info("no edits to save")
        else:
            st.success(f"saved {last['changed']} row(s) · {last['flagged']} flagged as infringement")


_HOW_IT_WORKS = """\
### What this is

Every illustration I publish gets reverse-image-searched through Google Lens, and
every place it turns up lands in this store. This tab is where those findings get
judged: is this a legitimate share, or is someone using my work with the credit
stripped off?

**The verdict column is the decision.** `0` means "this is an infringement, act on
it"; `1` means "reviewed, acceptable". The `contact` / `outreach` / `report` /
`fixed` columns are the follow-up trail after a `0`.

### Where the rows come from

`check_ip.run` uploads each illustration to Imgur once, runs a Google Lens search
against that URL, and records every match. Images already showing up a lot on
LinkedIn get re-searched every few days; the rest every six months. The run costs
one SerpAPI call per image, which is why the live run above is behind a toggle.

### What the skill does

`/check-ip` works the queue for you: it takes a batch of un-judged LinkedIn links,
opens each in a browser, and proposes a verdict with a reason and the poster's
profile. Its proposal shows in the **skill says** and **why** columns.

**The skill never decides and never contacts anyone.** It writes only its own
columns; the verdict and the whole follow-up trail stay mine. Worst case it
proposes something wrong and I overrule it here.

### Duplicates

The same URL often matches several illustrations. Only the canonical row of each
group is shown, so one reused post is judged once rather than eleven times.
"""


def run() -> None:
    """Entry point — routed from ``app/app.py``."""
    st.subheader("⚖️ check IP")

    if not review.store_exists():
        st.warning(
            "No store yet. Import the existing Excel history first:\n\n"
            "```\n& .\\.venv\\Scripts\\python.exe -m check_ip.migrate --report\n"
            "& .\\.venv\\Scripts\\python.exe -m check_ip.migrate\n```"
        )
        return

    with st.expander("ℹ️ how this works", expanded=False):
        st.markdown(_HOW_IT_WORKS)

    _render_header()
    _render_run_controls()
    _render_breakdown()
    st.divider()
    _render_table()
