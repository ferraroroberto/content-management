"""Stage: render every clip in both crops with burned captions.

* **1:1** — full frame of whoever is speaking, centre-cropped to 1080², with
  the framing (``zoom``) of each shot of the clip's camera plan.
* **9:16** — owner on top, guest below (1080×960 each), captions on the seam.

Both crops play the clip's kept spans back to back (the jump cuts from the
``edit`` stage), each audio span with a 10 ms fade in and out so a cut
doesn't click. Captions are the reviewed clip words on the cut timeline.
Output is 24 fps (episodes mix 24 and 30 fps sources), H.264 yuv420p at
~12 Mbps, AAC 48 kHz stereo, loudness-normalised.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from planning.videos.videos_session import windows_safe_filename
from podcast.captions import LAYOUTS, Layout, build_ass
from podcast.clips import load_clips, save_clips
from podcast.edit import FPS, load_clip_words, output_words
from podcast.episode import Episode, work_dir
from podcast.media import run_ffmpeg
from podcast.metrics import StageRecord

logger = logging.getLogger("podcast.render")

FADE_S = 0.01
LOUDNESS = "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000"
TRACK = {"guest": "0", "host": "1"}  # input index of each speaker's track


def _pane(width: int, height: int, zoom: float = 1.0) -> str:
    """Scale to ``height × zoom`` and crop the centre, a little above middle when zoomed."""
    scaled = 2 * round(height * zoom / 2)
    return (f"scale=-2:{scaled},crop={width}:{height}:(iw-{width})/2:(ih-{height})*0.45,setsar=1")


def _span(t0: float, a: float, b: float) -> str:
    return f"start={a - t0:.4f}:end={b - t0:.4f}"


def filter_graph(layout: Layout, clip: dict, ass_name: str) -> str:
    """ffmpeg filter_complex for one clip; input 0 is the guest track, input 1 the host.

    Video: 1:1 trims each shot from its speaker's track at its framing; 9:16
    stacks both tracks and trims the kept spans. Audio: both tracks mixed,
    the kept spans trimmed and faded. Both concatenate in order."""
    t0 = clip["start"]
    keep = clip.get("keep") or [[clip["start"], clip["end"]]]
    parts: list[str] = []
    if layout.name == "1x1":
        shots = clip.get("shots") or [{"a": a, "b": b, "spk": clip["speaker"], "zoom": 1.0} for a, b in keep]
        for spk, src in TRACK.items():
            mine = [i for i, s in enumerate(shots) if s["spk"] == spk]
            if mine:
                outs = "".join(f"[s{spk}{i}]" for i in mine)
                parts.append(f"[{src}:v]fps={FPS},split={len(mine)}{outs}")
        for i, s in enumerate(shots):
            parts.append(f"[s{s['spk']}{i}]trim={_span(t0, s['a'], s['b'])},setpts=PTS-STARTPTS,"
                         f"{_pane(layout.width, layout.height, s['zoom'])}[v{i}]")
        n = len(shots)
    else:
        half = layout.height // 2
        outs = "".join(f"[c{i}]" for i in range(len(keep)))
        parts.append(f"[1:v]fps={FPS},{_pane(layout.width, half)}[top];"
                     f"[0:v]fps={FPS},{_pane(layout.width, half)}[bot];"
                     f"[top][bot]vstack=inputs=2,split={len(keep)}{outs}")
        parts += [f"[c{i}]trim={_span(t0, a, b)},setpts=PTS-STARTPTS[v{i}]" for i, (a, b) in enumerate(keep)]
        n = len(keep)
    parts.append(f"{''.join(f'[v{i}]' for i in range(n))}concat=n={n}:v=1:a=0,ass={ass_name}:fontsdir=fonts[v]")
    outs = "".join(f"[m{i}]" for i in range(len(keep)))
    parts.append(f"[0:a][1:a]amix=inputs=2:normalize=0,asplit={len(keep)}{outs}")
    for i, (a, b) in enumerate(keep):
        parts.append(f"[m{i}]atrim={_span(t0, a, b)},asetpts=PTS-STARTPTS,afade=t=in:d={FADE_S},"
                     f"afade=t=out:st={b - a - FADE_S:.4f}:d={FADE_S}[k{i}]")
    parts.append(f"{''.join(f'[k{i}]' for i in range(len(keep)))}concat=n={len(keep)}:v=0:a=1,{LOUDNESS}[a]")
    return ";".join(parts)


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
    """Render one crop; ``words`` are the clip's caption words on its cut timeline."""
    ass_name = f"clip{clip['number']:02d}_{layout.name}.ass"
    duration = sum(b - a for a, b in clip.get("keep") or [[clip["start"], clip["end"]]])
    (scratch / ass_name).write_text(build_ass(words, 0.0, duration, layout), encoding="utf-8")
    seek = ["-ss", f"{clip['start']:.3f}", "-t", f"{clip['end'] - clip['start']:.3f}"]
    run_ffmpeg([
        *seek, "-i", str(ep.tracks["guest"]), *seek, "-i", str(ep.tracks["host"]),
        "-filter_complex", filter_graph(layout, clip, ass_name),
        "-map", "[v]", "-map", "[a]", "-r", str(FPS), *encoder_args(encoder),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-movflags", "+faststart", str(out),
    ], cwd=scratch)


def prepare_fonts(cfg: dict, scratch: Path) -> None:
    """Copy the caption font where the ASS subtitles filter looks for it (``fonts/`` in the scratch dir)."""
    fonts = scratch / "fonts"
    fonts.mkdir(exist_ok=True)
    shutil.copy2(cfg["fonts"]["caption"], fonts / Path(cfg["fonts"]["caption"]).name)


def run(ep: Episode, cfg: dict, rec: StageRecord, *, force: bool = False) -> list[dict]:
    """Render every clip; a clip already on disk is kept unless ``force``."""
    clips = load_clips(ep)
    clip_words = load_clip_words(ep)
    scratch = work_dir(cfg, ep)
    prepare_fonts(cfg, scratch)
    encoder = cfg.get("video_encoder", "libx264")
    taken: set[str] = set()
    for clip in clips:
        clip["file"] = clip_filename(clip, taken)
        words = clip_words.get(str(clip["number"]))
        if not words:
            logger.warning("⚠️ clip %d has no caption words: run the edit stage first", clip["number"])
        for (name, layout), out in zip(LAYOUTS.items(), clip_outputs(ep, clip, "videos")):
            out.parent.mkdir(parents=True, exist_ok=True)
            if force or not out.exists():
                render_clip(ep, clip, layout, output_words(clip, words), scratch, out, encoder)
            clip.setdefault("videos", {})[name] = out.relative_to(ep.package).as_posix()
        save_clips(ep, clips)  # per clip, so the app shows progress and a crash keeps it
        logger.info("✅ rendered %d/%d: %s", clip["number"], len(clips), clip["title"])
    return clips
