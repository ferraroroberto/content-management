"""Newsletter triage tab — run the engine for a closed Saturday→Saturday week, review the 8+8+8
suggestions in an editable table, Apply (decisions → sender tiers → watermark), distil lessons.

UI only: every read/write goes through ``newsletter.triage.review`` / ``.db`` / ``.lessons``
(issue #217). The engine runs as a subprocess in the shared ``"triage"`` process slot so the
status badge / live log panel / sidebar work exactly like the other pipelines.

Nothing is written before the owner clicks **Apply review**; a stored week is never re-run
silently — the override toggle must be on (the engine itself refuses without ``--force``).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
)
from newsletter.triage import db, lessons, review
from newsletter.triage.rank import TOPICS

PIPELINE_NAME = "triage"
_SHORT = {"leadership and management": "leadership", "personal development": "persdev", "innovation": "innovation"}


# ---------------------------------------------------------------------------
# cached reads (the store is remote — one call per rerun is plenty)


@st.cache_data(ttl=5, show_spinner=False)
def _runs() -> list[dict]:
    return db.list_runs()


@st.cache_data(ttl=5, show_spinner=False)
def _frame(run_id: int, all_candidates: bool) -> pd.DataFrame:
    return review.review_frame(run_id, all_candidates=all_candidates)


@st.cache_data(ttl=5, show_spinner=False)
def _run(run_id: int) -> dict | None:
    return db.get_run(run_id)


@st.cache_data(ttl=5, show_spinner=False)
def _review(start: str, end: str) -> dict | None:
    return db.get_review(start, end)


@st.cache_data(ttl=5, show_spinner=False)
def _lessons(start: str, end: str) -> list[dict]:
    return db.lessons(start=start, end=end)


@st.cache_data(ttl=5, show_spinner=False)
def _new_senders(run_id: int) -> list[dict]:
    return review.new_senders(run_id)


def _invalidate() -> None:
    for f in (_runs, _frame, _run, _review, _lessons, _new_senders):
        f.clear()


# ---------------------------------------------------------------------------
# callbacks (on_click — never `if st.button(...)`)


def _launch(cmd: list[str]) -> None:
    _invalidate()
    start_pipeline(PIPELINE_NAME, cmd)


def _apply(run_id: int, editor_key: str, comment_key: str) -> None:
    base: pd.DataFrame = st.session_state[f"{editor_key}-base"]
    edits = (st.session_state.get(editor_key) or {}).get("edited_rows", {})
    rows = base.to_dict("records")
    for idx, patch in edits.items():
        rows[int(idx)].update(patch)
    try:
        res = review.apply_review(run_id, rows, st.session_state.get(comment_key, ""))
        st.session_state["triage-last-apply"] = res
    except Exception as err:  # noqa: BLE001 — surfaced as st.error below
        st.session_state["triage-last-apply"] = {"error": str(err)}
    _invalidate()


def _distil(run_id: int) -> None:
    try:
        rows = lessons.propose(run_id)
        st.session_state["triage-last-distil"] = {"n": len(rows)}
    except Exception as err:  # noqa: BLE001
        st.session_state["triage-last-distil"] = {"error": str(err)}
    _invalidate()


def _accept_lessons(ids: list[int], accepted: bool) -> None:
    lessons.accept(ids, accepted=accepted)
    _invalidate()


def _save_tiers(run_id: int, editor_key: str) -> None:
    base: list[dict] = st.session_state[f"{editor_key}-base"]
    edits = (st.session_state.get(editor_key) or {}).get("edited_rows", {})
    n = 0
    for idx, patch in edits.items():
        if "tier" in patch:
            row = base[int(idx)]
            review.set_sender_tier(row["sender_address"], patch["tier"], name=row.get("sender_name", ""))
            n += 1
    st.session_state["triage-last-tiers"] = n
    _invalidate()


# ---------------------------------------------------------------------------
# sections


def _render_run_controls(runs: list[dict]) -> None:
    st.markdown("#### run")
    weeks = review.saturday_weeks(8)
    labels = [w[2] for w in weeks] + ["custom…"]
    choice = st.selectbox("window (closed week, Saturday → Saturday)", labels, index=1, key="triage-week")
    if choice == "custom…":
        with st.container(horizontal=True, gap="small"):
            start = st.date_input("since", value=weeks[1][0], key="triage-since")
            end = st.date_input("until (exclusive)", value=weeks[1][1], key="triage-until")
    else:
        start, end = next((w[0], w[1]) for w in weeks if w[2] == choice)
    with st.container(horizontal=True, gap="small"):
        offline = st.toggle("offline (history cache)", value=False, key="triage-offline",
                            help="Read the e-mails from results/newsletter/triage/history instead of Gmail.")
        no_llm = st.toggle("no LLM (rule-only)", value=False, key="triage-nollm")
        debug = st.toggle("debug", value=False, key="triage-debug")

    stored = next((r for r in runs if r["kind"] == "live" and r["window_start"] == str(start)
                   and r["window_end"] == str(end)), None)
    running = is_running(PIPELINE_NAME)
    cmd = [str(VENV_PY), "-m", "newsletter.triage.run", "--since", str(start), "--until", str(end),
           "--days", str(max(1, (end - start).days))]
    if offline:
        cmd += ["--source", "cache"]
    if no_llm:
        cmd.append("--no-llm")
    if debug:
        cmd.append("--debug")

    with st.container(horizontal=True, gap="small"):
        if stored:
            override = st.toggle(f"override stored run {stored['id']} ({stored['status']})", value=False,
                                 key="triage-override",
                                 help="This week is already stored. Re-running replaces its candidates; your "
                                      "applied decisions are kept and pre-fill the new table.")
            st.button("↻ Re-run week (override)", key="triage-rerun", type="primary", disabled=running or not override,
                      on_click=_launch, args=(cmd + ["--force"],))
        else:
            st.button("▶ Run triage", key="triage-run", type="primary", disabled=running or start >= end,
                      on_click=_launch, args=(cmd,))
        bt = st.text_input("backtest editions", value="", key="triage-backtest", placeholder="N226,N227",
                           label_visibility="collapsed", width=160)
        st.button("🧪 Backtest", key="triage-backtest-run", disabled=running or not bt.strip(),
                  on_click=_launch, args=([str(VENV_PY), "-m", "newsletter.triage.run", "--backtest", bt.strip(),
                                           "--force"] + (["--debug"] if debug else []),),
                  help="Replay past editions offline from the history cache (stored as kind=backtest).")
    render_status_badge(PIPELINE_NAME)
    render_log_panel(PIPELINE_NAME)


def _render_review(run: dict) -> None:
    run_id = int(run["id"])
    start, end = run["window_start"], run["window_end"]
    st.markdown(f"#### review — {start} → {end} · {run['kind']}"
                + (f" · {run.get('edition')}" if run.get("edition") else "")
                + (f" · stats: {run['stats'].get('emails', '?')} emails · {run['stats'].get('links', '?')} links · "
                   f"{run['stats'].get('llm_calls', '?')} LLM calls" if run.get("stats") else ""))
    if run.get("status") != "done":
        st.warning(f"run {run_id} is **{run.get('status')}** — " + (str((run.get("stats") or {}).get("error", ""))
                                                                    if run.get("status") == "failed" else "wait for it to finish"))
        return
    all_cands = st.toggle("show every scored candidate (promote from the long tail)", value=False,
                          key=f"triage-all-{run_id}")
    frame = _frame(run_id, all_cands)
    if frame.empty:
        st.info("no candidates stored for this run")
        return
    editor_key = f"triage-editor-{run_id}-{int(all_cands)}"
    st.session_state[f"{editor_key}-base"] = frame
    counts = review.topic_counts(frame)
    st.caption(" · ".join(f"{_SHORT[t]} {counts[t]}/8" for t in TOPICS)
               + " — suggestions pre-ticked (weak fills unticked); edit freely, nothing is saved until Apply.")
    st.data_editor(
        frame,
        key=editor_key,
        hide_index=True,
        num_rows="fixed",
        width="stretch",
        height=min(900, 60 + 36 * len(frame)),
        column_order=["topic", "pick", "star", "must_read", "score", "title", "url", "sender", "summary", "why", "note", "suggested"],
        disabled=["cid", "topic", "score", "title", "url", "sender", "summary", "why", "suggested", "canonical", "sender_address"],
        column_config={
            "topic": st.column_config.TextColumn("topic", width="medium"),
            "pick": st.column_config.CheckboxColumn("✅", help="include in the edition", width="small"),
            "star": st.column_config.CheckboxColumn("⭐", help="star (one per topic)", width="small"),
            "must_read": st.column_config.CheckboxColumn("🏆", help="must-read (one per edition)", width="small"),
            "score": st.column_config.NumberColumn("score", format="%.1f", width="small"),
            "title": st.column_config.TextColumn("title", width="large"),
            "url": st.column_config.LinkColumn("link", display_text="open ↗", width="small"),
            "sender": st.column_config.TextColumn("sender", width="small"),
            "summary": st.column_config.TextColumn("summary", width="large"),
            "why": st.column_config.TextColumn("why", width="medium"),
            "note": st.column_config.TextColumn("your note", width="medium"),
            "suggested": st.column_config.TextColumn("engine", width="small"),
        },
    )
    comment_key = f"triage-comment-{run_id}"
    prior = _review(start, end)
    st.text_area("why these choices (feeds the lessons step)", value=(prior or {}).get("comment") or "",
                 key=comment_key, height=90)
    with st.container(horizontal=True, gap="small"):
        st.button("✅ Apply review", key=f"triage-apply-{run_id}", type="primary",
                  on_click=_apply, args=(run_id, editor_key, comment_key),
                  help="Saves every row's decision for this window, updates sender tiers from the whole history, "
                       "stores the comment, advances the local watermark.")
        st.button("🧠 Distil lessons (claude_sonnet)", key=f"triage-distil-{run_id}", disabled=prior is None,
                  on_click=_distil, args=(run_id,),
                  help="Ask the hub to turn your comment + disagreements into ≤3 proposed criteria notes; "
                       "nothing enters the engine until you accept them below.")
    last = st.session_state.pop("triage-last-apply", None)
    if last:
        if "error" in last:
            st.error(f"apply failed: {last['error']}")
        else:
            st.success(f"applied: {last['decisions']} decisions · {last['picks']} picks · {last['stars']} stars · "
                       f"{len(last['tier_changes'])} tier change(s)"
                       + (f" · reviewed until {last['reviewed_until']}" if last.get("reviewed_until") else ""))
            for c in last["tier_changes"]:
                st.caption(f"• {c}")
    if prior:
        st.caption(f"last applied {str(prior.get('reviewed_at', ''))[:16]} · {prior.get('n_pick')} picks · "
                   + (f"{len(prior.get('tier_changes') or [])} tier change(s)"))
    _render_lessons(run_id, start, end)
    _render_new_senders(run_id)
    report_path = run.get("report_path")
    with st.expander("📄 rendered report (the markdown the engine wrote)", expanded=False):
        if report_path and Path(report_path).exists():
            st.markdown(Path(report_path).read_text(encoding="utf-8"))
        else:
            st.info("report file not found on this machine" + (f": {report_path}" if report_path else ""))


def _render_lessons(run_id: int, start: str, end: str) -> None:
    last = st.session_state.pop("triage-last-distil", None)
    if last:
        if "error" in last:
            st.error(f"distil failed: {last['error']}")
        elif not last.get("n"):
            st.info("the model proposed nothing new for this review")
    rows = _lessons(start, end)
    if not rows:
        return
    st.markdown("**lessons from this review** — tick to accept into the engine's criteria brief")
    for r in rows:
        key = f"triage-lesson-{r['id']}"
        st.checkbox(r["text"], value=bool(r.get("accepted")), key=key,
                    on_change=lambda rid=r["id"], k=key: _accept_lessons([rid], st.session_state[k]))


def _render_new_senders(run_id: int) -> None:
    new = _new_senders(run_id)
    if not new:
        return
    st.markdown("**new senders** — no history; set a tier (default `review` = scored on content only)")
    key = f"triage-senders-{run_id}"
    st.session_state[f"{key}-base"] = new
    st.data_editor(
        pd.DataFrame(new, columns=["sender_name", "sender_address", "emails", "tier"]),
        key=key, hide_index=True, num_rows="fixed", width="stretch",
        disabled=["sender_name", "sender_address", "emails"],
        column_config={"tier": st.column_config.SelectboxColumn("tier", options=list(review.TIERS), required=True),
                       "emails": st.column_config.NumberColumn("emails", width="small")},
    )
    n = st.session_state.pop("triage-last-tiers", None)
    st.button("💾 Save sender tiers", key=f"{key}-save", on_click=_save_tiers, args=(run_id, key))
    if n is not None:
        st.success(f"saved {n} tier(s) to overrides.json")


# ---------------------------------------------------------------------------


def run() -> None:
    st.subheader("🧭 triage — weekly newsletter inbox → edition shortlist")
    st.caption(
        "Pick a closed week → ▶ run (Gmail → links → scoring → 8+8+8 suggestions, stored in Supabase) → "
        "review the table → ✅ Apply (decisions, sender tiers, watermark) → 🧠 distil lessons. "
        "One window = one edition; a stored week is only re-run with the override toggle."
    )
    try:
        runs = _runs()
    except Exception as err:  # noqa: BLE001 — schema missing / network: show the state, never an empty page
        st.error(f"triage store unavailable: {str(err)[:300]}")
        st.caption("Apply `newsletter/triage/schema.sql` once in the Supabase SQL editor if the tables are missing.")
        runs = []
    _render_run_controls(runs)
    st.divider()
    if not runs:
        st.info("no stored runs yet — run a week above, or import history: "
                "`python -m newsletter.triage.db import-history` then `python -m newsletter.triage.run --backtest N224,N225,N226,N227`")
        return
    options = {f"{r['window_start']} → {r['window_end']} · {r['kind']}"
               + (f" {r.get('edition')}" if r.get("edition") else "")
               + f" · {r['status']} · {r.get('picks') if r.get('picks') is not None else '?'} picks"
               + (" · reviewed ✓" if r.get("reviewed") else ""): r["id"] for r in runs}
    choice = st.selectbox("stored run", list(options.keys()), index=0, key="triage-run-pick")
    run = _run(options[choice])
    if run:
        _render_review(run)
