"""Stages: clip selection and per-clip copy.

Selection shows the model the episode as numbered sentences and asks for
sentence-id ranges, so every cut lands on a sentence boundary by
construction. Copy follows the owner's house style (derived in the #333
spike): lowercase ≤5-word titles, a LinkedIn hook + short paragraphs + fixed
footer, and the templated Instagram caption.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from podcast.episode import Episode, work_dir
from podcast.hub import ask_json
from podcast.metrics import StageRecord
from podcast.transcribe import _norm, build_turns, drop_backchannel, fmt_ts, load_words, sentences

logger = logging.getLogger("podcast.clips")

CLIPS_FILE = "clips.json"
START_PAD_S = 0.15
END_PAD_S = 0.35
MAX_OVERLAP = 0.3


# ── style examples (Notion, read-only) ──────────────────────────────────

def style_examples(cfg: dict, ep: Episode) -> dict:
    """Past clip titles and LinkedIn intros from the Notion Clips table, cached locally."""
    cache = work_dir(cfg, ep) / "style_examples.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    examples: dict = {"titles": [], "linkedin": []}
    db_id = (cfg.get("notion") or {}).get("clips_db_id")
    if not db_id:
        logger.warning("⚠️ podcast.notion.clips_db_id not set — selecting without style examples")
        return examples
    from config.loader import load_full_config  # noqa: PLC0415
    from reporting.notion._client import extract_property_value, init_notion_client  # noqa: PLC0415
    from reporting.notion.editorial import query_rows_by_filter  # noqa: PLC0415

    notion = init_notion_client(load_full_config()["notion"]["api_token"])
    rows = query_rows_by_filter(notion, db_id, {"property": "clip", "title": {"is_not_empty": True}})
    for row in rows:
        props = row.get("properties", {})
        title = extract_property_value(props["clip"]) if "clip" in props else None
        if title:
            examples["titles"].append(str(title).strip())
        li = extract_property_value(props["TextLI"]) if "TextLI" in props else None
        if li and "A clip from my conversation" in li:
            examples["linkedin"].append(li.split("A clip from my conversation")[0].strip())
    rng = random.Random(333)
    examples["titles"] = rng.sample(examples["titles"], min(40, len(examples["titles"])))
    examples["linkedin"] = examples["linkedin"][:6]
    cache.write_text(json.dumps(examples, indent=1, ensure_ascii=False), encoding="utf-8")
    logger.info("ℹ️ style examples: %d titles, %d LinkedIn intros (Notion, read-only)",
                len(examples["titles"]), len(examples["linkedin"]))
    return examples


# ── selection ───────────────────────────────────────────────────────────

SELECT_SYSTEM = (
    "You are the video editor of a leadership podcast. You pick short social clips "
    "that work on their own for someone scrolling LinkedIn or Instagram."
)


def _selection_prompt(ep: Episode, sents: list[dict], n: int, cfg: dict, titles: list[str]) -> str:
    interviewee = ep.host_first if ep.host_is_interviewee else ep.guest_first
    lines = "\n".join(f"S{i} [{fmt_ts(s['start'])}-{fmt_ts(s['end'])}] {ep.label(s['speaker'])}: {s['text']}"
                      for i, s in enumerate(sents))
    return f"""Below is an interview transcript split into numbered sentences. {interviewee} is the interviewee.

Pick the {n + 5} best clips, ranked best first. House style, from 380 published clips:
- {cfg['clip_min_s']}-{cfg['clip_max_s']} seconds, ideally 35-65 (median 52 s). Each sentence shows [start-end];
  a clip's length is the end of its last sentence minus the start of its first. Check it: most single
  sentences are 3-10 s, so a clip is usually 5 to 12 sentences.
- One self-contained idea per clip. A viewer with no context must follow it: no "as I said", no unresolved "that"/"this" pointing back.
- The first sentence is the hook: a claim, a question, a striking fact or a story opener. It must grab within 3 seconds.
- End on a landed sentence that closes the thought.
- Mostly the interviewee speaking; a short question from the interviewer at the start is fine.
- No two clips about the same point. Clips must not overlap.
- Skip greetings, logistics and the sign-off.

Titles: lowercase, at most 5 words, no trailing punctuation, no names; a noun phrase or an imperative. Past titles:
{json.dumps(titles[:40], ensure_ascii=False)}

Reply with JSON only: a list of objects
{{"start_id": <int S-number of the first sentence>, "end_id": <int S-number of the last sentence>, "title": "...", "why": "<one line>"}}

