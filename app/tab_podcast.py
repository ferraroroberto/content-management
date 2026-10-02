"""Podcast tab — run the episode pipeline, review its clips and outputs (issue #333).

Everything shown is read from the episode's local ``podcast package`` folder;
nothing here posts, schedules or writes to Notion. The owner's per-clip review
(approve / drop / request changes, issue #339) is written to the episode's
``review.json``; the ``revise`` stage applies the feedback.
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from app.process_runner import (
    VENV_PY,
    is_running,
    render_log_panel,
    render_status_badge,
    start_pipeline,
)
from podcast import review_state
from podcast.clips import clip_length, load_clips
from podcast.episode import Episode, list_episodes, load_episode, load_podcast_config
from podcast.metrics import load_metrics, summarize
from podcast.report import SCORES_FILE
from podcast.revise import list_versions
from podcast.transcribe import fmt_ts

PIPELINE_NAME = "podcast"
RUNBOOK = Path(__file__).resolve().parent.parent / "docs" / "podcast-runbook.md"
RUNBOOK_LABEL = "📖 How the podcast pipeline works"
STAGES = ["sync", "transcribe", "clean", "select", "copy", "edit", "render", "revise", "episode", "covers", "package",
          "score"]
STATUS_ICON = {"pending": "⏳ pending", "approved": "✅ approved", "changes": "✏️ changes", "dropped": "🗑️ dropped"}


def render_runbook() -> None:
    """The start-to-finish runbook, read from disk on every render so the card
    never drifts from ``docs/podcast-runbook.md``."""
    with st.expander(RUNBOOK_LABEL, expanded=False):
        try:
            st.markdown(RUNBOOK.read_text(encoding="utf-8"))
        except OSError as exc:
            st.warning(f"runbook not readable: {exc}")


def _run_controls(folder: Path) -> None:
    cols = st.columns([4, 2, 2])
    with cols[0]:
        stages = st.multiselect("stages", STAGES, default=STAGES, key="podcast-stages",
                                help="Stages whose output already exists are skipped unless 'redo' is on.")
    with cols[1]:
        force = st.toggle("redo existing", value=False, key="podcast-force",
                          help="Re-run the selected stages even if their output exists.")
    with cols[2]:
        debug = st.toggle("debug", value=False, key="podcast-debug")
    cmd = [str(VENV_PY), "podcast_pipeline.py", str(folder), "--stages", ",".join(stages)]
    if force:
        cmd.append("--force")
    if debug:
        cmd.append("--debug")
    st.button("▶ run episode pipeline", key="podcast-run", type="primary",
              disabled=is_running(PIPELINE_NAME) or not stages,
              on_click=start_pipeline, args=(PIPELINE_NAME, cmd))
    render_status_badge(PIPELINE_NAME)
    with st.expander("log", expanded=is_running(PIPELINE_NAME)):
        render_log_panel(PIPELINE_NAME, height=300)


def _metrics(package: Path, cfg: dict) -> None:
    rows = summarize(load_metrics(package), cfg.get("llm_rates_usd_per_mtok", {}))
    if not rows:
        st.caption("no stage has run yet")
        return
    st.dataframe(rows, hide_index=True, width="stretch", key="podcast-metrics")
    st.caption("cost = metered-API equivalent at list prices; the hub runs on the subscription.")


def _files(package: Path) -> None:
    files = sorted(p for p in package.iterdir() if p.is_file() and p.suffix in {".md", ".txt", ".docx", ".html"})
    for path in files:
        st.markdown(f"- `{path.name}`")
    st.code(str(package), language=None)
    images = sorted(package.glob("*_thumbnail.png")) + sorted(package.glob("*_text.png"))
    if images:
        img_cols = st.columns(len(images))
        for col, path in zip(img_cols, images):
            col.image(str(path), caption=path.name, width="stretch")


def _review_bar(ep: Episode, clips: list[dict], state: dict) -> None:
    """Review progress, and the button that applies all open feedback."""
    waiting = review_state.to_revise(clips, state)
    cols = st.columns([3, 2])
    with cols[0]:
        if review_state.review_done(clips, state):
            st.success(f"review done: {review_state.summary(clips, state)}; covers and package can run")
        else:
            st.caption(f"review: {review_state.summary(clips, state)}. Covers and package wait until every "
                       "clip is approved or dropped.")
    with cols[1]:
        cmd = [str(VENV_PY), "podcast_pipeline.py", str(ep.folder), "--stages", "revise"]
        st.button(f"▶ apply feedback ({len(waiting)} clip{'s' if len(waiting) != 1 else ''})",
                  key="podcast-revise", disabled=is_running(PIPELINE_NAME) or not waiting,
                  on_click=start_pipeline, args=(PIPELINE_NAME, cmd),
                  help="Maps each clip's feedback to edits, re-renders those clips only, keeps the old version.")


def _send_feedback(ep: Episode, number: int) -> None:
    key = f"podcast-feedback-{number}"
    text = st.session_state.get(key, "")
    if text.strip():
        review_state.request_changes(ep, number, text)
        st.session_state[key] = ""


def _review_controls(ep: Episode, number: int, entry: dict) -> None:
    st.markdown(f"**{STATUS_ICON.get(entry['status'], entry['status'])}** · version {entry['version']}")
    cols = st.columns(2)
    cols[0].button("✅ approve", key=f"podcast-approve-{number}", disabled=entry["status"] == "approved",
                   on_click=review_state.set_status, args=(ep, number, "approved"), width="stretch")
    cols[1].button("🗑️ drop", key=f"podcast-drop-{number}", disabled=entry["status"] == "dropped",
                   on_click=review_state.set_status, args=(ep, number, "dropped"), width="stretch")
    st.text_area("feedback", key=f"podcast-feedback-{number}", height=110,
                 placeholder="e.g. at 0:12 'happy' should be 'crappy'; start at 'sleep'; no punch-ins; "
                             "title: why eight hours")
    st.button("✏️ request changes", key=f"podcast-request-{number}", on_click=_send_feedback, args=(ep, number),
              width="stretch", help="Saved now; '▶ apply feedback' re-renders the clip as a new version.")
    if entry["status"] in review_state.DECIDED:
        st.button("↩ back to pending", key=f"podcast-reopen-{number}", on_click=review_state.set_status,
                  args=(ep, number, "pending"), width="stretch")


def _rounds(entry: dict) -> None:
    rounds = entry.get("rounds") or []
    if not rounds:
        return
    with st.expander(f"feedback rounds ({len(rounds)})"):
        for r in reversed(rounds):
            state = f"applied → v{r['produced_version']}" if r.get("applied_at") else "open"
            st.markdown(f"**v{r['version']}** · {r['at'][:16].replace('T', ' ')} UTC · {state}")
            st.text(r["feedback"])
            if r.get("applied"):
                st.caption("applied: " + "; ".join(r["applied"]))
            if r.get("unhandled"):
                st.warning("not applied: " + "; ".join(r["unhandled"]))


def _clips(ep: Episode, clips: list[dict]) -> None:
    package = ep.package
    scores_path = package / SCORES_FILE
    scores = {s["number"]: s for s in json.loads(scores_path.read_text(encoding="utf-8"))} \
        if scores_path.exists() else {}
    state = review_state.load_review(ep)
    entries = {c["number"]: review_state.clip_state(state, c["number"]) for c in clips}
    _review_bar(ep, clips, state)
    table = [{"#": c["number"], "title": c["title"], "from": fmt_ts(c["start"]),
              "length (s)": round(clip_length(c)), "rendered": bool(c.get("videos")),
              "score": scores.get(c["number"], {}).get("mean"),
              "review": STATUS_ICON.get(entries[c["number"]]["status"]), "v": entries[c["number"]]["version"]}
             for c in clips]
    st.dataframe(table, hide_index=True, width="stretch", key="podcast-clips-table")

    labels = {c["number"]: f"{c['number']}. {c['title']} · {STATUS_ICON.get(entries[c['number']]['status'])}"
              for c in clips}
    number = st.selectbox("review clip", list(labels), format_func=labels.get, key="podcast-clip")
    clip, entry = next(c for c in clips if c["number"] == number), entries[number]
    older = {f"v{p.name[1:]} (earlier)": p for p in list_versions(ep, number)}
    current = f"v{entry['version']} (current)"
    shown = st.selectbox("version", [current, *reversed(older)], key=f"podcast-version-{number}") \
        if older else current
    video_cols = st.columns([1, 1, 1])
    for col, crop in zip(video_cols[:2], ("1x1", "9x16")):
        if shown == current:
            rel = (clip.get("videos") or {}).get(crop)
            path = package / rel if rel else None
        else:
            path = older[shown] / f"{crop}.mp4"
        with col:
            st.caption(crop)
            if path and path.exists():
                st.video(str(path))
            else:
                st.caption("not rendered yet")
    with video_cols[2]:
        cover = (package / "clips" / "covers" / f"{clip.get('file', '')}.png" if shown == current
                 else older[shown] / "cover.png")
        st.caption("cover")
        if cover.exists():
            st.image(str(cover), width="stretch")
        else:
            st.caption("made by the covers stage once the review is done")
        _review_controls(ep, number, entry)
        if number in scores:
            s = scores[number]
            st.markdown(f"**score {s.get('mean')}** · hook {s.get('hook')} · self-contained "
                        f"{s.get('self_contained')} · captions {s.get('caption_accuracy')} "
                        f"({s.get('caption_fixes', 0)} fixes) · framing {s.get('framing_1x1')}/{s.get('framing_9x16')}")
            st.caption(f"{s.get('note', '')} {s.get('framing_note', '')}")
        review = clip.get("caption_review") or {}
        if review.get("corrections") or review.get("doubts"):
            fixes = ", ".join(f"{f['from']} → {f['to'] or '(dropped)'}" for f in review.get("corrections", []))
            st.caption(f"caption fixes: {fixes or 'none'}; doubts: {review.get('doubts') or 'none'}")
    _rounds(entry)
    copy_cols = st.columns(2)
    with copy_cols[0]:
        st.text_area("Instagram", clip.get("instagram", ""), height=120, disabled=True,
                     key=f"podcast-ig-{number}")
    with copy_cols[1]:
        st.text_area("LinkedIn", clip.get("linkedin", ""), height=320, disabled=True,
                     key=f"podcast-li-{number}")
    with st.expander("clip text"):
        st.text(clip.get("text", ""))


def run() -> None:
    st.subheader("🎙️ podcast — episode package")
    st.caption("Two-track recording → cleaned transcript, 15 clips in 1:1 and 9:16 with captions and copy, "
               "covers, episode document and website page. Local only: nothing is published.")
    render_runbook()
    try:
        cfg = load_podcast_config()
    except RuntimeError as exc:
        st.warning(f"{exc}: copy the podcast block from config/config_example.json and fill it in.")
        return
    folders = list_episodes(cfg)
    if not folders:
        st.warning(f"No episode folder with an episode.json under {cfg.get('episodes_root')}")
        return
    folder = st.selectbox("episode", folders, format_func=lambda p: p.name, key="podcast-episode")
    try:
        ep = load_episode(folder, cfg)
    except (OSError, ValueError, KeyError) as exc:
        st.error(f"episode.json could not be read: {exc}")
        return

    _run_controls(folder)
    if not ep.package.is_dir():
        st.caption("no outputs yet: run the pipeline")
        return
    # segmented_control, not st.tabs(): st.tabs() snaps back to the first tab
    # on the rerun the clip picker triggers (issue #157, see app.py).
    view = st.segmented_control("view", ["clips", "cost", "files"], default="clips",
                                key="podcast-view", label_visibility="collapsed") or "clips"
    if view == "cost":
        _metrics(ep.package, cfg)
    elif view == "files":
        _files(ep.package)
    else:
        clips = load_clips(ep)
        if clips:
            _clips(ep, clips)
        else:
            st.caption("no clips selected yet")
