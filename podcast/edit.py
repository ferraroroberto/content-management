"""Stage: per-clip edit decisions — the caption words, their LLM review, the
jump-cut list and the 1:1 camera plan (issue #335).

* **Caption words** come from a whisper pass over the clip's own mixed audio
  (both tracks): where both people talk at once the per-track pass can lose a
  sentence and stretch the next words over the gap.
* **Cut list.** Pauses are found on the audio, not on whisper's word times,
  which drift up to ~0.3 s into the silence around them. A silence longer
  than ``MAX_PAUSE_S`` shrinks to ``KEEP_PAUSE_S``, so every cut lands in
  silence, between words. A short sound flanked by silences is a filler
  island when its words are all fillers, or when it carries no word and an
  isolated decode of it hears nothing but a filler: whisper writes almost no
  "um"/"uh", and in the pilot most untranscribed islands were real words
  with misplaced times, so they are only cut once that decode says filler.
* **Opener.** A leading "And…/So, yeah…/But then…" is cut so the clip
  opens on its hook. It is read from this decode, not at selection: the
  per-track pass's bleed gate often drops exactly those short first words.
* **Camera plan (1:1).** The crop follows whoever is speaking (per-track
  words, so the speaker is known) and changes framing on a jump cut or
  after ``MAX_SHOT_S`` of one speaker, the way the owner's published
  episodes punch in and out.

Times in ``keep`` and the shots are episode seconds on a 1/``FPS`` grid
from the clip start, so the video and audio trims cut on the same frames.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np

from podcast.clips import load_clips, save_clips
from podcast.episode import Episode, work_dir
from podcast.media import extract_mix_wav, read_wav, write_wav
from podcast.metrics import StageRecord
from podcast.review import REVIEW_FILE, review_clip
from podcast.transcribe import (_runs, drop_backchannel, find_loops, load_words, segment_words,
                                whisper_segments, words_between)

logger = logging.getLogger("podcast.edit")

FPS = 24
CLIP_WORDS_FILE = "transcript/clip_words.json"

FRAME_S = 0.01
SILENCE_DB = 30.0       # below the clip's loud speech (95th percentile) by this much = silence
MIN_SILENCE_S = 0.12
MAX_PAUSE_S = 0.4       # longer pauses are cut down ...
KEEP_PAUSE_S = 0.24     # ... to this
LEAD_S = 0.1            # silence kept before the first word
TAIL_S = 0.3            # and after the last
MAX_ISLAND_S = 1.0

FILLERS = frozenset({"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "mhm"})
OPENERS = FILLERS | {"and", "so", "but", "yeah", "yes", "well", "absolutely", "exactly", "okay", "ok",
                     "right", "oh"}
MAX_OPENER_WORDS = 4
_CORE = re.compile(r"[^\w']+")

MIN_TURN_S = 1.5        # a shorter turn of the other speaker does not move the camera
MIN_TURN_WORDS = 4
MIN_SHOT_S = 2.0        # a jump cut changes framing only after this long on one shot
MAX_SHOT_S = 6.0        # one framing at most this long
ZOOMS = (1.0, 1.18, 1.08)


def core(word: str) -> str:
    return _CORE.sub("", word.lower())


def is_filler(word: dict) -> bool:
    return core(word["w"]) in FILLERS


# ── audio: silences and filler islands ──────────────────────────────────

def silences(samples: np.ndarray, rate: int, *, offset: float = 0.0) -> list[tuple[float, float]]:
    """Silent runs ≥ ``MIN_SILENCE_S``, relative to the clip's loud speech."""
    hop = int(rate * FRAME_S)
    n = len(samples) // hop
    if not n:
        return []
    frames = samples[: n * hop].astype(np.float32).reshape(n, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-3)
    silent = db < np.percentile(db, 95) - SILENCE_DB
    runs, i = [], 0
    while i < n:
        j = i
        while j < n and silent[j] == silent[i]:
            j += 1
        if silent[i] and (j - i) * FRAME_S >= MIN_SILENCE_S:
            runs.append((round(offset + i * FRAME_S, 2), round(offset + j * FRAME_S, 2)))
        i = j
    return runs


