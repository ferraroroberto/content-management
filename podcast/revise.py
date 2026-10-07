"""Stage: apply the owner's review feedback to the clips that asked for changes
(issue #339), and re-render only those.

Per clip with an open review round:

1. One hub call (``models.revise``) turns the free-text notes into edits in a
   fixed vocabulary: caption fixes, a new start/end word, cuts, extending the
   source span, shot speaker/framing overrides, and a new title or copy.
   Whatever does not fit the vocabulary comes back as ``unhandled`` and is
   shown to the owner, never guessed.
2. ``resolve`` checks every index the model quoted and turns it into episode
   times, so the edits still land after an extension re-decodes the clip.
3. The current version (both crops, cover, clip record) is copied to
   ``clips/versions/<NN>/v<N>/`` before anything is replaced.
4. An extension re-runs the clip's edit decisions and caption review; then
   ``apply_ops`` (pure) applies the rest, both crops are re-rendered, and the
   clip goes back to ``pending`` review as version N+1.

Approved, dropped and pending clips are never touched.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from podcast.captions import LAYOUTS
from podcast.clips import (clip_text, instagram_caption, linkedin_post, load_clips, save_clips,
                           write_clips_md)
from podcast.edit import (CLIP_WORDS_FILE, FPS, LEAD_S, TAIL_S, ZOOMS, _grid, edit_clip, is_filler,
                          kept_seconds, load_clip_words, output_words, remap)
from podcast.episode import Episode, work_dir
from podcast.hub import ask_json
from podcast.metrics import StageRecord
from podcast.render import clip_filename, clip_outputs, prepare_fonts, render_clip
from podcast.review import _same, apply_corrections, review_clip
from podcast.review_state import clip_state, load_review, now, open_round, to_revise, update
from podcast.transcribe import load_words

logger = logging.getLogger("podcast.revise")

VERSIONS_DIR = "clips/versions"
CREDIT_MARK = "A clip from my conversation"
MATCH_S = 0.05  # a fix finds its word again by start time within this
MAX_EXTEND_S = 60.0

REVISE_SYSTEM = ("You turn a video editor's review notes on one podcast clip into precise edits. "
                 "You change only what the notes ask for.")

REVISE_PROMPT = """The owner watched this clip and left review notes. Turn the notes into edits.

Speakers: guest = {guest}, host = {host}.
Clip title: {title}
Length after jump cuts: {cut_s:.1f} s
Instagram caption: {instagram}
LinkedIn hook: {hook}
LinkedIn body:
{body}

Caption words as they play in the clip, "[index] word (seconds into the clip)". Words already cut
out are not listed.
{words}

Camera shots of the square (1:1) version, in order, "[index] speaker, from-to seconds, zoom":
{shots}

Review notes:
\"\"\"{feedback}\"\"\"

Edits you can make. Leave out every field the notes do not ask for.
- "caption_fixes": [{{"i": <index>, "from": "<word as shown>", "to": "<corrected word or words>"}}] for a
  caption word that is wrong; "to": "" removes the word from the captions.
- "start_word": <index> to start the clip at that word (everything before it is cut).
- "end_word": <index> to end the clip after that word.
- "cuts": [{{"from_word": <index>, "to_word": <index>}}] to remove those words with their sound and picture
  (a stumble, a tangent).
- "extend_start_s" / "extend_end_s": <seconds> to add before the clip's start / after its end, when the
  notes say it starts too late or ends too early (the words outside the clip are not shown: estimate).
- "shots": [{{"shot": <index>, "speaker": "guest" | "host", "zoom": 1.0 | 1.08 | 1.18}}] to change who is
  on screen or the framing of a shot (1.0 is the widest); give only the fields that change.
- "all_zoom": <number> for one framing over the whole clip (1.0 = no punch-ins).
- "title": the new title: lowercase, at most 5 words, no names, no punctuation at the end.
- "instagram": the full new Instagram caption, only when the notes ask for one (a new title already
  updates the templated caption).
- "linkedin_hook" / "linkedin_body": a new hook line / a new body (one-sentence paragraphs separated by a
  blank line); the credit line and footer are added after it.
- "unhandled": what in the notes these edits cannot express, in a few words (empty when all is covered).

