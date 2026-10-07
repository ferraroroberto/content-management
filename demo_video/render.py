"""Render one cut through the Remotion template (``demo_video/remotion``)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from config.no_window import NO_WINDOW
from demo_video.storyboard import Cut, Demo, media_root, resolve_cut
from podcast.media import probe

logger = logging.getLogger("demo_video.render")

TEMPLATE = Path(__file__).parent / "remotion"
ENTRY = "src/index.ts"
COMPOSITION = "Demo"


def load_config() -> dict:
    """The optional ``demo_video`` block of config.json (defaults when absent)."""
    try:
        from config.loader import load_full_config  # noqa: PLC0415
        block = load_full_config().get("demo_video") or {}
    except FileNotFoundError:
        block = {}
    return block


def node_root(cfg: dict) -> Path:
    """The Remotion template folder holding ``node_modules`` (never under OneDrive)."""
    return Path(cfg.get("node_root") or TEMPLATE)


def remotion_cli(root: Path) -> list[str]:
    """``node <root>/node_modules/@remotion/cli/remotion-cli.js`` — node directly, no ``npx.cmd`` shell."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found on PATH (install Node.js 20+)")
    cli = root / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    if not cli.is_file():
        raise RuntimeError(f"Remotion is not installed in {root} — run `npm install` there")
    return [node, str(cli)]


def output_path(folder: Path, cut: Cut, *, preview: bool) -> Path:
    return folder / "out" / f"{cut.id}{'.preview' if preview else ''}.mp4"


def track_seconds(folder: Path, demo: Demo, cut: Cut) -> dict[str, float]:
    """Length of each soundtrack file that needs it (``align_end_tail_s``)."""
    out = {}
    for t in cut.soundtrack:
        if t.align_end_tail_s is not None:
            file = t.file.replace("{lang}", cut.lang)
            out[file] = probe(media_root(folder, demo) / file)["duration"]
    return out


def write_props(folder: Path, demo: Demo, cut: Cut) -> Path:
    props = resolve_cut(demo, folder, cut, track_seconds=track_seconds(folder, demo, cut))
    path = folder / "out" / ".props" / f"{cut.id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(props, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def render_cut(folder: Path, demo: Demo, cut: Cut, *, preview: bool = False, cfg: Optional[dict] = None,
               timeout: int = 3600) -> Path:
    """Render ``cut`` to ``out/<cut>.mp4`` (or ``.preview.mp4`` at half scale); returns the file."""
    root = node_root(cfg if cfg is not None else load_config())
    props = write_props(folder, demo, cut)
    out = output_path(folder, cut, preview=preview)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [*remotion_cli(root), "render", ENTRY, COMPOSITION, str(out), f"--props={props}",
           f"--public-dir={media_root(folder, demo)}", "--log=error"]
    cmd += ["--scale=0.5"] if preview else ["--crf=18"]
    logger.info("ℹ️ rendering %s (%s, %s%s) → %s", cut.id, cut.lang, cut.aspect, ", preview" if preview else "", out)
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"remotion render failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-2000:]}")
    info = probe(out)
    logger.info("✅ %s: %dx%d, %.1f s, rendered in %.0f s", out.name, info["width"], info["height"], info["duration"],
                time.monotonic() - t0)
    return out