def islands(sil: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Short sounds flanked by silences on both sides."""
    return [(a[1], b[0]) for a, b in zip(sil, sil[1:]) if b[0] - a[1] <= MAX_ISLAND_S]


def words_in(words: list[dict], a: float, b: float) -> list[dict]:
    """Words whose midpoint falls inside ``[a, b]``."""
    return [w for w in words if a <= (w["s"] + w["e"]) / 2 <= b]


def _you_know(words: list[dict]) -> bool:
    return [core(w["w"]) for w in words] == ["you", "know"]


def classify_islands(isl: list[tuple[float, float]], words: list[dict]) -> tuple[list, list]:
    """``(fillers, unknown)``: islands whose words are all fillers (or a bare
    "you know"), and islands with no word at all, which need a listen."""
    fillers, unknown = [], []
    for a, b in isl:
        inside = words_in(words, a - 0.05, b + 0.05)
        if not inside:
            unknown.append((a, b))
        elif all(is_filler(w) for w in inside) or _you_know(inside):
            fillers.append((a, b))
    return fillers, unknown


def heard_as_filler(text: str) -> bool:
    """An isolated decode of an island: nothing, or only filler sounds."""
    return all(core(t) in FILLERS for t in text.split() if core(t))


def opener_count(words: list[dict]) -> int:
    """How many leading words are an opener ("And…", "So, yeah…", "But then…")."""
    k = 0
    while k < min(MAX_OPENER_WORDS, len(words) - 1):
        word = core(words[k]["w"])
        if word not in OPENERS and not (word == "then" and k and core(words[k - 1]["w"]) in ("and", "but")):
            break
        k += 1
    return k


def opener_cut(words: list[dict], k: int, sil: list[tuple[float, float]],
               start: float) -> Optional[tuple[float, float]]:
    """The region to remove for ``k`` opener words: up to the silence before
    the first kept word when there is one, else up to the word boundary
    (padded by ``LEAD_S``, which the edge rule of ``cut_list`` gives back)."""
    if not k:
        return None
    last, first = words[k - 1], words[k]
    gaps = [s for s in sil if last["s"] <= s[1] and s[0] <= first["s"] + 0.2]
    gap = max(gaps, default=None)
    if gap and gap[0] > start:
        return start, gap[0]
    return start, (last["e"] + first["s"]) / 2 + LEAD_S


# ── cut list and time remap ─────────────────────────────────────────────

def _grid(t: float, start: float) -> float:
    return round(start + round((t - start) * FPS) / FPS, 4)


def cut_list(start: float, end: float, sil: list[tuple[float, float]],
             fillers: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Kept spans of ``[start, end]``: long pauses shortened, filler islands
    removed together with the silences around them, edges trimmed."""
    regions = sorted([(a, b, False) for a, b in sil] + [(a, b, True) for a, b in fillers])
    merged: list[list] = []
    for a, b, filler in regions:
        if merged and a <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], b)
            merged[-1][2] = merged[-1][2] or filler
        else:
            merged.append([a, b, filler])
    cuts: list[tuple[float, float]] = []
    for a, b, filler in merged:
        a, b = max(a, start), min(b, end)
        if a <= start + 0.02:
            cut = (start, b - LEAD_S)
        elif b >= end - 0.02:
            cut = (a + TAIL_S, end)
        elif filler or b - a > MAX_PAUSE_S:
            cut = (a + KEEP_PAUSE_S / 2, b - KEEP_PAUSE_S / 2)
        else:
            continue
        if cut[1] - cut[0] >= 1 / FPS:
            cuts.append(cut)
    keep, t = [], start
    for a, b in cuts:
        keep.append((t, a))
        t = b
    keep.append((t, end))
    keep = [(_grid(a, start), _grid(b, start)) for a, b in keep]
    return [(a, b) for a, b in keep if b - a >= 2 / FPS]


def remap(t: float, keep: list[tuple[float, float]]) -> float:
    """Episode time → time in the cut clip; a time inside a cut lands on the next kept span."""
    out = 0.0
    for a, b in keep:
        if t < a:
            return round(out, 3)
        if t <= b:
            return round(out + t - a, 3)
        out += b - a
    return round(out, 3)


def cut_words(words: list[dict], keep: list[tuple[float, float]]) -> list[dict]:
    """Caption words on the cut clip's timeline: fillers dropped, the rest remapped."""
    out = []
    for w in words:
        if is_filler(w) or not keep[0][0] <= w["s"] < keep[-1][1]:
            continue
        s, e = remap(w["s"], keep), remap(w["e"], keep)
        out.append({**w, "s": s, "e": max(e, s + 0.05)})
    return out


