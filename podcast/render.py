"""Stage: render every clip in both crops with burned captions.

* **1:1** — the clip's dominant speaker full frame, centre-cropped to 1080².
* **9:16** — owner on top, guest below (1080×960 each), captions on the seam.

Both are normalised to 24 fps (episodes mix 24 and 30 fps sources), H.264
yuv420p at ~12 Mbps, AAC 48 kHz stereo, loudness-normalised.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from planning.videos.videos_session import windows_safe_filename
from podcast.captions import LAYOUTS, Layout, build_ass
from podcast.clips import load_clips, save_clips
from podcast.episode import Episode, work_dir
from podcast.media import run_ffmpeg
from podcast.metrics import StageRecord
from podcast.transcribe import load_words

logger = logging.getLogger("podcast.render")

FPS = 24
AUDIO_FILTER = "amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000"


def _pane(src: str, width: int, height: int) -> str:
    return f"[{src}:v]fps={FPS},scale=-2:{height},crop={width}:{height},setsar=1"


def filter_graph(layout: Layout, speaker: str, ass_name: str) -> str:
    """ffmpeg filter_complex; input 0 is the guest track, input 1 the host track."""
    captions = f"ass={ass_name}:fontsdir=fonts"
    if layout.name == "1x1":
        src = "0" if speaker == "guest" else "1"
        video = f"{_pane(src, layout.width, layout.height)},{captions}[v]"
    else:
        half = layout.height // 2
        video = (f"{_pane('1', layout.width, half)}[top];"
                 f"{_pane('0', layout.width, half)}[bot];"
                 f"[top][bot]vstack=inputs=2,{captions}[v]")
    return f"{video};[0:a][1:a]{AUDIO_FILTER}[a]"


def encoder_args(encoder: str) -> list[str]:
    if encoder.endswith("_nvenc"):
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", "-b:v", "12M",
                "-maxrate", "14M", "-bufsize", "24M"]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-maxrate", "12M", "-bufsize", "24M"]


def clip_filename(clip: dict, taken: set[str]) -> str:
    """File stem from the title (house convention), unique within the episode."""
    stem = windows_safe_filename(clip["title"]) or f"clip {clip['number']:02d}"
    while stem.lower() in taken:
        stem = f"{stem} {clip['number']}"
    taken.add(stem.lower())
    return stem


def clip_outputs(ep: Episode, clip: dict, kind: str) -> list[Path]:
    """Files a clip ends up with: ``"videos"`` (both crops) or ``"covers"``."""
    if kind == "covers":
        return [ep.package / "clips" / "covers" / f"{clip['file']}.png"]
    return [ep.package / "clips" / name / f"{clip['file']}.mp4" for name in LAYOUTS]


def render_clip(ep: Episode, clip: dict, layout: Layout, words: list[dict], scratch: Path,
                out: Path, encoder: str) -> None:
    ass_name = f"clip{clip['number']:02d}_{layout.name}.ass"
    (scratch / ass_name).write_text(build_ass(words, clip["start"], clip["end"], layout), encoding="utf-8")
    dur = clip["end"] - clip["start"]
    seek = ["-ss", f"{clip['start']:.3f}", "-t", f"{dur:.3f}"]
    run_ffmpeg([
        *seek, "-i", str(ep.tracks["guest"]), *seek, "-i", str(ep.tracks["host"]),
        "-filter_complex", filter_graph(layout, clip["speaker"], ass_name),
        "-map", "[v]", "-map", "[a]", "-r", str(FPS), *encoder_args(encoder),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ], cwd=scratch)


def run(ep: Episode, cfg: dict, rec: StageRecord, *, force: bool = False) -> list[dict]:
    """Render every clip; a clip already on disk is kept unless ``force``."""
    clips = load_clips(ep)
    words = load_words(ep)
    scratch = work_dir(cfg, ep)
    fonts = scratch / "fonts"
    fonts.mkdir(exist_ok=True)
    shutil.copy2(cfg["fonts"]["caption"], fonts / Path(cfg["fonts"]["caption"]).name)
    encoder = cfg.get("video_encoder", "libx264")
    taken: set[str] = set()
    for clip in clips:
        clip["file"] = clip_filename(clip, taken)
        for (name, layout), out in zip(LAYOUTS.items(), clip_outputs(ep, clip, "videos")):
            out.parent.mkdir(parents=True, exist_ok=True)
            if force or not out.exists():
                render_clip(ep, clip, layout, words, scratch, out, encoder)
            clip.setdefault("videos", {})[name] = out.relative_to(ep.package).as_posix()
        save_clips(ep, clips)  # per clip, so the app shows progress and a crash keeps it
        logger.info("✅ rendered %d/%d: %s", clip["number"], len(clips), clip["title"])
    return clips
