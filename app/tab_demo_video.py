"""Demo video tab — the runbook and the deterministic stages (issue #362).

The interactive part of a demo — the message brief, the storyboard, the
recording and the review loop — is the ``/demo-video`` skill in Claude Code;
this tab shows how it all works and runs the stages that need no judgment:
prep, check, preview, render and status. Nothing here publishes anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import streamlit as st
from pydantic import ValidationError

from app.process_runner import VENV_PY, is_running, render_log_panel, render_status_badge, start_pipeline
from demo_video.render import output_path
from demo_video.storyboard import DEMO_FILE, Demo, load_demo, total_frames, validate

PIPELINE_NAME = "demo_video"
RUNBOOK = Path(__file__).resolve().parent.parent / "docs" / "demo-video-runbook.md"
RUNBOOK_LABEL = "📖 How demo videos are made"
ALL_CUTS = "all cuts"

# button label → the pipeline arguments it adds (record is left to the skill: it needs a reviewed driver)
ACTIONS: dict[str, list[str]] = {
    "🧰 prep": ["--stages", "prep"],
    "🛡️ check": ["--stages", "check"],
    "👀 preview": ["--preview"],
    "🎬 render": [],
    "ℹ️ status": ["--status"],
}


def build_command(folder: Path, action: str, *, cut: Optional[str] = None, force: bool = False) -> list[str]:
    """The ``demo_video_pipeline.py`` command line for one of the tab's buttons."""
    cmd = [str(VENV_PY), "demo_video_pipeline.py", str(folder), *ACTIONS[action]]
    if action == "ℹ️ status":
        return cmd
    if cut and cut != ALL_CUTS:
        cmd += ["--cut", cut]
    if force and action in ("🧰 prep", "👀 preview", "🎬 render"):
        cmd.append("--force")
    return cmd


def render_runbook() -> None:
    """The runbook, read from disk on every render so the card never drifts from
    ``docs/demo-video-runbook.md``."""
    with st.expander(RUNBOOK_LABEL, expanded=False):
        try:
            st.markdown(RUNBOOK.read_text(encoding="utf-8"))
        except OSError as exc:
            st.warning(f"runbook not readable: {exc}")


def _load(folder: Path) -> Optional[Demo]:
    if not (folder / DEMO_FILE).is_file():
        st.warning(f"No {DEMO_FILE} in this folder — start a demo with `/demo-video new <app> <folder>` in Claude Code.")
        return None
    try:
        return load_demo(folder)
    except ValidationError as exc:
        st.error(f"{DEMO_FILE} is invalid:\n\n{exc}")
        return None


def _summary(folder: Path, demo: Demo) -> None:
    st.caption(f"**{demo.title}** — {len(demo.scenes)} scenes, {total_frames(demo) / demo.fps:.1f} s")
    rows = []
    for cut in demo.cuts:
        final, preview = output_path(folder, cut, preview=False), output_path(folder, cut, preview=True)
        rows.append({"cut": cut.id, "language": cut.lang, "aspect": cut.aspect,
                     "audience": "public" if cut.public else "private",
                     "state": "rendered" if final.is_file() else "preview only" if preview.is_file() else "to render"})
    st.dataframe(rows, hide_index=True, width="stretch")
    errors = validate(demo, folder)
    if errors:
        st.warning("Storyboard problems:\n\n" + "\n".join(f"- {e}" for e in errors))


def _controls(folder: Path, demo: Demo) -> None:
    cols = st.columns([3, 2])
    with cols[0]:
        cut = st.selectbox("cut", [ALL_CUTS, *(c.id for c in demo.cuts)], key="demo-video-cut")
    with cols[1]:
        force = st.toggle("redo existing", value=False, key="demo-video-force",
                          help="Re-transcode in prep, re-render a cut whose output exists.")
    running = is_running(PIPELINE_NAME)
    buttons = st.columns(len(ACTIONS))
    for col, action in zip(buttons, ACTIONS):
        with col:
            st.button(action, key=f"demo-video-{action}", width="stretch", disabled=running,
                      on_click=start_pipeline, args=(PIPELINE_NAME, build_command(folder, action, cut=cut, force=force)))
    render_status_badge(PIPELINE_NAME)
    with st.expander("log", expanded=running):
        render_log_panel(PIPELINE_NAME, height=300)


def _outputs(folder: Path) -> None:
    out = folder / "out"
    videos = sorted(out.glob("*.mp4")) if out.is_dir() else []
    sheets = sorted(out.glob("*.sheet.png")) if out.is_dir() else []
    if not videos:
        st.caption("Nothing rendered yet.")
        return
    st.markdown("**Rendered**")
    for v in videos:
        st.code(str(v), language=None)
    for sheet in sheets:
        with st.expander(f"🖼️ {sheet.name}", expanded=False):
            st.image(str(sheet), width="stretch")


def run() -> None:
    render_runbook()
    st.info("The message brief, storyboard, recording and review are done with **`/demo-video`** in Claude Code, "
            "run from this repo. This tab runs the stages that need no judgment.")
    folder_text = st.text_input("demo folder", key="demo-video-folder",
                                placeholder="the folder holding demo.json (outside the repo)")
    if not folder_text.strip():
        return
    folder = Path(folder_text.strip().strip('"'))
    demo = _load(folder)
    if demo is None:
        return
    _summary(folder, demo)
    _controls(folder, demo)
    _outputs(folder)
