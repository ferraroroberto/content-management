"""Stage: align the two recorder tracks before anything reads them (issue #343).

Each side of a recorder session (#341, #342) records on its own computer and
its own clock, so the two full-quality tracks start at different moments and
drift apart by the clocks' difference. The pipeline assumes they are aligned
(``transcribe``, ``edit`` and ``render`` cut both at the same episode times),
so this stage runs first.

It uses the reference files the call leaves on each side: the *other* side's
voice as received over the call, recorded on *this* side's clock, plus where
each reference starts inside its own side's main recording (``r``, from the
page; see the recorder's sidecar ``.json`` files). Cross-correlating the
guest's own track against the host's reference of it gives ``c1``; the host's
own track against the guest's reference gives ``c2``. With ``D`` the shift
from guest time to host time (``host time = guest time + D``) and ``L`` the
one-way call latency::

    c1 = D + L_to_host - r_host        c2 = -D + L_to_guest - r_guest
    D  = (c1 - c2 + r_host - r_guest) / 2      (latency cancels, NTP-style)

``D`` is measured near the start and near the end; their difference over the
time between them is the clock drift. With only one reference the latency
cannot cancel, and the stage says so (the error is the call's one-way delay).

The host track is the time base and stays as recorded. The guest track is
re-timed once (shifted by ``D``, stretched by the drift) into
``video editing/synced - guest.mp4``, and ``<package>/sync.json`` points the
episode at it, so every later stage reads aligned tracks unchanged.
``episode.json`` is never rewritten. A session without reference files
(a Riverside recording) is taken as aligned: the stage does nothing.

Correlation is GCC-PHAT at 8 kHz, band-limited to the telephone band the call
codec keeps, over speech-rich windows (silence correlates with anything: a
window is used only when its peak clears ``MIN_PEAK_RATIO`` over the median).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import numpy as np

from podcast.episode import SYNC_FILE, VIDEO_DIR, Episode, episode_path, work_dir
from podcast.media import read_wav, run_ffmpeg
from podcast.metrics import StageRecord

logger = logging.getLogger("podcast.sync")

OUTPUT = "synced - guest.mp4"
RATE = 8000
WINDOW_S = 20.0
SEARCH_S = 600.0        # how far apart the two sides may have pressed record
EDGE_S = 300.0          # start/end windows come from the first/last this much (at most a third)
END_SEARCH_S = 15.0     # the end window is searched around the start's lag (drift is small)
MIN_PEAK_RATIO = 8.0
BAND_HZ = (200.0, 3400.0)
FRAME_S = 0.05
MAX_WINDOWS = 8
NO_DRIFT_PPM = 2.0      # below this the drift (~7 ms an hour) is not corrected


class SyncError(RuntimeError):
    """No reliable alignment: the message names what was tried."""


@dataclass
class Reference:
    """The other side's voice as heard on ``side``, on ``side``'s clock."""
    path: Path
    side: str               # the side that recorded it
    offset_in_main_s: Optional[float]


# ── signal processing (pure) ────────────────────────────────────────────

def locate(window: np.ndarray, search: np.ndarray, rate: int = RATE) -> tuple[int, float]:
    """Where ``window`` best matches inside ``search`` (sample index of its
    start) and the peak's ratio over the median correlation (GCC-PHAT)."""
    n = len(search) + len(window)
    size = 1 << (n - 1).bit_length()
    spec = np.fft.rfft(search, size) * np.conj(np.fft.rfft(window, size))
    spec /= np.abs(spec) + 1e-12
    freqs = np.fft.rfftfreq(size, 1 / rate)
    spec[(freqs < BAND_HZ[0]) | (freqs > BAND_HZ[1])] = 0
    corr = np.fft.irfft(spec, size)[: len(search) - len(window) + 1]
    lag = int(np.argmax(corr))
    floor = float(np.median(np.abs(corr))) or 1e-12
    return lag, float(corr[lag] / floor)


