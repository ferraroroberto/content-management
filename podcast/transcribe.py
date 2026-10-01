"""Stage: per-track transcription through the local whisper server.

Riverside records one track per speaker, but each microphone also picks up
the other voice. Sent raw, whisper transcribes that bleed and, worse, loops
on it ("I will concede on this" ×325 in the spike). So:

1. **Bleed gate** — mute a track's frames where the other track is much
   louder (it is the other person talking), with a short hold so word
   edges survive.
2. **Silence filter** — drop whisper segments that sit mostly on muted audio
   (hallucinations over silence).
3. **Loop repair** — find windows where an n-gram repeats; re-transcribe just
   that window from the ungated audio, then collapse any verbatim repeat run
   that still remains.
4. **Turns** — interleave both speakers' words by time and fold the other
   speaker's one-word backchannels ("yeah") out of the reading transcript.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

import numpy as np
import requests

from podcast.episode import SPEAKERS, Episode, work_dir
from podcast.media import extract_wav, read_wav, write_wav
from podcast.metrics import StageRecord

logger = logging.getLogger("podcast.transcribe")

FRAME_MS = 50
BLEED_RATIO = 1.8
HOLD_MS = 200
MUTED_SEGMENT_MAX = 0.8
LOOP_N = 5
LOOP_MIN_REPEATS = 4
LOOP_WINDOW_S = 120.0
LOOP_PAD_S = 3.0

_PUNCT_ONLY = re.compile(r"^[^\w]+$")
_NORM = re.compile(r"[^\w']+")


def _norm(word: str) -> str:
    return _NORM.sub("", word.lower())


# ── bleed gate ──────────────────────────────────────────────────────────

def frame_rms(samples: np.ndarray, rate: int, frame_ms: int = FRAME_MS) -> np.ndarray:
    hop = int(rate * frame_ms / 1000)
    n = len(samples) // hop
    frames = samples[: n * hop].astype(np.float32).reshape(n, hop)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-9)


def keep_mask(own_rms: np.ndarray, other_rms: np.ndarray, *, ratio: float = BLEED_RATIO,
              hold_frames: int = HOLD_MS // FRAME_MS) -> np.ndarray:
    """True where ``own`` should be kept: not dominated by the other track,
    dilated by ``hold_frames`` either side so word onsets/tails survive."""
    n = min(len(own_rms), len(other_rms))
    keep = ~(other_rms[:n] > ratio * own_rms[:n])
    if hold_frames:
        kernel = np.ones(2 * hold_frames + 1)
        keep = np.convolve(keep.astype(float), kernel, mode="same") > 0
    return keep


def apply_mask(samples: np.ndarray, keep: np.ndarray, rate: int,
               frame_ms: int = FRAME_MS) -> np.ndarray:
    hop = int(rate * frame_ms / 1000)
    gain = np.repeat(keep.astype(np.int16), hop)
    out = samples.copy()
    n = min(len(out), len(gain))
    out[:n] = out[:n] * gain[:n]
    out[n:] = 0
    return out


def muted_fraction(keep: np.ndarray, start: float, end: float,
                   frame_ms: int = FRAME_MS) -> float:
    a = int(start * 1000 / frame_ms)
    b = max(a + 1, int(end * 1000 / frame_ms))
    window = keep[a:b]
    return 1.0 if window.size == 0 else float(1.0 - window.mean())


# ── whisper ─────────────────────────────────────────────────────────────

def whisper_segments(url: str, wav: Path, rec: StageRecord, *, language: str = "en") -> list[dict]:
    """POST a WAV to the whisper server; return ``verbose_json`` segments."""
    t0 = time.perf_counter()
    with open(wav, "rb") as fh:
        resp = requests.post(
            url,
            files={"file": (wav.name, fh, "audio/wav")},
            data={"response_format": "verbose_json", "timestamp_granularities[]": "word",
                  "language": language, "temperature": "0"},
            timeout=1800,
        )
    resp.raise_for_status()
    rec.add_whisper(time.perf_counter() - t0)
    return resp.json().get("segments") or []


def segment_words(segment: dict, speaker: str, offset: float = 0.0) -> list[dict]:
    """Word dicts ``{w, s, e, spk}``.

    whisper.cpp's "words" are sub-word tokens: a token without a leading space
    continues the previous word (``" don"`` + ``"'t"``, ``" U"`` + ``"plo"`` +
    ``"ad"``), and punctuation gets its own, meaningless, timestamps. Both
    fold into the previous word, which keeps its start and takes the end.
    """
    words: list[dict] = []
    for tok in segment.get("words") or []:
        raw = tok.get("word") or ""
        text = raw.strip()
        if not text:
            continue
        if words and (_PUNCT_ONLY.match(text) or not raw[:1].isspace()):
            words[-1]["w"] += text
            if not _PUNCT_ONLY.match(text):
                words[-1]["e"] = round(tok["end"] + offset, 2)
            continue
        words.append({"w": text, "s": round(tok["start"] + offset, 2),
                      "e": round(tok["end"] + offset, 2), "spk": speaker})
    return words


# ── loop repair ─────────────────────────────────────────────────────────

def find_loops(words: list[dict], *, n: int = LOOP_N, min_repeats: int = LOOP_MIN_REPEATS,
               window_s: float = LOOP_WINDOW_S) -> list[tuple[float, float]]:
    """Time spans where one n-gram occurs ``min_repeats``+ times within ``window_s``."""
    norm = [_norm(w["w"]) for w in words]
    seen: dict[tuple, list[int]] = {}
    for i in range(len(words) - n + 1):
        gram = tuple(norm[i:i + n])
        # Backchannel runs ("yeah yeah yeah …") are speech, not decoder loops.
        if all(gram) and len(set(gram)) >= 3:
            seen.setdefault(gram, []).append(i)
    spans: list[tuple[float, float]] = []
    for idxs in seen.values():
        if len(idxs) < min_repeats:
            continue
        for k in range(len(idxs) - min_repeats + 1):
            first, last = idxs[k], idxs[k + min_repeats - 1]
            if words[last]["s"] - words[first]["s"] <= window_s:
                spans.append((words[first]["s"], words[last + n - 1]["e"]))
    spans.sort()
    merged: list[tuple[float, float]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def collapse_repeats(words: list[dict], *, max_n: int = 8) -> list[dict]:
    """Drop immediately repeated word runs (``a b c a b c`` → ``a b c``), longest first."""
    out = list(words)
    for n in range(max_n, 0, -1):
        i, kept = 0, []
        while i < len(out):
            if (len(kept) >= n and i + n <= len(out)
                    and [_norm(w["w"]) for w in kept[-n:]] == [_norm(w["w"]) for w in out[i:i + n]]
                    and (n > 1 or len(_norm(out[i]["w"])) > 3)):
                i += n
                continue
            kept.append(out[i])
            i += 1
        out = kept
    return out


# ── turns ───────────────────────────────────────────────────────────────

def _runs(words: list[dict], gap_s: float) -> list[list[dict]]:
    runs: list[list[dict]] = []
    for w in sorted(words, key=lambda w: w["s"]):
        if runs and runs[-1][-1]["spk"] == w["spk"] and w["s"] - runs[-1][-1]["e"] <= gap_s:
            runs[-1].append(w)
        else:
            runs.append([w])
    return runs


def drop_backchannel(words: list[dict], *, gap_s: float = 2.0, max_words: int = 3,
                     max_s: float = 1.5) -> list[dict]:
    """Remove the other speaker's short interjection ("yeah") when it splits a turn."""
    runs = _runs(words, gap_s)
    kept: list[dict] = []
    for i, run in enumerate(runs):
        prev_spk = runs[i - 1][0]["spk"] if i > 0 else None
        next_spk = runs[i + 1][0]["spk"] if i + 1 < len(runs) else None
        short = len(run) <= max_words and run[-1]["e"] - run[0]["s"] <= max_s
        if short and prev_spk == next_spk and prev_spk not in (None, run[0]["spk"]):
            continue
        kept.extend(run)
    return kept