Transcript:
{lines}
"""


TOP_UP = """
Already chosen (do not overlap them): {taken}.
Some of your picks were rejected for being shorter than {min_s} s or longer than {max_s} s.
Pick {missing} MORE clips that meet every rule above, with the length checked against the [start-end] times.
Reply with JSON only, same shape.
"""


def overlap_ratio(a: dict, b: dict) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / max(0.01, min(a["end"] - a["start"], b["end"] - b["start"]))


def resolve_candidates(raw: list[dict], sents: list[dict], *, n: int, min_s: float,
                       max_s: float) -> list[dict]:
    """Map sentence-id ranges to padded times; drop invalid, too short/long and
    overlapping candidates (rank order wins); keep the first ``n``."""
    picked: list[dict] = []
    for cand in raw:
        try:
            a, b = int(cand["start_id"]), int(cand["end_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= a <= b < len(sents)):
            continue
        clip = {"start": round(max(0.0, sents[a]["start"] - START_PAD_S), 2),
                "end": round(sents[b]["end"] + END_PAD_S, 2),
                "title": str(cand.get("title", "")).strip().lower().rstrip(".!?"),
                "why": str(cand.get("why", "")).strip(),
                "sentence_ids": [a, b]}
        dur = clip["end"] - clip["start"]
        if not (min_s <= dur <= max_s):
            logger.info("ℹ️ skip '%s': %.0fs outside %s-%ss", clip["title"], dur, min_s, max_s)
            continue
        if any(overlap_ratio(clip, p) > MAX_OVERLAP for p in picked):
            logger.info("ℹ️ skip '%s': overlaps a higher-ranked clip", clip["title"])
            continue
        picked.append(clip)
        if len(picked) == n:
            break
    return picked


OPENERS = frozenset({"and", "so", "but", "yeah", "yes", "well", "absolutely", "exactly", "okay", "ok",
                     "right", "oh", "um", "uh"})
MAX_OPENER_WORDS = 4


def trim_opener(clip: dict, words: list[dict], *, min_s: float) -> dict:
    """Start the clip after a leading "And…/So…/Yeah…" (and a "then" after
    "and"/"but"), so it opens on its hook. A trim that would leave the clip
    shorter than ``min_s`` is not made."""
    inside = [w for w in drop_backchannel(words) if clip["start"] <= w["s"] < clip["end"]]
    k = 0
    while k < min(MAX_OPENER_WORDS, len(inside) - 1):
        core = _norm(inside[k]["w"])
        after_conj = k and _norm(inside[k - 1]["w"]) in ("and", "but")
        if core not in OPENERS and not (core == "then" and after_conj):
            break
        k += 1
    start = round(inside[k]["s"] - START_PAD_S, 2) if k else clip["start"]
    if k and clip["end"] - start >= min_s:
        logger.info("ℹ️ clip '%s': trimmed opener %r", clip.get("title", ""),
                    " ".join(w["w"] for w in inside[:k]))
        clip = {**clip, "start": start, "trimmed_opener": " ".join(w["w"] for w in inside[:k])}
    return clip


def _as_list(reply) -> list[dict]:
    """The model sometimes wraps the list in an object (``{"clips": [...]}``)."""
    if isinstance(reply, dict):
        reply = next((v for v in reply.values() if isinstance(v, list)), [])
    return [c for c in reply if isinstance(c, dict)] if isinstance(reply, list) else []


def run_select(ep: Episode, cfg: dict, rec: StageRecord) -> list[dict]:
    words = load_words(ep)
    sents = [s for s in sentences(words) if ep.in_window(s["start"], s["end"])]
    examples = style_examples(cfg, ep)
    n = int(cfg.get("clips_per_episode", 15))
    prompt = _selection_prompt(ep, sents, n, cfg, examples["titles"])
    raw = ask_json(cfg, rec, "select", prompt, system=SELECT_SYSTEM, max_tokens=6000)
    limits = {"n": n, "min_s": cfg["clip_min_s"], "max_s": cfg["clip_max_s"]}
    clips = resolve_candidates(_as_list(raw), sents, **limits)
    if len(clips) < n:
        # One top-up round: the model can't time spans exactly, so ask for the
        # shortfall with the kept clips and the rejection rule spelled out.
        logger.info("ℹ️ %d of %d clips valid, asking for more", len(clips), n)
        taken = ", ".join(f"S{c['sentence_ids'][0]}-S{c['sentence_ids'][1]}" for c in clips)
        extra = ask_json(cfg, rec, "select", prompt + TOP_UP.format(
            taken=taken, missing=n - len(clips) + 4, min_s=cfg["clip_min_s"], max_s=cfg["clip_max_s"]),
            system=SELECT_SYSTEM, max_tokens=4000)
        clips = resolve_candidates(_as_list(raw) + _as_list(extra), sents, **limits)
    clips = [trim_opener(c, words, min_s=cfg["clip_min_s"]) for c in clips]
    clips.sort(key=lambda c: c["start"])
    for i, clip in enumerate(clips, 1):
        clip["number"] = i
        clip["speaker"] = dominant_speaker(words, clip["start"], clip["end"])
        clip["text"] = clip_text(ep, words, clip["start"], clip["end"])
    if len(clips) < n:
        logger.warning("⚠️ only %d of %d clips survived validation", len(clips), n)
    save_clips(ep, clips)
    logger.info("✅ selected %d clips", len(clips))
    return clips


def dominant_speaker(words: list[dict], start: float, end: float) -> str:
    """Speaker with the most speaking time inside the window."""
    talk: dict[str, float] = {}
    for w in words:
        if start <= w["s"] < end:
            talk[w["spk"]] = talk.get(w["spk"], 0.0) + (w["e"] - w["s"])
    return max(talk, key=talk.get) if talk else "guest"


def clip_text(ep: Episode, words: list[dict], start: float, end: float) -> str:
    """The clip's words as speaker turns; word-based, so a clip that starts
    mid-sentence (a trimmed opener) keeps the rest of that sentence."""
    inside = [w for w in words if start - 0.01 <= w["s"] < end + 0.01]
    return "\n".join(f"{ep.label(t['speaker'])}: {t['text']}" for t in build_turns(inside))


# ── copy ────────────────────────────────────────────────────────────────

COPY_SYSTEM = (
    "You write social copy for Roberto Ferraro, who curates leadership and personal-development "
    "ideas. Plain, warm, direct English. No hashtags, no buzzwords, no em dashes."
)


def _copy_prompt(ep: Episode, clips: list[dict], examples: list[str]) -> str:
    if ep.host_is_interviewee:
        voice = (f"In this episode {ep.guest_display} interviews {ep.host_first}. When {ep.host_first} "
                 f"speaks in the clip, write in FIRST PERSON as {ep.host_first} (\"I\", \"my\").")
    else:
        voice = (f"{ep.guest_display} is the guest. Write in third person about {ep.guest_first} "
                 f"({ep.guest_pronoun_possessive} ideas), and pull one short quote.")
    payload = [{"number": c["number"], "draft_title": c["title"], "text": c["text"]} for c in clips]
    return f"""Write the copy for each clip. {voice}

