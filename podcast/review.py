"""Caption review: an LLM reads every caption word of a clip in context (the
words around the clip and the episode's topic) and corrects what was
misheard — in a conversation about sleep, "aim for hate" is "aim for eight".

The model answers with word-index corrections, each marked sure or not. Only
sure ones are applied (the rest are reported as doubts), and
``apply_corrections`` takes one only when the word at that index still reads
as the model quoted it, so a miscounted index cannot overwrite the wrong word.
"""

from __future__ import annotations

import json
import logging
import re

from podcast.episode import Episode
from podcast.hub import ask_json
from podcast.metrics import StageRecord
from podcast.transcribe import sentences

logger = logging.getLogger("podcast.review")

REVIEW_FILE = "caption_review.json"
CONTEXT_S = 60.0
_EDGE = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)

REVIEW_SYSTEM = ("You proofread burned-in video captions. You fix misheard words and nothing else: "
                 "no rephrasing, no grammar fixes, no style edits.")

REVIEW_PROMPT = """Below are the captions of one clip from a podcast conversation, word by word, numbered.
They come from machine transcription, which mishears words that sound alike. Read them in context
and correct every word that was clearly misheard: one that makes no sense here but a similar-sounding
word does (in a conversation about sleep, "we aim for hate" is "we aim for eight"). A word that makes
sense stays, even if informal or ungrammatical ("I don't hate you" stays). Names: use the spelling
in the speaker list. The correction must sound like the shown word; when the independent transcript
has the same word, both decoders heard it, so be extra careful. Mark a correction "sure": false when
you are guessing: it is then not applied, and it is shown to the owner as a doubt.

Speakers: {speakers}
{topic}
Clip title: {title}

Independent transcript of the minutes around the clip (another decode, it has its own errors; use it
for context and as a second opinion, not as ground truth):
{context}

Captions:
{captions}

Reply with JSON only:
{{"corrections": [{{"i": <word number>, "from": "<the word as shown>", "to": "<the corrected word or words>", "sure": true}}],
 "raw_score": <1-5, accuracy of the captions as shown: 5 = no misheard word, 3 = a few, 1 = hard to follow>,
 "final_score": <1-5, the same after your corrections>,
 "doubts": "<at most 15 words: anything you could not resolve, or empty>"}}
"""


def _parts(word: str) -> tuple[str, str, str]:
    m = _EDGE.match(word)
    return m.group(1), m.group(2), m.group(3)


def _same(a: str, b: str) -> bool:
    return _parts(a)[1].lower() == _parts(b)[1].lower()


def apply_corrections(words: list[dict], corrections: list) -> tuple[list[dict], list[dict]]:
    """Return ``(corrected words, applied corrections)``.

    A correction applies only when ``from`` matches the word at ``i``; the
    original's punctuation and leading capital are kept. An empty ``to``
    drops the word."""
    out = [dict(w) for w in words]
    applied, dropped = [], set()
    for fix in corrections if isinstance(corrections, list) else []:
        try:
            i, src, dst = int(fix["i"]), str(fix["from"]), str(fix.get("to", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= i < len(out) or i in dropped or not _same(out[i]["w"], src):
            logger.warning("⚠️ caption fix skipped, word %s is not %r", i, src)
            continue
        if _same(out[i]["w"], dst):
            continue
        lead, body, trail = _parts(out[i]["w"])
        if not dst:
            dropped.add(i)
        else:
            d_lead, d_body, d_trail = _parts(dst)
            if body[:1].isupper() and d_body[:1].islower():
                d_body = d_body[:1].upper() + d_body[1:]
            out[i]["w"] = (d_lead or lead) + d_body + (d_trail or trail)
        applied.append({"i": i, "from": words[i]["w"], "to": out[i]["w"] if dst else ""})
    return [w for i, w in enumerate(out) if i not in dropped], applied


def _context(ep: Episode, episode_words: list[dict], clip: dict) -> str:
    sents = [s for s in sentences(episode_words)
             if clip["start"] - CONTEXT_S <= s["start"] and s["end"] <= clip["end"] + CONTEXT_S]
    return "\n".join(f"{ep.label(s['speaker'])}: {s['text']}" for s in sents)


def _topic(ep: Episode) -> str:
    path = ep.package / "episode_copy.json"
    if not path.exists():
        return ""
    intro = json.loads(path.read_text(encoding="utf-8")).get("youtube_intro", "")
    return f"Episode topic: {intro}" if intro else ""


def review_clip(ep: Episode, cfg: dict, rec: StageRecord, clip: dict, words: list[dict],
                episode_words: list[dict]) -> tuple[list[dict], dict]:
    """Review one clip's caption words; returns the corrected words and the review record."""
    captions = " ".join(f"[{i}]{w['w']}" for i, w in enumerate(words))
    prompt = REVIEW_PROMPT.format(
        speakers=f"{ep.guest_display} (guest), {ep.host_name} (host)", topic=_topic(ep),
        title=clip.get("title", ""), context=_context(ep, episode_words, clip), captions=captions)
    try:
        reply = ask_json(cfg, rec, "caption_review", prompt, system=REVIEW_SYSTEM, max_tokens=3000)
    except Exception as exc:  # noqa: BLE001 — keep the decode rather than lose the clip
        logger.error("❌ clip %d: caption review failed, captions unreviewed: %s", clip["number"], exc)
        return words, {"raw_score": None, "final_score": None, "corrections": [], "doubts": "review failed"}
    reply = reply if isinstance(reply, dict) else {}
    proposed = [f for f in reply.get("corrections") or [] if isinstance(f, dict)]
    unsure = [f for f in proposed if f.get("sure") is not True]
    fixed, applied = apply_corrections(words, [f for f in proposed if f.get("sure") is True])
    notes = [str(reply["doubts"])] if reply.get("doubts") else []
    notes += [f"{f.get('from')} → {f.get('to')}?" for f in unsure]
    record = {"raw_score": reply.get("raw_score"), "final_score": reply.get("final_score"),
              "corrections": applied, "doubts": "; ".join(notes)}
    logger.info("ℹ️ clip %d: caption review %s → %s, %d fix(es)%s", clip["number"], record["raw_score"],
                record["final_score"], len(applied), f" · doubts: {record['doubts']}" if record["doubts"] else "")
    return fixed, record
