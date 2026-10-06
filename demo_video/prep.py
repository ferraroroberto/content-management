"""The prep stage: transcode recordings, measure state timelines, read the music, contact sheets (issue #360)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from demo_video import media
from demo_video.checks import state_timeline
from demo_video.storyboard import STATES_KEY, Demo, WindowStates, clip_files, load_marks, media_root, scene_frames, states_key

logger = logging.getLogger("demo_video.prep")


def languages(demo: Demo) -> list[str]:
    return sorted({c.lang for c in demo.cuts})


def transcode_sources(demo: Demo, folder: Path, *, force: bool = False) -> list[Path]:
    """Every clip with a ``source`` recording → its CFR H.264 ``file`` (when missing, older, or forced)."""
    done = []
    for lang in languages(demo):
        files = clip_files(demo, lang)
        for cid, spec in demo.clips.items():
            if not spec.source:
                continue
            rec = folder / spec.source.replace("{lang}", lang)
            dst = media_root(folder, demo) / files[cid]
            if not rec.is_file():
                logger.warning("⚠️ %s (%s): recording not found: %s", cid, lang, rec)
                continue
            if force or media.needs_transcode(rec, dst):
                logger.info("ℹ️ transcoding %s → %s", rec.name, dst)
                done.append(media.transcode(rec, dst))
    return done


def measure_states(demo: Demo, folder: Path) -> dict[str, int]:
    """Measure every ``legend.measure`` and store it in that language's marks file under ``_states``."""
    counts: dict[str, int] = {}
    for lang in languages(demo):
        marks_path = folder / demo.marks.replace("{lang}", lang)
        marks = load_marks(folder, demo, lang)
        if not marks:
            continue
        files = clip_files(demo, lang)
        changed = False
        for s in demo.scenes:
            if not isinstance(s, WindowStates) or s.legend.measure is None:
                continue
            ref, m = s.legend.anchor, s.legend.measure
            start, end = marks[demo.clips[ref.video].marks or ref.video][ref.beat]
            timeline = state_timeline(media_root(folder, demo) / files[ref.video], box=m.box.model_dump(),
                                      palette=m.palette, start=start, end=end, step_s=m.step_s)
            marks.setdefault(STATES_KEY, {})[states_key(ref)] = timeline
            counts[f"{lang}:{s.id}"] = len(timeline)
            changed = True
            logger.info("ℹ️ %s %s: states %s", lang, s.id, timeline)
        if changed:
            marks_path.write_text(json.dumps(marks, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return counts


def read_music(demo: Demo, folder: Path) -> dict[str, list]:
    """Loudness envelopes per track and, for a track that ends with the video, the best scenes to bring it in."""
    out_dir = folder / "out" / "prep"
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = demo.fps
    total = sum(d for _, d in scene_frames(demo)) / fps
    boundaries = [(s.id, f / fps) for s, (f, _) in zip(demo.scenes, scene_frames(demo))][1:]
    suggestions: dict[str, list] = {}
    for cut in demo.cuts:
        report = {"cut": cut.id, "tracks": []}
        for t in cut.soundtrack:
            path = media_root(folder, demo) / t.file.replace("{lang}", cut.lang)
            if not path.is_file():
                continue
            env = media.loudness_envelope(path)
            entry = {"file": t.file, "seconds": len(env), "envelope_db": env}
            if t.align_end_tail_s is not None:
                ranked = media.suggest_swaps(env, len(env) - t.align_end_tail_s, total, boundaries)
                entry["entry_points"] = [{"scene": sc, "at_s": at, "build_db": db} for sc, at, db in ranked[:5]]
                suggestions[f"{cut.id}:{t.file}"] = ranked[:3]
                for sc, at, db in ranked[:3]:
                    logger.info("ℹ️ %s %s: bring it in at scene %s (%.1f s): builds %+.1f dB", cut.id, t.file, sc, at, db)
            report["tracks"].append(entry)
        (out_dir / f"music-{cut.id}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return suggestions


def recording_sheets(demo: Demo, folder: Path) -> list[Path]:
    """One contact sheet per clip and language: a frame at the middle of each beat (8 even frames without marks)."""
    sheets = []
    for lang in languages(demo):
        marks = load_marks(folder, demo, lang)
        for cid, file in clip_files(demo, lang).items():
            path = media_root(folder, demo) / file
            if not path.is_file():
                continue
            beats = marks.get(demo.clips[cid].marks or cid, {})
            if beats:
                times = [(a + b) / 2 for a, b in beats.values()]
            else:
                length = media.probe(path)["duration"]
                times = [length * (i + 0.5) / 8 for i in range(8)]
            sheets.append(media.contact_sheet(path, times, folder / "out" / "prep" / f"{lang}-{cid}.png"))
    return sheets


def run_prep(demo: Demo, folder: Path, *, force: bool = False) -> None:
    transcoded = transcode_sources(demo, folder, force=force)
    states = measure_states(demo, folder)
    read_music(demo, folder)
    sheets = recording_sheets(demo, folder)
    logger.info("✅ prep: %d transcoded, %d state timelines, %d contact sheets in out/prep/",
                len(transcoded), len(states), len(sheets))
