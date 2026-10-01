"""Podcast tab — run the episode pipeline, review its clips and outputs (issue #333).

Everything shown is read from the episode's local ``podcast package`` folder;
nothing here posts, schedules or writes to Notion.
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
from podcast.clips import load_clips
from podcast.episode import list_episodes, load_episode, load_podcast_config
from podcast.metrics import load_metrics, summarize
from podcast.report import SCORES_FILE
from podcast.transcribe import fmt_ts

PIPELINE_NAME = "podcast"
STAGES = ["transcribe", "clean", "select", "copy", "render", "episode", "covers", "package", "score"]


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


def _clips(package: Path, clips: list[dict]) -> None:
    scores_path = package / SCORES_FILE
    scores = {s["number"]: s for s in json.loads(scores_path.read_text(encoding="utf-8"))} \
        if scores_path.exists() else {}
    table = [{"#": c["number"], "title": c["title"], "from": fmt_ts(c["start"]),
              "length (s)": round(c["end"] - c["start"]), "rendered": bool(c.get("videos")),
              "score": scores.get(c["number"], {}).get("mean")} for c in clips]
    st.dataframe(table, hide_index=True, width="stretch", key="podcast-clips-table")

    labels = {c["number"]: f"{c['number']}. {c['title']}" for c in clips}
    number = st.selectbox("review clip", list(labels), format_func=labels.get, key="podcast-clip")
    clip = next(c for c in clips if c["number"] == number)
    video_cols = st.columns([1, 1, 1])
    for col, crop in zip(video_cols[:2], ("1x1", "9x16")):
        rel = (clip.get("videos") or {}).get(crop)
        with col:
            st.caption(crop)
            if rel and (package / rel).exists():
                st.video(str(package / rel))
            else:
                st.caption("not rendered yet")
    with video_cols[2]:
        cover = package / "clips" / "covers" / f"{clip.get('file', '')}.png"
        st.caption("cover")
        if clip.get("file") and cover.exists():
            st.image(str(cover), width="stretch")
        if number in scores:
            s = scores[number]
            st.markdown(f"**score {s.get('mean')}** · hook {s.get('hook')} · self-contained "
                        f"{s.get('self_contained')} · captions {s.get('caption_accuracy')} "
                        f"({s.get('caption_wer_pct')}% WER) · framing {s.get('framing_1x1')}/{s.get('framing_9x16')}")
            st.caption(f"{s.get('note', '')} {s.get('framing_note', '')}")
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
            _clips(ep.package, clips)
        else:
            st.caption("no clips selected yet")
