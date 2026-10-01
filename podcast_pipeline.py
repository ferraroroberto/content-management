#!/usr/bin/env python
"""Podcast episode pipeline (issue #333).

    python podcast_pipeline.py "<episode folder>" [--stages transcribe,clean,...] [--force]

Stages run in order and each one writes its output into
``<episode folder>/<podcast.package_dirname>/``. A stage whose output already
exists is skipped unless ``--force`` is given, so a run resumes where the
last one stopped. Nothing is published, posted or written to Notion.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Callable

sys.path.append(str(Path(__file__).parent))
from config.console import force_utf8_stdio  # noqa: E402

force_utf8_stdio()
from config.logger_config import setup_logger  # noqa: E402
from podcast.episode import Episode, load_episode, load_podcast_config  # noqa: E402
from podcast.metrics import stage_timer  # noqa: E402

logger: logging.Logger = logging.getLogger("podcast")


def _transcribe(ep, cfg, rec, force):
    from podcast import transcribe  # noqa: PLC0415
    transcribe.run(ep, cfg, rec)


def _clean(ep, cfg, rec, force):
    from podcast import package  # noqa: PLC0415
    package.run_clean(ep, cfg, rec)


def _select(ep, cfg, rec, force):
    from podcast import clips  # noqa: PLC0415
    clips.run_select(ep, cfg, rec)


def _copy(ep, cfg, rec, force):
    from podcast import clips  # noqa: PLC0415
    clips.run_copy(ep, cfg, rec)


def _render(ep, cfg, rec, force):
    from podcast import render  # noqa: PLC0415
    render.run(ep, cfg, rec, force=force)


def _episode(ep, cfg, rec, force):
    from podcast import package  # noqa: PLC0415
    package.run_episode_copy(ep, cfg, rec)


def _covers(ep, cfg, rec, force):
    from podcast import covers  # noqa: PLC0415
    covers.run(ep, cfg, rec, force=force)


def _package(ep, cfg, rec, force):
    from podcast import package  # noqa: PLC0415
    package.run_package(ep, cfg, rec)


def _score(ep, cfg, rec, force):
    from podcast import score  # noqa: PLC0415
    score.run(ep, cfg, rec)


def _exists(rel: str) -> Callable[[Episode], bool]:
    return lambda ep: (ep.package / rel.format(base=ep.base_name)).exists()


def _every_clip_has(kind: str) -> Callable[[Episode], bool]:
    """Done only when every selected clip has its output — a crash mid-stage
    must not look finished on resume (the stage itself skips files on disk)."""
    def done(ep: Episode) -> bool:
        from podcast.clips import load_clips  # noqa: PLC0415
        from podcast.render import clip_outputs  # noqa: PLC0415
        clips = load_clips(ep)
        return bool(clips) and all(
            c.get("file") and all(path.exists() for path in clip_outputs(ep, c, kind)) for c in clips)
    return done


# stage name → (runner, "is the output already there?")
STAGES: dict[str, tuple[Callable, Callable[[Episode], bool]]] = {
    "transcribe": (_transcribe, _exists("transcript/turns.json")),
    "clean": (_clean, _exists("{base} - transcript.md")),
    "select": (_select, _exists("clips.json")),
    "copy": (_copy, _exists("clips.md")),
    "render": (_render, _every_clip_has("videos")),
    "episode": (_episode, _exists("episode_copy.json")),
    "covers": (_covers, lambda ep: _exists("{base} (1920x1080)_text.png")(ep)
               and _every_clip_has("covers")(ep)),
    "package": (_package, _exists("{base}.docx")),
    "score": (_score, _exists("scores.json")),
}


def main(argv: list[str] | None = None) -> int:
    global logger
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episode", help="episode folder containing episode.json")
    parser.add_argument("--stages", default=",".join(STAGES), help="comma-separated subset, in pipeline order")
    parser.add_argument("--force", action="store_true", help="re-run stages whose output exists")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    logger = setup_logger("podcast", file_logging=False,
                          level=logging.DEBUG if args.debug else logging.INFO)

    wanted = [s.strip() for s in args.stages.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in STAGES]
    if unknown:
        logger.error("❌ unknown stage(s): %s (known: %s)", unknown, ", ".join(STAGES))
        return 2

    cfg = load_podcast_config()
    ep = load_episode(Path(args.episode), cfg)
    ep.package.mkdir(parents=True, exist_ok=True)
    logger.info("ℹ️ episode: %s → %s", ep.folder.name, ep.package)

    for name in STAGES:
        if name not in wanted:
            continue
        runner, is_done = STAGES[name]
        if is_done(ep) and not args.force:
            logger.info("ℹ️ %s: output exists, skipping (use --force to redo)", name)
            continue
        logger.info("▶ %s", name)
        try:
            with stage_timer(ep.package, name) as rec:
                runner(ep, cfg, rec, args.force)
        except Exception:
            logger.exception("❌ stage %s failed", name)
            return 1
        logger.info("✅ %s done in %.0fs", name, rec.wall_s)
    from podcast.report import write_report  # noqa: PLC0415
    path = write_report(ep, cfg)
    logger.info("✅ metrics: %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
