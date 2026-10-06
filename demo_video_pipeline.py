#!/usr/bin/env python
"""Demo-video pipeline (issue #359).

    python demo_video_pipeline.py "<demo folder>" [--stages render] [--cut <id>] [--preview] [--force]
    python demo_video_pipeline.py "<demo folder>" --status

A demo folder holds ``demo.json`` (the storyboard: cuts, copy per language,
scenes, soundtrack), its marks file, a media folder with the recorded clips
and music, and ``out/``. ``render`` writes ``out/<cut>.mp4`` for every cut (or
only ``--cut``); ``--preview`` renders ``out/<cut>.preview.mp4`` at half
scale, the fast way to review. A cut whose output exists is skipped unless
``--force``. Nothing is published, posted or uploaded.

``--status`` runs nothing and writes nothing: it validates the storyboard,
lists missing media and says which cuts are rendered; it exits 2 when the
storyboard is invalid or media is missing.

The interactive way to make a demo is the ``/demo-video`` skill; see
``demo_video/README.md``.
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
from pydantic import ValidationError  # noqa: E402

from config.logger_config import setup_logger  # noqa: E402
from demo_video.render import load_config, output_path, render_cut  # noqa: E402
from demo_video.storyboard import Cut, Demo, load_demo, missing_media, total_frames, validate  # noqa: E402

logger: logging.Logger = logging.getLogger("demo_video")


def _render(folder: Path, demo: Demo, cut: Cut, args: argparse.Namespace) -> None:
    render_cut(folder, demo, cut, preview=args.preview, cfg=load_config())


# stage name → (runner, "is this cut's output already there?")
STAGES: dict[str, tuple[Callable, Callable[[Path, Cut, argparse.Namespace], bool]]] = {
    "render": (_render, lambda folder, cut, args: output_path(folder, cut, preview=args.preview).is_file()),
}


def status(folder: Path, demo: Demo, errors: list[str]) -> int:
    """Log where the demo stands; read-only. 2 when the storyboard is invalid or media is missing."""
    logger.info("ℹ️ demo: %s — %d scenes, %.1f s", demo.title, len(demo.scenes), total_frames(demo) / demo.fps)
    for e in errors:
        logger.error("❌ %s", e)
    missing_any = False
    for cut in demo.cuts:
        missing = missing_media(folder, demo, cut)
        missing_any = missing_any or bool(missing)
        final, preview = output_path(folder, cut, preview=False), output_path(folder, cut, preview=True)
        state = "rendered" if final.is_file() else "preview only" if preview.is_file() else "to render"
        logger.info("ℹ️ cut %s (%s, %s, %s): %s", cut.id, cut.lang, cut.aspect, "public" if cut.public else "private", state)
        for f in missing:
            logger.error("❌ cut %s: media missing: %s", cut.id, f)
    return 2 if errors or missing_any else 0


def main(argv: list[str] | None = None) -> int:
    global logger
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="demo folder containing demo.json")
    parser.add_argument("--stages", default=",".join(STAGES), help="comma-separated subset, in pipeline order")
    parser.add_argument("--cut", help="only this cut id")
    parser.add_argument("--preview", action="store_true", help="half-scale preview render (out/<cut>.preview.mp4)")
    parser.add_argument("--force", action="store_true", help="re-run stages whose output exists")
    parser.add_argument("--status", action="store_true", help="say where the demo stands; run and write nothing")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    logger = setup_logger("demo_video", file_logging=False, level=logging.DEBUG if args.debug else logging.INFO)

    wanted = [s.strip() for s in args.stages.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in STAGES]
    if unknown:
        logger.error("❌ unknown stage(s): %s (known: %s)", unknown, ", ".join(STAGES))
        return 2

    folder = Path(args.folder)
    try:
        demo = load_demo(folder)
    except FileNotFoundError:
        logger.error("❌ no demo.json in %s", folder)
        return 2
    except ValidationError as exc:
        logger.error("❌ demo.json is invalid:\n%s", exc)
        return 2
    errors = validate(demo, folder)
    if args.status:
        return status(folder, demo, errors)
    if errors:
        for e in errors:
            logger.error("❌ %s", e)
        return 2

    try:
        cuts = [demo.cut(args.cut)] if args.cut else demo.cuts
    except KeyError as exc:
        logger.error("❌ %s", exc.args[0])
        return 2
    for name in STAGES:
        if name not in wanted:
            continue
        runner, is_done = STAGES[name]
        for cut in cuts:
            if is_done(folder, cut, args) and not args.force:
                logger.info("ℹ️ %s %s: output exists, skipping (use --force to redo)", name, cut.id)
                continue
            missing = missing_media(folder, demo, cut)
            if missing:
                logger.error("❌ %s %s: media missing: %s", name, cut.id, ", ".join(missing))
                return 2
            logger.info("▶ %s %s", name, cut.id)
            try:
                runner(folder, demo, cut, args)
            except Exception:
                logger.exception("❌ %s %s failed", name, cut.id)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