def build_turns(words: list[dict], *, gap_s: float = 2.0) -> list[dict]:
    """Group time-ordered words into speaker turns for the reading transcript."""
    turns: list[list[dict]] = []
    for w in drop_backchannel(words, gap_s=gap_s):
        if turns and turns[-1][-1]["spk"] == w["spk"]:
            turns[-1].append(w)
        else:
            turns.append([w])
    return [{"speaker": t[0]["spk"], "start": t[0]["s"], "end": t[-1]["e"],
             "text": " ".join(w["w"] for w in t)} for t in turns]


def sentences(words: list[dict], *, gap_s: float = 1.5) -> list[dict]:
    """Split time-ordered words into per-speaker sentences ``{speaker, start, end, text}``."""
    out: list[list[dict]] = []
    for w in drop_backchannel(words):
        cur = out[-1] if out else None
        if (cur and cur[-1]["spk"] == w["spk"] and w["s"] - cur[-1]["e"] <= gap_s
                and not cur[-1]["w"].endswith((".", "?", "!"))):
            cur.append(w)
        else:
            out.append([w])
    return [{"speaker": s[0]["spk"], "start": s[0]["s"], "end": s[-1]["e"],
             "text": " ".join(w["w"] for w in s)} for s in out]


def fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ── stage ───────────────────────────────────────────────────────────────