def kept_seconds(keep: list[tuple[float, float]]) -> float:
    return round(sum(b - a for a, b in keep), 2)


# ── 1:1 camera plan ─────────────────────────────────────────────────────

def speaker_switches(words: list[dict], start: float, end: float, first: str) -> list[tuple[float, str]]:
    """``(time, speaker)`` from which the camera shows that speaker; turns
    shorter than ``MIN_TURN_S`` or ``MIN_TURN_WORDS`` don't move it (whisper
    can stretch a lone "hmm" over seconds). ``first`` is the fallback for a
    clip with no turn that long."""
    inside = drop_backchannel([w for w in words if start <= w["s"] < end and not is_filler(w)])
    turns = [r for r in _runs(inside, gap_s=1e9)
             if len(r) >= MIN_TURN_WORDS and r[-1]["e"] - r[0]["s"] >= MIN_TURN_S]
    switches = [(start, turns[0][0]["spk"] if turns else first)]
    for run in turns[1:]:
        if run[0]["spk"] != switches[-1][1]:
            switches.append((max(start, run[0]["s"] - 0.15), run[0]["spk"]))
    return switches


def shots(keep: list[tuple[float, float]], switches: list[tuple[float, str]],
          words: list[dict]) -> list[dict]:
    """Split the kept spans into shots ``{a, b, spk, zoom}``.

    A shot ends at a speaker switch; the framing changes at a jump cut once it
    has run ``MIN_SHOT_S``, and at a word start before it runs past
    ``MAX_SHOT_S``. Framings cycle through ``ZOOMS`` per speaker."""
    start = keep[0][0]
    marks = sorted({_grid(t, start) for t, _ in switches[1:]})
    word_starts = sorted({_grid(w["s"], start) for w in words})

    def speaker_at(t: float) -> str:
        return [spk for at, spk in switches if at <= t + 1e-6][-1]

    out: list[dict] = []
    framing: dict[str, int] = {}
    shown = 0.0  # seconds on the current framing

    def emit(a: float, b: float, spk: str) -> None:
        nonlocal shown
        out.append({"a": a, "b": b, "spk": spk, "zoom": ZOOMS[framing.setdefault(spk, 0) % len(ZOOMS)]})
        shown += b - a

    def reframe(spk: str) -> None:
        nonlocal shown
        framing[spk] = framing.get(spk, 0) + 1
        shown = 0.0

    for a, b in keep:
        pieces = [a, *(m for m in marks if a < m < b), b]
        for i, (t, stop) in enumerate(zip(pieces, pieces[1:])):
            spk = speaker_at(t)
            if not out or out[-1]["spk"] != spk:
                shown = 0.0
            elif i == 0 and shown >= MIN_SHOT_S:
                reframe(spk)  # a jump cut: hide it with a new framing
            while stop - t + shown > MAX_SHOT_S:
                due = t + MAX_SHOT_S - shown
                split = [x for x in word_starts if due - 1.5 <= x <= due and t < x < stop - 1.0]
                if not split:
                    break
                emit(t, split[-1], spk)
                reframe(spk)
                t = split[-1]
            emit(t, stop, spk)
    return out


# ── stage ───────────────────────────────────────────────────────────────

def load_clip_words(ep: Episode) -> dict[str, list[dict]]:
    """Caption words per clip number (as a string key), episode times, review applied."""
    path = ep.package / CLIP_WORDS_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def decode_clip(cfg: dict, clip: dict, wav: Path, episode_words: list[dict], rec: StageRecord) -> list[dict]:
    """Caption words from a whisper pass over the clip's mixed audio; a decoder
    loop falls back to the episode's per-track words."""
    words = [w for seg in whisper_segments(cfg["whisper_url"], wav, rec)
             for w in segment_words(seg, "mix", offset=clip["start"])]
    if not words or find_loops(words):
        logger.warning("⚠️ clip %d: caption pass %s, using the episode transcript", clip["number"],
                       "looped" if words else "was empty")
        return words_between(episode_words, clip["start"], clip["end"])
    return words


