"""Stage: per-clip quality score against a short rubric (each 1-5).

* **hook** — does the first 3 s grab? (LLM, text of the first 3 s)
* **self_contained** — one idea a stranger can follow? (LLM, clip text)
* **caption_accuracy** — burned caption words vs a fresh whisper pass over
  the rendered clip's audio (word error rate, deterministic)
* **framing_1x1 / framing_9x16** — one frame of each render judged by a
  vision call (face in frame, headroom, captions clear of the face)
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re

from PIL import Image

from podcast.captions import caption_text
from podcast.clips import load_clips, save_clips, write_clips_md
from podcast.episode import Episode, work_dir
from podcast.hub import ask_json
from podcast.media import extract_wav, frame_at
from podcast.metrics import StageRecord
from podcast.report import SCORES_FILE
from podcast.transcribe import load_words, segment_words, whisper_segments

logger = logging.getLogger("podcast.score")

_TOKEN = re.compile(r"[a-z0-9']+")

TEXT_PROMPT = """Score each social video clip from a podcast, 1 (poor) to 5 (excellent):
- "hook": do the words spoken in the first 3 seconds make a scroller stop? (a claim, question, tension or story opener scores high; a filler start or a reference back scores low)
- "self_contained": is it one complete idea that a stranger with no context can follow?
Be strict; 3 is average. Reply with JSON only: a list of {{"number", "hook", "self_contained", "note": "<max 12 words>"}}.

{clips}
"""

FRAMING_PROMPT = ("These are two frames of the same social video clip: first the 1:1 crop, then the 9:16 "
                  "stacked crop. Score the framing of each, 1 (poor) to 5 (excellent): faces fully in frame "
                  "and not cut, sensible headroom, nobody pushed to the edge, captions not covering a face. "
                  'Reply with JSON only: {"framing_1x1": n, "framing_9x16": n, "note": "<max 12 words>"}')


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower().replace("’", "'"))


def word_error_rate(reference: list[str], hypothesis: list[str]) -> float:
    """Levenshtein distance over words, divided by the reference length."""
    if not reference:
        return 0.0 if not hypothesis else 1.0
    prev = list(range(len(hypothesis) + 1))
    for i, ref in enumerate(reference, 1):
        cur = [i] + [0] * len(hypothesis)
        for j, hyp in enumerate(hypothesis, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ref != hyp))
        prev = cur
    return prev[-1] / len(reference)


def wer_to_score(wer: float) -> int:
    for limit, score in ((0.05, 5), (0.10, 4), (0.20, 3), (0.35, 2)):
        if wer <= limit:
            return score
    return 1


def _image_block(path) -> dict:
    img = Image.open(path).convert("RGB")
    img.thumbnail((540, 540))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode()}}


def run(ep: Episode, cfg: dict, rec: StageRecord) -> list[dict]:
    clips = load_clips(ep)
    words = load_words(ep)
    scratch = work_dir(cfg, ep)

    payload = []
    for c in clips:
        first3 = " ".join(w["w"] for w in words if c["start"] <= w["s"] < c["start"] + 3.0)
        payload.append({"number": c["number"], "first_3_seconds": first3, "full_text": c["text"]})
    text_scores = {int(s["number"]): s for s in ask_json(
        cfg, rec, "score", TEXT_PROMPT.format(clips=json.dumps(payload, ensure_ascii=False, indent=1)))}

    results: list[dict] = []
    for c in clips:
        row = {"number": c["number"], "title": c["title"]}
        ts = text_scores.get(c["number"], {})
        row.update(hook=ts.get("hook"), self_contained=ts.get("self_contained"), note=ts.get("note", ""))

        wav = extract_wav(ep.package / c["videos"]["1x1"], scratch / f"score_{c['number']:02d}.wav")
        heard: list[dict] = []
        for seg in whisper_segments(cfg["whisper_url"], wav, rec):
            heard.extend(segment_words(seg, "mix"))
        wer = word_error_rate(tokens(caption_text(words, c["start"], c["end"])),
                              tokens(" ".join(w["w"] for w in heard)))
        row.update(caption_wer_pct=round(100 * wer, 1), caption_accuracy=wer_to_score(wer))

        frames = [frame_at(ep.package / c["videos"][name], 1.5, scratch / f"score_{name}.png")
                  for name in ("1x1", "9x16")]
        framing = ask_json(cfg, rec, "score", [_image_block(frames[0]), _image_block(frames[1]),
                                               {"type": "text", "text": FRAMING_PROMPT}], max_tokens=300)
        row.update(framing_1x1=framing.get("framing_1x1"), framing_9x16=framing.get("framing_9x16"),
                   framing_note=framing.get("note", ""))
        nums = [row[k] for k in ("hook", "self_contained", "caption_accuracy", "framing_1x1", "framing_9x16")
                if isinstance(row.get(k), (int, float))]
        row["mean"] = round(sum(nums) / len(nums), 2) if nums else None
        c["score"] = {k: row[k] for k in ("hook", "self_contained", "caption_accuracy",
                                          "framing_1x1", "framing_9x16", "mean")}
        results.append(row)
        logger.info("ℹ️ clip %d scored %s", c["number"], row["mean"])

    (ep.package / SCORES_FILE).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    save_clips(ep, clips)
    write_clips_md(ep, clips)
    logger.info("✅ scored %d clips", len(results))
    return results