Times in the notes ("at 0:12", "around 20 seconds") are seconds into the clip, like the times above.
Reply with JSON only: one object with the fields you use.
"""


# ── prompt ──────────────────────────────────────────────────────────────

def _keep(clip: dict) -> list[tuple[float, float]]:
    return [tuple(span) for span in (clip.get("keep") or [[clip["start"], clip["end"]]])]


def shown_words(clip: dict, words: list[dict]) -> list[tuple[int, dict]]:
    """``(index, word)`` for the words that play in the cut clip (fillers and the trimmed edges left out)."""
    keep = _keep(clip)
    return [(i, w) for i, w in enumerate(words)
            if not is_filler(w) and keep[0][0] <= w["s"] < keep[-1][1]]


def split_linkedin(post: str) -> tuple[str, str]:
    """``(hook, body)`` of a post built by ``linkedin_post``: the paragraphs before the credit line."""
    paras = [p for p in post.split("\n\n")]
    head = paras[:next((i for i, p in enumerate(paras) if p.startswith(CREDIT_MARK)), len(paras))]
    return (head[0], "\n\n".join(head[1:])) if head else ("", "")


def build_prompt(ep: Episode, clip: dict, words: list[dict], feedback: str) -> str:
    keep = _keep(clip)
    hook, body = split_linkedin(clip.get("linkedin", ""))
    word_lines = " ".join(f"[{i}]{w['w']} ({remap(w['s'], keep):.1f})" for i, w in shown_words(clip, words))
    shot_lines = "\n".join(f"[{k}] {s['spk']}, {remap(s['a'], keep):.1f}-{remap(s['b'], keep):.1f}, "
                           f"zoom {s['zoom']}" for k, s in enumerate(clip.get("shots") or []))
    return REVISE_PROMPT.format(
        guest=ep.guest_display, host=ep.host_name, title=clip.get("title", ""),
        cut_s=kept_seconds(keep), instagram=clip.get("instagram", ""), hook=hook, body=body,
        words=word_lines, shots=shot_lines or "(single shot)", feedback=feedback)


# ── reply → time-based operations ───────────────────────────────────────

def _index(value, words: list[dict]) -> Optional[int]:
    try:
        i = int(value)
    except (TypeError, ValueError):
        return None
    return i if 0 <= i < len(words) else None


def _seconds(value) -> float:
    try:
        return min(max(float(value), 0.0), MAX_EXTEND_S)
    except (TypeError, ValueError):
        return 0.0


def resolve(reply: dict, clip: dict, words: list[dict]) -> dict:
    """Check the model's edits against the clip and express them in episode
    time. A quoted index that does not exist, or a fix whose ``from`` is not
    the word at that index, goes to ``unhandled`` instead."""
    reply = reply if isinstance(reply, dict) else {}
    unhandled = [str(reply["unhandled"]).strip()] if str(reply.get("unhandled") or "").strip() else []
    ops: dict = {"fixes": [], "start": None, "end": None, "cuts": [], "shots": [], "all_zoom": None,
                 "extend": (_seconds(reply.get("extend_start_s")), _seconds(reply.get("extend_end_s")))}
    for fix in reply.get("caption_fixes") or []:
        i = _index(fix.get("i"), words) if isinstance(fix, dict) else None
        if i is None or not _same(words[i]["w"], str(fix.get("from", ""))):
            unhandled.append(f"caption fix {fix!r} does not match the captions")
            continue
        ops["fixes"].append({"t": words[i]["s"], "from": words[i]["w"], "to": str(fix.get("to", "")).strip()})
    if reply.get("start_word") is not None:
        i = _index(reply["start_word"], words)
        if i is None:
            unhandled.append(f"start word {reply['start_word']!r} does not exist")
        else:
            ops["start"] = words[i]["s"] - LEAD_S
    if reply.get("end_word") is not None:
        j = _index(reply["end_word"], words)
        if j is None:
            unhandled.append(f"end word {reply['end_word']!r} does not exist")
        else:
            ops["end"] = words[j]["e"] + TAIL_S
    for cut in reply.get("cuts") or []:
        a = _index(cut.get("from_word"), words) if isinstance(cut, dict) else None
        b = _index(cut.get("to_word"), words) if isinstance(cut, dict) else None
        if a is None or b is None or b < a:
            unhandled.append(f"cut {cut!r} does not match the captions")
            continue
        lo = (words[a - 1]["e"] + words[a]["s"]) / 2 if a > 0 else words[a]["s"] - LEAD_S
        hi = (words[b]["e"] + words[b + 1]["s"]) / 2 if b + 1 < len(words) else words[b]["e"] + TAIL_S
        ops["cuts"].append((max(lo, words[a]["s"] - LEAD_S), min(hi, words[b]["e"] + TAIL_S)))
    shots = clip.get("shots") or []
    for change in reply.get("shots") or []:
        k = change.get("shot") if isinstance(change, dict) else None
        if not isinstance(k, int) or not 0 <= k < len(shots):
            unhandled.append(f"shot change {change!r} names no shot")
            continue
        override = {"a": shots[k]["a"], "b": shots[k]["b"]}
        if change.get("speaker") in ("guest", "host"):
            override["spk"] = change["speaker"]
        if isinstance(change.get("zoom"), (int, float)):
            override["zoom"] = min(ZOOMS, key=lambda z: abs(z - change["zoom"]))
        ops["shots"].append(override)
    if isinstance(reply.get("all_zoom"), (int, float)):
        ops["all_zoom"] = min(ZOOMS, key=lambda z: abs(z - reply["all_zoom"]))
    for key in ("title", "instagram", "linkedin_hook", "linkedin_body"):
        value = str(reply.get(key) or "").strip()
        ops[key] = value or None
    ops["unhandled"] = unhandled
    return ops


# ── apply (pure) ────────────────────────────────────────────────────────

def subtract(keep: list[tuple[float, float]], a: float, b: float) -> list[tuple[float, float]]:
    out = []
    for x, y in keep:
        if y <= a or x >= b:
            out.append((x, y))
            continue
        if x < a:
            out.append((x, a))
        if b < y:
            out.append((b, y))
    return out


def fit_shots(shots: list[dict], keep: list[tuple[float, float]]) -> list[dict]:
    """The camera plan cut to the new kept spans; each piece keeps its speaker and framing."""
    out = []
    for s in shots:
        for x, y in keep:
            a, b = max(s["a"], x), min(s["b"], y)
            if b - a >= 1 / FPS:
                out.append({**s, "a": a, "b": b})
    return out


def apply_ops(ep: Episode, clip: dict, words: list[dict], ops: dict,
              footer: str) -> tuple[dict, list[dict], list[str], list[str]]:
    """Return ``(clip, words, applied, unhandled)`` with every operation but
    ``extend`` applied (the caller re-edits for that first)."""
    clip, applied, unhandled = json.loads(json.dumps(clip)), [], list(ops.get("unhandled", []))

    found = []
    for fix in ops["fixes"]:
        i = next((k for k, w in enumerate(words) if abs(w["s"] - fix["t"]) <= MATCH_S
                  and _same(w["w"], fix["from"])), None)
        if i is None:
            unhandled.append(f"caption fix {fix['from']!r} → {fix['to']!r}: word no longer there")
        else:
            found.append({"i": i, "from": words[i]["w"], "to": fix["to"]})
    words, fixed = apply_corrections(words, found)
    applied += [f"caption {f['from']!r} → {f['to'] or '(removed)'!r}" for f in fixed]

    keep, start = _keep(clip), clip["start"]
    if ops["start"] is not None or ops["end"] is not None or ops["cuts"]:
        trimmed = list(keep)
        if ops["start"] is not None:
            trimmed = [(max(x, ops["start"]), y) for x, y in trimmed if y > ops["start"]]
        if ops["end"] is not None:
            trimmed = [(x, min(y, ops["end"])) for x, y in trimmed if x < ops["end"]]
        for a, b in ops["cuts"]:
            trimmed = subtract(trimmed, a, b)
        trimmed = [(_grid(x, start), _grid(y, start)) for x, y in trimmed]
        trimmed = [(x, y) for x, y in trimmed if y - x >= 2 / FPS]
        remaining = [w for w in words if not any(a <= w["s"] < b for a, b in ops["cuts"])]
        if not any(x <= w["s"] < y for w in remaining if not is_filler(w) for x, y in trimmed):
            unhandled.append("the trims and cuts would leave no spoken word: not applied")
        else:
            if len(remaining) < len(words):
                applied.append(f"cut {len(words) - len(remaining)} word(s)")
            words = remaining
            if ops["start"] is not None:
                applied.append(f"start moved to {ops['start']:.2f} s")
            if ops["end"] is not None:
                applied.append(f"end moved to {ops['end']:.2f} s")
            clip["keep"] = [list(span) for span in trimmed]
            clip["shots"] = fit_shots(clip.get("shots") or [], trimmed)
            keep = trimmed

    for override in ops["shots"]:
        # The shots now inside the referenced shot's time range (cuts may have split it).
        hits = [s for s in clip.get("shots") or [] if override["a"] <= (s["a"] + s["b"]) / 2 <= override["b"]]
        for s in hits:
            s.update({k: v for k, v in override.items() if k in ("spk", "zoom")})
        if hits:
            applied.append(f"shot at {override['a']:.1f} s → "
                           + ", ".join(f"{k} {v}" for k, v in override.items() if k in ("spk", "zoom")))
    if ops["all_zoom"] is not None:
        for s in clip.get("shots") or []:
            s["zoom"] = ops["all_zoom"]
        applied.append(f"one framing (zoom {ops['all_zoom']}) for the whole clip")

    if ops["title"]:
        title = ops["title"].lower().rstrip(".!?")
        if title != clip["title"]:
            applied.append(f"title {clip['title']!r} → {title!r}")
            clip["title"] = title
            clip["instagram"] = instagram_caption(ep, title)
    if ops["instagram"]:
        clip["instagram"] = ops["instagram"]
        applied.append("Instagram caption rewritten")
    if ops["linkedin_hook"] or ops["linkedin_body"]:
        hook, body = split_linkedin(clip.get("linkedin", ""))
        clip["linkedin"] = linkedin_post(ep, ops["linkedin_hook"] or hook, ops["linkedin_body"] or body, footer)
        applied.append("LinkedIn " + " and ".join(k for k in ("hook", "body") if ops[f"linkedin_{k}"])
                       + " rewritten")

    clip.setdefault("edit", {}).update(cut_s=kept_seconds(keep), jump_cuts=len(keep) - 1)
    return clip, words, applied, unhandled


# ── versions ────────────────────────────────────────────────────────────

def version_dir(ep: Episode, number: int, version: int) -> Path:
    return ep.package / VERSIONS_DIR / f"{number:02d}" / f"v{version}"


def list_versions(ep: Episode, number: int) -> list[Path]:
    root = ep.package / VERSIONS_DIR / f"{number:02d}"
    if not root.is_dir():
        return []
    return sorted((p for p in root.iterdir() if re.fullmatch(r"v\d+", p.name)), key=lambda p: int(p.name[1:]))


def archive_version(ep: Episode, clip: dict, words: list[dict], version: int) -> Path:
    """Copy the clip's current crops, cover and record into its version folder
    (once: a re-run after a failed render keeps the first copy)."""
    dst = version_dir(ep, clip["number"], version)
    dst.mkdir(parents=True, exist_ok=True)
    sources = dict(zip(LAYOUTS, clip_outputs(ep, clip, "videos")))
    sources["cover"] = clip_outputs(ep, clip, "covers")[0]
    for name, src in sources.items():
        out = dst / f"{name}{src.suffix}"
        if src.exists() and not out.exists():
            shutil.copy2(src, out)
    record = dst / "clip.json"
    if not record.exists():
        record.write_text(json.dumps({"clip": clip, "words": words}, indent=1, ensure_ascii=False),
                          encoding="utf-8")
    return dst


# ── stage ───────────────────────────────────────────────────────────────

def _extend(ep: Episode, cfg: dict, rec: StageRecord, clip: dict, before: float, after: float,
            episode_words: list[dict], scratch: Path) -> list[dict]:
    """Widen the source span and redo the clip's edit decisions and caption review."""
    clip["start"] = round(max(ep.start_s, clip["start"] - before), 3)
    clip["end"] = round(clip["end"] + after, 3)
    clip["text"] = clip_text(ep, episode_words, clip["start"], clip["end"])
    tracks = {"guest": ep.tracks["guest"], "host": ep.tracks["host"]}
    words = edit_clip(cfg, clip, tracks, episode_words, scratch, rec)
    words, clip["caption_review"] = review_clip(ep, cfg, rec, clip, words, episode_words)
    return words