def _listen(cfg: dict, samples: np.ndarray, rate: int, a: float, b: float, scratch: Path,
            rec: StageRecord) -> str:
    """Decode one island on its own, padded with silence."""
    pad = np.zeros(int(0.4 * rate), dtype=np.int16)
    snippet = samples[max(0, int((a - 0.03) * rate)):int((b + 0.03) * rate)]
    wav = write_wav(scratch / "island.wav", np.concatenate([pad, snippet, pad]), rate)
    return " ".join(seg.get("text", "") for seg in whisper_segments(cfg["whisper_url"], wav, rec)).strip()


def edit_clip(cfg: dict, clip: dict, tracks: list[Path], episode_words: list[dict], scratch: Path,
              rec: StageRecord) -> list[dict]:
    """Decode the clip, find its cuts and camera plan; returns the caption words."""
    wav = extract_mix_wav(tracks, scratch / f"cap_{clip['number']:02d}.wav",
                          start=clip["start"], duration=clip["end"] - clip["start"])
    words = decode_clip(cfg, clip, wav, episode_words, rec)
    samples, rate = read_wav(wav)
    sil = silences(samples, rate, offset=clip["start"])
    k = opener_count(words)
    opener = opener_cut(words, k, sil, clip["start"])
    opener_text, words = " ".join(w["w"] for w in words[:k]), words[k:]
    fillers, unknown = classify_islands(islands(sil), words)
    for a, b in unknown:
        heard = _listen(cfg, samples, rate, a - clip["start"], b - clip["start"], scratch, rec)
        if heard_as_filler(heard):
            fillers.append((a, b))
        logger.debug("island %.2f-%.2f heard as %r", a, b, heard)
    keep = cut_list(clip["start"], clip["end"], sil, fillers + ([opener] if opener else []))
    switches = speaker_switches(episode_words, keep[0][0], clip["end"], clip["speaker"])
    clip["keep"] = [list(span) for span in keep]
    clip["shots"] = shots(keep, switches, [w for w in words if not is_filler(w)])
    clip["edit"] = {"source_s": round(clip["end"] - clip["start"], 2), "cut_s": kept_seconds(keep),
                    "jump_cuts": len(keep) - 1, "fillers_cut": len(fillers), "opener_cut": opener_text,
                    "speaker_switches": len(switches) - 1}
    logger.info("ℹ️ clip %d: %.1fs → %.1fs, %d jump cuts, %d filler(s), opener %r, %d shots",
                clip["number"], clip["edit"]["source_s"], clip["edit"]["cut_s"], len(keep) - 1,
                len(fillers), opener_text, len(clip["shots"]))
    return words


def run(ep: Episode, cfg: dict, rec: StageRecord) -> list[dict]:
    """Edit decisions for every clip, then the caption review (in parallel)."""
    clips = load_clips(ep)
    episode_words = load_words(ep)
    scratch = work_dir(cfg, ep)
    tracks = [ep.tracks["guest"], ep.tracks["host"]]
    decoded = {str(c["number"]): edit_clip(cfg, c, tracks, episode_words, scratch, rec) for c in clips}
    with ThreadPoolExecutor(max_workers=3) as pool:
        reviews = list(pool.map(lambda c: review_clip(ep, cfg, rec, c, decoded[str(c["number"])],
                                                      episode_words), clips))
    clip_words = {}
    for clip, (words, review) in zip(clips, reviews):
        clip_words[str(clip["number"])] = words
        clip["caption_review"] = review
    (ep.package / CLIP_WORDS_FILE).parent.mkdir(parents=True, exist_ok=True)
    (ep.package / CLIP_WORDS_FILE).write_text(json.dumps(clip_words, ensure_ascii=False), encoding="utf-8")
    (ep.package / REVIEW_FILE).write_text(json.dumps({c["number"]: c["caption_review"] for c in clips},
                                                     indent=1, ensure_ascii=False), encoding="utf-8")
    save_clips(ep, clips)
    logger.info("✅ edit decisions and caption review for %d clips", len(clips))
    return clips


def output_words(clip: dict, words: Optional[list[dict]]) -> list[dict]:
    """A clip's caption words on its cut timeline (uncut when it has no cut list)."""
    keep = clip.get("keep") or [[clip["start"], clip["end"]]]
    return cut_words(words or [], [tuple(span) for span in keep])