For each clip return:
- "title": lowercase, at most 5 words, no punctuation at the end, no names (keep the draft if it is good).
- "linkedin_hook": one line, a claim or a question that makes people stop scrolling.
- "linkedin_body": 3 or 4 one-sentence paragraphs (separate them with a blank line) restating the clip's idea. Stay faithful to what is said.

Recent LinkedIn intros, for voice (hook + paragraphs only):
{json.dumps(examples, ensure_ascii=False, indent=1)}

Reply with JSON only: a list of {{"number", "title", "linkedin_hook", "linkedin_body"}}.

Clips:
{json.dumps(payload, ensure_ascii=False, indent=1)}
"""


def linkedin_post(ep: Episode, hook: str, body: str, footer: str) -> str:
    credit = f"A clip from my conversation with the {ep.adjective} @{ep.guest_display}"
    return "\n\n".join(p for p in (hook.strip(), body.strip(), credit, footer.strip()) if p)


def instagram_caption(ep: Episode, title: str) -> str:
    return f"{title[:1].upper()}{title[1:]}. A clip from my conversation with the {ep.adjective} {ep.guest_display}"


def run_copy(ep: Episode, cfg: dict, rec: StageRecord) -> list[dict]:
    clips = load_clips(ep)
    examples = style_examples(cfg, ep)["linkedin"]
    replies = ask_json(cfg, rec, "copy", _copy_prompt(ep, clips, examples),
                       system=COPY_SYSTEM, max_tokens=12000)
    by_number = {int(r["number"]): r for r in replies if "number" in r}
    for clip in clips:
        reply = by_number.get(clip["number"])
        if not reply:
            logger.warning("⚠️ no copy for clip %d", clip["number"])
            continue
        clip["title"] = str(reply.get("title") or clip["title"]).strip().lower().rstrip(".!?")
        clip["linkedin"] = linkedin_post(ep, reply.get("linkedin_hook", ""),
                                         reply.get("linkedin_body", ""), cfg.get("linkedin_footer", ""))
        clip["instagram"] = instagram_caption(ep, clip["title"])
    save_clips(ep, clips)
    write_clips_md(ep, clips)
    logger.info("✅ copy written for %d clips", len(clips))
    return clips


# ── persistence ─────────────────────────────────────────────────────────

def load_clips(ep: Episode) -> list[dict]:
    path = ep.package / CLIPS_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_clips(ep: Episode, clips: list[dict]) -> None:
    ep.package.mkdir(parents=True, exist_ok=True)
    (ep.package / CLIPS_FILE).write_text(json.dumps(clips, indent=1, ensure_ascii=False), encoding="utf-8")


def write_clips_md(ep: Episode, clips: list[dict]) -> Path:
    parts = [f"# Clips - {ep.base_name}\n"]
    for c in clips:
        parts.append(f"## {c['number']}. {c['title']}  ({fmt_ts(c['start'])}-{fmt_ts(c['end'])}, "
                     f"{c['end'] - c['start']:.0f} s)\n")
        if c.get("why"):
            parts.append(f"_Why:_ {c['why']}\n")
        parts.append(f"**Instagram:** {c.get('instagram', '')}\n")
        parts.append(f"**LinkedIn:**\n\n{c.get('linkedin', '')}\n")
        if c.get("score"):
            parts.append(f"**Score:** {json.dumps(c['score'])}\n")
        parts.append(f"<details><summary>clip text</summary>\n\n{c['text']}\n\n</details>\n")
    path = ep.package / "clips.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path