def speech_windows(x: np.ndarray, rate: int = RATE, window_s: float = WINDOW_S,
                   limit: int = MAX_WINDOWS) -> list[float]:
    """Start times (s) of non-overlapping windows richest in speech: most
    frames within 20 dB of the loud level (the 95th percentile), but with
    pauses (constant sound correlates badly). Judged against the loud level,
    not a noise floor: in continuous speech the quietest frames are speech too."""
    hop = int(rate * FRAME_S)
    frames = len(x) // hop
    if frames == 0:
        return []
    rms = np.sqrt(np.mean(x[: frames * hop].reshape(frames, hop) ** 2, axis=1) + 1e-12)
    active = rms > max(0.1 * np.percentile(rms, 95), 1e-4)
    per = int(window_s / FRAME_S)
    if frames < per:
        return []
    score = np.convolve(active.astype(float), np.ones(per), "valid") / per
    score[(score > 0.97)] *= 0.5  # nothing but sound: likely noise, not speech
    picks: list[float] = []
    for i in np.argsort(-score):
        if score[i] < 0.15 or len(picks) >= limit:
            break
        if all(abs(i * FRAME_S - p) >= window_s for p in picks):
            picks.append(round(i * FRAME_S, 2))
    return picks


def solve(c1: Optional[float], c2: Optional[float], r_host: float, r_guest: float) -> tuple[float, str]:
    """``D`` (guest time → host time) from the two lags; see the module doc."""
    if c1 is not None and c2 is not None:
        return (c1 - c2 + r_host - r_guest) / 2, "both references"
    if c1 is not None:
        return c1 + r_host, "host reference only (off by the call's one-way delay)"
    if c2 is not None:
        return -c2 - r_guest, "guest reference only (off by the call's one-way delay)"
    raise SyncError("no reference could be aligned")


def at(points: list[tuple[float, float]], t: float) -> float:
    """The straight line through the first and last ``(time, value)`` points, at ``t``."""
    (t0, v0), (t1, v1) = points[0], points[-1]
    return v0 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def drift(points: list[tuple[float, float]]) -> float:
    """Clock drift (guest relative to host, as a fraction) from ``(host time, D)`` points."""
    if len(points) < 2:
        return 0.0
    (t0, d0), (t1, d1) = points[0], points[-1]
    rho = (d1 - d0) / (t1 - t0) if t1 > t0 else 0.0
    return 0.0 if abs(rho) * 1e6 < NO_DRIFT_PPM else rho


def retime_filters(d0: float, rho: float, fps: float) -> tuple[str, str]:
    """ffmpeg video and audio filters placing guest time on host time:
    ``host = (guest + d0) / (1 - rho)``."""
    stretch = 1 - rho
    video = (f"setpts=(PTS-STARTPTS+{d0:.6f}/TB)/{stretch:.9f},trim=start=0,"
             f"fps={fps:g}:start_time=0")
    shift = (f"adelay={d0 * 1000:.3f}:all=1" if d0 >= 0
             else f"atrim=start={-d0:.6f},asetpts=PTS-STARTPTS")
    audio = f"{shift},atempo={stretch:.9f},aresample=48000"
    return video, audio


# ── measuring a session ─────────────────────────────────────────────────