def revise_clip(ep: Episode, cfg: dict, rec: StageRecord, clip: dict, words: list[dict], feedback: str,
                scratch: Path, episode_words: Optional[list[dict]]) -> tuple[dict, list[dict], dict]:
    """Map the feedback, apply it; returns ``(clip, words, round record)``. Renders nothing."""
    reply = ask_json(cfg, rec, "revise", build_prompt(ep, clip, words, feedback),
                     system=REVISE_SYSTEM, max_tokens=4000)
    ops = resolve(reply, clip, words)
    extended = []
    before, after = ops["extend"]
    if before or after:
        clip = json.loads(json.dumps(clip))
        words = _extend(ep, cfg, rec, clip, before, after, episode_words or load_words(ep), scratch)
        extended = [f"extended by {before:.1f} s before and {after:.1f} s after (re-edited)"]
    clip, words, applied, unhandled = apply_ops(ep, clip, words, ops, cfg.get("linkedin_footer", ""))
    return clip, words, {"edits": reply, "applied": extended + applied, "unhandled": unhandled}


def run(ep: Episode, cfg: dict, rec: StageRecord) -> list[int]:
    """Revise and re-render every clip with an open review round; returns their numbers."""
    clips = load_clips(ep)
    state = load_review(ep)
    targets = [n for n in to_revise(clips, state)
               if next(c for c in clips if c["number"] == n).get("videos")]
    if not targets:
        logger.info("ℹ️ revise: no clip has open feedback")
        return []
    clip_words = load_clip_words(ep)
    episode_words = load_words(ep)
    scratch = work_dir(cfg, ep)
    prepare_fonts(cfg, scratch)
    encoder = cfg.get("video_encoder", "libx264")
    failed = []
    for number in targets:
        try:
            _revise_one(ep, cfg, rec, clips, clip_words, state, number, scratch, episode_words, encoder)
        except Exception:  # noqa: BLE001 — one clip's failure leaves its round open, the rest go on
            logger.exception("❌ clip %d: revise failed, its feedback stays open", number)
            failed.append(number)
    write_clips_md(ep, clips)
    if failed:
        raise RuntimeError(f"revise failed for clip(s) {failed}; their feedback is still open")
    return targets


