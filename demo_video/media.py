"""Media prep for demo videos: transcode, loudness, contact sheets, output checks.

Every ffmpeg/ffprobe call goes through ``podcast.media`` (``probe``,
``run_ffmpeg``, ``ffmpeg_pipe``, ``frame_at``, ``extract_wav``) — this module
only composes them.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import numpy as np

from config.no_window import NO_WINDOW
from podcast.media import _tool, extract_wav, frame_at, probe, read_wav, run_ffmpeg

logger = logging.getLogger("demo_video.media")

State = Literal["pass", "fail", "unknown"]


@dataclass
class Result:
    """One check's outcome. ``unknown`` is its own state and never counts as a pass."""

    name: str
    state: State
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "pass"

    def line(self) -> str:
        icon = {"pass": "✅", "fail": "❌", "unknown": "⚠️"}[self.state]
        return f"{icon} {self.name}: {self.state}" + (f" — {self.reason}" if self.reason else "")


def transcode(rec: Path, clip: Path) -> Path:
    """A screen recording (Playwright VP8 ``.webm``, variable rate) → 30 fps CFR H.264, no audio, seekable."""
    clip.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(rec), "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium",
                "-crf", "14", "-g", "15", "-an", str(clip)])
    return clip


def needs_transcode(rec: Path, clip: Path) -> bool:
    return rec.is_file() and (not clip.is_file() or clip.stat().st_mtime < rec.stat().st_mtime)


def loudness_envelope(track: Path, *, window_s: float = 1.0) -> list[float]:
    """RMS level per window in dBFS (−120 for silence), from a 16 kHz mono decode of ``track``."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = extract_wav(track, Path(tmp) / "a.wav")
        samples, rate = read_wav(wav)
    step = max(1, int(rate * window_s))
    out = []
    for i in range(0, len(samples), step):
        chunk = samples[i:i + step].astype(np.float64) / 32768.0
        if len(chunk) < step // 2:
            break
        rms = math.sqrt(float(np.mean(chunk * chunk))) if len(chunk) else 0.0
        out.append(round(20 * math.log10(rms), 1) if rms > 1e-6 else -120.0)
    return out


def suggest_swaps(envelope: list[float], track_end_s: float, video_s: float, boundaries: list[tuple[str, float]],
                  *, look_s: int = 3) -> list[tuple[str, float, float]]:
    """Rank scene boundaries for bringing in a track that is trimmed to end with the video.

    ``track_end_s`` is where in the track the video's last frame lands (its
    length minus the tail). For each boundary ``(scene, t)`` the track would be
    at ``track_end_s - (video_s - t)``; the score is how much louder its next
    ``look_s`` seconds are than its previous ones — a build is a good entry.
    Returns ``(scene, t, score_db)``, best first.
    """
    ranked = []
    for scene, t in boundaries:
        src = int(round(track_end_s - (video_s - t)))
        if src - look_s < 0 or src + look_s > len(envelope):
            continue
        before = float(np.mean(envelope[src - look_s:src]))
        after = float(np.mean(envelope[src:src + look_s]))
        ranked.append((scene, round(t, 2), round(after - before, 1)))
    return sorted(ranked, key=lambda r: r[2], reverse=True)


def contact_sheet(video: Path, times: list[float], dst: Path, *, cols: int = 4, width: int = 480) -> Path:
    """Frames of ``video`` at ``times`` tiled into one PNG (numbered inputs: this ffmpeg has no glob)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(times):
            frame_at(video, max(0.0, t), Path(tmp) / f"f-{i:03d}.png")
        rows = math.ceil(len(times) / cols)
        run_ffmpeg(["-i", str(Path(tmp) / "f-%03d.png"),
                    "-vf", f"scale={width}:-1,tile={cols}x{rows}:padding=4:color=white", "-frames:v", "1", str(dst)])
    return dst


def has_audio(path: Path) -> Optional[bool]:
    """True/False, or None when ffprobe cannot read the file."""
    try:
        out = subprocess.run([_tool("ffprobe"), "-v", "error", "-select_streams", "a", "-show_entries",
                              "stream=codec_type", "-of", "json", str(path)],
                             check=True, capture_output=True, timeout=120, creationflags=NO_WINDOW)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError):
        return None
    return bool(json.loads(out.stdout or b"{}").get("streams"))


def verify_output(mp4: Path, *, width: int, height: int, fps: int, duration: float, tolerance_s: float = 1.0) -> Result:
    """Size, frame rate, length, an audio track, and a fade at the end (the last second ≥ 6 dB under the body)."""
    name = f"output {mp4.name}"
    try:
        info = probe(mp4)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
        return Result(name, "unknown", f"ffprobe could not read it ({exc.__class__.__name__})")
    if info["width"] == 0:
        return Result(name, "unknown", "ffprobe found no video stream")
    problems = []
    if (info["width"], info["height"]) != (width, height):
        problems.append(f"{info['width']}x{info['height']} ≠ {width}x{height}")
    if abs(info["fps"] - fps) > 0.01:
        problems.append(f"{info['fps']} fps ≠ {fps}")
    if abs(info["duration"] - duration) > tolerance_s:
        problems.append(f"{info['duration']:.1f} s ≠ {duration:.1f} s ±{tolerance_s}")
    audio = has_audio(mp4)
    if audio is None:
        return Result(name, "unknown", "ffprobe could not list the audio streams")
    if not audio:
        problems.append("no audio track")
    else:
        env = loudness_envelope(mp4)
        body = [v for v in env[:-3] if v > -100]
        if body and env and env[-1] > float(np.median(body)) - 6:
            problems.append(f"no fade at the end (last second {env[-1]:.0f} dBFS vs body {float(np.median(body)):.0f})")
    if problems:
        return Result(name, "fail", "; ".join(problems))
    return Result(name, "pass", f"{width}x{height}, {fps} fps, {info['duration']:.1f} s, audio, fades out")