def _audio(path: Path, scratch: Path, name: str, start: float = 0.0, duration: Optional[float] = None) -> np.ndarray:
    wav = scratch / f"sync_{name}.wav"
    args = ["-ss", f"{max(start, 0):.3f}"] + (["-t", f"{duration:.3f}"] if duration else [])
    run_ffmpeg([*args, "-i", str(path), "-vn", "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(wav)])
    samples, _ = read_wav(wav)
    return samples.astype(np.float32) / 32768


def lag_points(ref: np.ndarray, own: np.ndarray, *, near_lag: Optional[float] = None,
               from_end: bool = False) -> list[tuple[float, float, float]]:
    """``(reference time, lag = reference time - own time, peak ratio)`` for
    the speech windows of ``ref`` in its first (or last) ``EDGE_S``, at most
    a third of it so the two ends never share a window. Each window is
    searched in ``own`` within ``SEARCH_S`` of the same time, or within
    ``END_SEARCH_S`` of ``near_lag`` when a lag is already known."""
    span = int(min(EDGE_S, len(ref) / RATE / 3) * RATE)
    base = (len(ref) - span) / RATE if from_end else 0.0
    part = ref[len(ref) - span:] if from_end else ref[:span]
    reach = END_SEARCH_S if near_lag is not None else SEARCH_S
    w = int(WINDOW_S * RATE)
    out = []
    for start in speech_windows(part):
        t_ref = base + start
        guess = t_ref - (near_lag or 0.0)
        lo = max(0, int((guess - reach) * RATE))
        hi = min(len(own), int((guess + reach) * RATE) + w)
        if hi - lo <= w:
            continue
        a = int(start * RATE)
        idx, ratio = locate(part[a:a + w], own[lo:hi])
        out.append((t_ref, t_ref - (lo + idx) / RATE, ratio))
    return out


def best(points: list[tuple[float, float, float]], label: str) -> tuple[float, float]:
    good = [p for p in points if p[2] >= MIN_PEAK_RATIO]
    if not good:
        ratios = ", ".join(f"{p[2]:.1f}" for p in points) or "no usable speech window"
        raise SyncError(f"{label}: no window cleared a peak ratio of {MIN_PEAK_RATIO} (got {ratios})")
    t, lag, ratio = max(good, key=lambda p: p[2])
    logger.info("ℹ️ %s: lag %.3f s at %.0f s (peak ratio %.0f)", label, lag, t, ratio)
    return t, lag


def estimate(refs: dict[str, np.ndarray], own: dict[str, np.ndarray], r: dict[str, float]) -> dict:
    """Offset (at host time 0), drift and how they were found, from each
    side's reference audio (``refs[side]``: the other side's voice heard on
    ``side``), each side's own audio, and each reference's start in its
    side's main recording (``r``)."""
    found: dict[str, dict] = {}
    failures: list[str] = []
    for side, ref_audio in refs.items():
        own_audio = own["guest" if side == "host" else "host"]
        try:
            start = best(lag_points(ref_audio, own_audio), f"{side} reference, start")
            end = best(lag_points(ref_audio, own_audio, near_lag=start[1], from_end=True), f"{side} reference, end")
            found[side] = {"start": start, "end": end}
        except SyncError as exc:
            failures.append(str(exc))
            logger.warning("⚠️ %s", exc)
    if not found:
        raise SyncError("; ".join(failures))
    # Each side's lag as a line over host time (drift moves it), so the two
    # sides are combined at the same instants, not at their own windows' times.
    series: dict[str, list[tuple[float, float]]] = {}
    if "host" in found:
        series["host"] = [(t + r.get("host", 0.0), lag) for t, lag in (found["host"]["start"], found["host"]["end"])]
    if "guest" in found:
        series["guest"] = [(t - lag, lag) for t, lag in (found["guest"]["start"], found["guest"]["end"])]
    times = [min(pts[0][0] for pts in series.values()), max(pts[-1][0] for pts in series.values())]
    points, method = [], ""
    for h in times:
        c1 = at(series["host"], h) if "host" in series else None
        c2 = at(series["guest"], h) if "guest" in series else None
        d, method = solve(c1, c2, r.get("host", 0.0), r.get("guest", 0.0))
        points.append((h, d))
    rho = drift(points)
    report = {"offset_s": round(points[0][1] - rho * points[0][0], 4), "drift_ppm": round(rho * 1e6, 2),
              "method": method, "points": [[round(t, 2), round(d, 4)] for t, d in points]}
    if len(found) == 2:
        report["round_trip_s"] = round(found["host"]["start"][1] + found["guest"]["start"][1]
                                       + r.get("host", 0.0) + r.get("guest", 0.0), 3)
    return report


def measure(refs: dict[str, Reference], tracks: dict[str, Path], scratch: Path) -> dict:
    """``estimate`` over the session's files."""
    ref_audio = {side: _audio(ref.path, scratch, f"ref_{side}") for side, ref in refs.items()}
    voices = {"guest" if side == "host" else "host" for side in refs}
    own_audio = {voice: _audio(tracks[voice], scratch, f"own_{voice}") for voice in voices}
    return estimate(ref_audio, own_audio, {side: ref.offset_in_main_s or 0.0 for side, ref in refs.items()})


# ── stage ───────────────────────────────────────────────────────────────

def references(ep: Episode) -> dict[str, Reference]:
    """The first reference each side recorded beside the episode's main
    track (the recorder's sidecars say which belongs to which)."""
    mains = {}
    for side, track in ep.tracks.items():
        sidecar = track.with_suffix(".json")
        if sidecar.exists():
            mains[side] = json.loads(sidecar.read_text(encoding="utf-8")).get("rid")
    found: dict[str, Reference] = {}
    folder = next(iter(ep.tracks.values())).parent
    for sidecar in sorted(folder.glob("recorder - * remote-ref - *.json")):
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        side = meta.get("side")
        if meta.get("kind") != "ref" or side not in mains or meta.get("main") != mains[side] or side in found:
            continue
        found[side] = Reference(sidecar.with_suffix(".mp4"), side, meta.get("offset_in_main_s"))
    return found


def video_fps(path: Path) -> float:
    from podcast.media import _tool, NO_WINDOW  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    out = subprocess.run([_tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=avg_frame_rate", "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, creationflags=NO_WINDOW).stdout.strip()
    num, _, den = out.partition("/")
    try:
        fps = float(num) / float(den or 1)
    except ValueError:
        fps = 30.0
    return round(fps) if abs(fps - round(fps)) < 0.1 else round(fps, 3)


def retime(src: Path, dst: Path, d0: float, rho: float, encoder: str) -> None:
    video, audio = retime_filters(d0, rho, video_fps(src))
    venc = (["-c:v", encoder, "-preset", "p6", "-rc", "vbr", "-cq", "18", "-b:v", "0"] if "nvenc" in encoder
            else ["-c:v", "libx264", "-preset", "medium", "-crf", "16"])
    run_ffmpeg(["-i", str(src), "-filter_complex", f"[0:v]{video}[v];[0:a]{audio}[a]", "-map", "[v]", "-map", "[a]",
                *venc, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", str(dst)],
               timeout=4 * 3600)


def source_tracks(ep: Episode) -> dict[str, Path]:
    """The tracks ``episode.json`` names, before any ``sync.json`` swap. A re-run
    must read these: after a first sync ``ep.tracks["guest"]`` is the synced
    file itself, so re-timing it would write over its own input (and measuring
    it would report ~zero offset)."""
    if not (ep.package / SYNC_FILE).exists():
        return ep.tracks
    spec = json.loads((ep.folder / "episode.json").read_text(encoding="utf-8"))
    return {k: ep.folder / v for k, v in spec["tracks"].items()}


def run(ep: Episode, cfg: dict, rec: StageRecord) -> Optional[dict]:
    ep = replace(ep, tracks=source_tracks(ep))
    refs = references(ep)
    if not refs:
        logger.info("ℹ️ sync: no recorder reference files beside the tracks; taking them as aligned")
        return None
    report = measure(refs, ep.tracks, work_dir(cfg, ep))
    if "only" in report["method"]:
        logger.warning("⚠️ sync from the %s", report["method"])
    logger.info("ℹ️ sync: guest → host offset %.3f s, drift %.1f ppm (%s)%s", report["offset_s"],
                report["drift_ppm"], report["method"],
                f", call round trip {report['round_trip_s']:.3f} s" if "round_trip_s" in report else "")
    # Into this episode's own folder, never beside a source track it may only
    # read (a trial episode points at another folder's recording, #350).
    dst = ep.folder / VIDEO_DIR / OUTPUT
    dst.parent.mkdir(parents=True, exist_ok=True)
    retime(ep.tracks["guest"], dst, report["offset_s"], report["drift_ppm"] / 1e6, cfg.get("video_encoder", "libx264"))
    report["tracks"] = {"host": episode_path(ep.folder, ep.tracks["host"]), "guest": episode_path(ep.folder, dst)}
    ep.package.mkdir(parents=True, exist_ok=True)
    (ep.package / SYNC_FILE).write_text(json.dumps(report, indent=1), encoding="utf-8")
    logger.info("✅ sync: %s written; the episode now reads %s", SYNC_FILE, OUTPUT)
    return report
