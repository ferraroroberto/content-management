"""ffmpeg / ffprobe wrappers shared by the podcast stages."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import wave
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("podcast.media")

NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} not found on PATH")
    return path


def probe(path: Path) -> dict:
    """Return ``{width, height, fps, duration}`` of a video file's first video stream."""
    out = subprocess.run(
        [_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate:format=duration",
         "-of", "json", str(path)],
        check=True, capture_output=True, timeout=120, creationflags=NO_WINDOW,
    )
    data = json.loads(out.stdout or b"{}")
    stream = (data.get("streams") or [{}])[0]
    num, _, den = (stream.get("r_frame_rate") or "0/1").partition("/")
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "fps": round(float(num) / float(den or 1), 3),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def run_ffmpeg(args: list[str], *, cwd: Optional[Path] = None, timeout: int = 3600) -> None:
    """Run ffmpeg quietly; raise with the stderr tail on failure."""
    cmd = [_tool("ffmpeg"), "-hide_banner", "-v", "error", "-y", *args]
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-1500:]}")


def extract_wav(src: Path, dst: Path, *, start: Optional[float] = None,
                duration: Optional[float] = None) -> Path:
    """16 kHz mono PCM WAV of ``src`` (optionally a window of it)."""
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    run_ffmpeg([*args, "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(dst)])
    return dst


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Mono int16 samples and the sample rate."""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data, rate


def write_wav(path: Path, samples: np.ndarray, rate: int) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype(np.int16).tobytes())
    return path


def frame_at(video: Path, seconds: float, dst: Path) -> Path:
    """Grab one frame as PNG."""
    run_ffmpeg(["-ss", f"{seconds:.3f}", "-i", str(video), "-frames:v", "1", str(dst)])
    return dst