def _transcribe_track(url: str, speaker: str, gated: Path, raw: np.ndarray, rate: int,
                      keep: np.ndarray, scratch: Path, rec: StageRecord) -> list[dict]:
    words: list[dict] = []
    dropped = 0
    for seg in whisper_segments(url, gated, rec):
        if muted_fraction(keep, seg["start"], seg["end"]) > MUTED_SEGMENT_MAX:
            dropped += 1
            continue
        words.extend(segment_words(seg, speaker))
    logger.info("ℹ️ %s: %d words, %d segments dropped as over-silence", speaker, len(words), dropped)

    for start, end in find_loops(words):
        a, b = max(0.0, start - LOOP_PAD_S), end + LOOP_PAD_S
        logger.warning("⚠️ %s: loop %s–%s, re-transcribing ungated", speaker, fmt_ts(a), fmt_ts(b))
        cut = write_wav(scratch / f"loop_{speaker}_{int(a)}.wav", raw[int(a * rate):int(b * rate)], rate)
        redo: list[dict] = []
        for seg in whisper_segments(url, cut, rec):
            redo.extend(segment_words(seg, speaker, offset=a))
        # Ungated audio carries the other speaker's bleed: keep only words
        # spoken where this track is the dominant one.
        redo = [w for w in redo if muted_fraction(keep, w["s"], w["e"]) < 0.5]
        words = [w for w in words if not (a <= w["s"] < b)] + redo
        words.sort(key=lambda w: w["s"])
    before = len(words)
    words = collapse_repeats(words)
    if before != len(words):
        logger.info("ℹ️ %s: collapsed %d repeated words", speaker, before - len(words))
    return words


def run(ep: Episode, cfg: dict, rec: StageRecord) -> Path:
    """Transcribe both tracks; write ``transcript/words.json`` + ``turns.json`` + raw md."""
    scratch = work_dir(cfg, ep)
    out_dir = ep.package / "transcript"
    out_dir.mkdir(parents=True, exist_ok=True)

    audio: dict[str, tuple[np.ndarray, int]] = {}
    for spk in SPEAKERS:
        wav = scratch / f"{spk}.wav"
        if not wav.exists():
            logger.info("ℹ️ extracting %s audio", spk)
            extract_wav(ep.tracks[spk], wav)
        audio[spk] = read_wav(wav)

    rms = {spk: frame_rms(*audio[spk]) for spk in SPEAKERS}
    words: list[dict] = []
    for spk in SPEAKERS:
        other = "host" if spk == "guest" else "guest"
        samples, rate = audio[spk]
        keep = keep_mask(rms[spk], rms[other])
        gated = write_wav(scratch / f"{spk}_gated.wav", apply_mask(samples, keep, rate), rate)
        logger.info("ℹ️ %s: gate keeps %.0f%% of frames", spk, 100 * keep.mean())
        words.extend(_transcribe_track(cfg["whisper_url"], spk, gated, samples, rate, keep, scratch, rec))

    words.sort(key=lambda w: w["s"])
    turns = build_turns(words)
    (out_dir / "words.json").write_text(json.dumps(words), encoding="utf-8")
    (out_dir / "turns.json").write_text(json.dumps(turns, indent=1), encoding="utf-8")
    lines = [f"[{fmt_ts(t['start'])}] {ep.label(t['speaker'])}: {t['text']}" for t in turns]
    (out_dir / "raw transcript.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    logger.info("✅ transcript: %d words, %d turns", len(words), len(turns))
    return out_dir


def load_words(ep: Episode) -> list[dict]:
    return json.loads((ep.package / "transcript" / "words.json").read_text(encoding="utf-8"))


def load_turns(ep: Episode, *, windowed: bool = True) -> list[dict]:
    turns = json.loads((ep.package / "transcript" / "turns.json").read_text(encoding="utf-8"))
    if windowed:
        turns = [t for t in turns if ep.in_window(t["start"], t["end"])]
    return turns


def words_between(words: list[dict], start: float, end: float,
                  speaker: Optional[str] = None) -> list[dict]:
    return [w for w in words if start <= w["s"] < end and (speaker is None or w["spk"] == speaker)]