def _revise_one(ep: Episode, cfg: dict, rec: StageRecord, clips: list[dict], clip_words: dict, state: dict,
                number: int, scratch: Path, episode_words: list[dict], encoder: str) -> None:
    index = next(k for k, c in enumerate(clips) if c["number"] == number)
    old = clips[index]
    entry = clip_state(state, number)
    feedback, version = open_round(entry)["feedback"], entry["version"]
    words = clip_words.get(str(number)) or []
    archive_version(ep, old, words, version)
    clip, words, record = revise_clip(ep, cfg, rec, old, words, feedback, scratch, episode_words)
    stale = []
    if clip["title"] != old["title"]:
        taken = {c["file"].lower() for c in clips if c.get("file") and c["number"] != number}
        clip["file"] = clip_filename(clip, taken)
        stale = clip_outputs(ep, old, "videos")
    new_videos = clip_outputs(ep, clip, "videos")
    for (name, layout), out in zip(LAYOUTS.items(), new_videos):
        render_clip(ep, clip, layout, output_words(clip, words), scratch, out, encoder)
        clip.setdefault("videos", {})[name] = out.relative_to(ep.package).as_posix()
    # A title change that windows_safe_filename strips (a "?" or ":") keeps the
    # old stem, so the "stale" files are the ones just rendered — keep those.
    stale = [p for p in stale if p not in new_videos]
    # The cover waits for the covers stage (after review); the old one is in the version folder.
    for path in [*stale, clip_outputs(ep, old, "covers")[0]]:
        path.unlink(missing_ok=True)
    clips[index] = clip
    clip_words[str(number)] = words
    save_clips(ep, clips)
    (ep.package / CLIP_WORDS_FILE).write_text(json.dumps(clip_words, ensure_ascii=False), encoding="utf-8")

    def close(c: dict) -> None:
        last = open_round(c)
        c.update(status="pending", version=version + 1)
        if not last:
            return
        # Feedback the owner added while this ran was not mapped: it opens the next round.
        extra = last["feedback"][len(feedback):].strip() if last["feedback"].startswith(feedback) else ""
        last.update(feedback=feedback, applied_at=now(), produced_version=version + 1, **record)
        if extra:
            c["rounds"].append({"at": now(), "feedback": extra, "version": version + 1})
            c["status"] = "changes"
    update(ep, number, close)
    logger.info("✅ clip %d revised → v%d: %s%s", number, version + 1, "; ".join(record["applied"]) or "no edit",
                f" · unhandled: {'; '.join(record['unhandled'])}" if record["unhandled"] else "")
