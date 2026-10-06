#!/usr/bin/env python
"""Demo-video pipeline (issues #359, #360, #361).

    python demo_video_pipeline.py "<demo folder>" [--stages record,prep,check,render] [--cut <id>] [--preview] [--force]
    python demo_video_pipeline.py "<demo folder>" --status

A demo folder holds ``demo.json`` (the storyboard: cuts, copy per language,
scenes, soundtrack), its marks file, a media folder with the clips and music,
and ``out/``. Stages, in order:

- ``record``: boot the app through the demo's throwaway driver (a disposable
  instance, never the live app), run the ``recording`` takes and beats in a
  real browser, and write ``rec/<lang>/<page>.webm`` plus the marks file, for
  every language the selected cuts use.
- ``prep``: transcode each clip's raw ``source`` recording to CFR H.264,
  measure ``legend.measure`` state timelines into the marks file, read the
  music (loudness envelopes, best scenes to bring a closing track in) and
  write contact sheets of the recordings to ``out/prep/``.
- ``check``: overrun, privacy and licence. A ``fail`` or ``unknown`` stops
  the run (exit 3). ``render`` always runs the checks first, so a failing
  demo never renders.
- ``render``: ``out/<cut>.mp4`` for every cut (or only ``--cut``), then
  verifies the file (size, fps, length, audio, a fade at the end) and writes
  ``out/<cut>.sheet.png``; ``--preview`` renders ``out/<cut>.preview.mp4`` at
  half scale instead, the fast way to review. A cut whose output exists is
  skipped unless ``--force``.

Nothing is published, posted or uploaded. ``--status`` runs nothing and
writes nothing: it validates the storyboard, lists missing media and says
which cuts are rendered; it exits 2 when the storyboard is invalid or media
is missing.

The interactive way to make a demo is the ``/demo-video`` skill; see
``demo_video/README.md``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from config.console import force_utf8_stdio  # noqa: E402

force_utf8_stdio()
from pydantic import ValidationError  # noqa: E402

from config.logger_config import setup_logger  # noqa: E402
from demo_video.checks import run_checks, write_report  # noqa: E402
from demo_video.media import contact_sheet, verify_output  # noqa: E402
from demo_video.prep import run_prep  # noqa: E402
from demo_video.record import run_recording  # noqa: E402
from demo_video.render import load_config, output_path, render_cut  # noqa: E402
from demo_video.storyboard import ASPECTS, Cut, Demo, load_demo, missing_media, scene_frames, total_frames, validate  # noqa: E402

logger: logging.Logger = logging.getLogger("demo_video")

STAGES = ("record", "prep", "check", "render")
CHECKS_FAILED = 3


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


def checks(folder: Path, demo: Demo, cuts: list[Cut]) -> bool:
    results = run_checks(demo, folder, cuts)
    for r in results:
        (logger.info if r.ok else logger.error)("%s", r.line())
    write_report(folder, results)
    return all(r.ok for r in results)


def render(folder: Path, demo: Demo, cut: Cut, *, preview: bool) -> bool:
    out = render_cut(folder, demo, cut, preview=preview, cfg=load_config())
    mids = [(f + d / 2) / demo.fps for f, d in scene_frames(demo)]
    contact_sheet(out, mids, out.with_suffix(".sheet.png"))
    if preview:
        return True
    width, height = ASPECTS[cut.aspect]
    result = verify_output(out, width=width, height=height, fps=demo.fps, duration=total_frames(demo) / demo.fps)
    (logger.info if result.ok else logger.error)("%s", result.line())
    return result.ok


def main(argv: list[str] | None = None) -> int:
    global logger
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", help="demo folder containing demo.json")
    parser.add_argument("--stages", default="render", help=f"comma-separated subset of {', '.join(STAGES)} (default: render)")
    parser.add_argument("--cut", help="only this cut id")
    parser.add_argument("--preview", action="store_true", help="half-scale preview render (out/<cut>.preview.mp4)")
    parser.add_argument("--force", action="store_true", help="redo outputs that exist (transcodes, renders)")
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
    if args.status:
        return status(folder, demo, validate(demo, folder))
    try:
        cuts = [demo.cut(args.cut)] if args.cut else demo.cuts
    except KeyError as exc:
        logger.error("❌ %s", exc.args[0])
        return 2

    if "record" in wanted:
        if demo.recording is None:
            logger.error("❌ demo.json has no `recording` block")
            return 2
        for lang in sorted({c.lang for c in cuts}):
            logger.info("▶ record %s", lang)
            try:
                run_recording(demo, folder, lang)
            except Exception:
                logger.exception("❌ record %s failed", lang)
                return 1
    if "prep" in wanted:
        logger.info("▶ prep")
        try:
            run_prep(demo, folder, force=args.force)
        except Exception:
            logger.exception("❌ prep failed")
            return 1
    if not ("check" in wanted or "render" in wanted):
        return 0

    errors = validate(demo, folder)  # after prep: measured states now exist
    if errors:
        for e in errors:
            logger.error("❌ %s", e)
        return 2
    for cut in cuts:
        missing = missing_media(folder, demo, cut)
        if missing:
            logger.error("❌ cut %s: media missing: %s", cut.id, ", ".join(missing))
            return 2
    logger.info("▶ check")
    if not checks(folder, demo, cuts):
        logger.error("❌ checks failed — nothing rendered (report: out/prep/checks.json)")
        return CHECKS_FAILED
    if "render" not in wanted:
        return 0

    for cut in cuts:
        if output_path(folder, cut, preview=args.preview).is_file() and not args.force:
            logger.info("ℹ️ render %s: output exists, skipping (use --force to redo)", cut.id)
            continue
        logger.info("▶ render %s", cut.id)
        try:
            if not render(folder, demo, cut, preview=args.preview):
                return 1
        except Exception:
            logger.exception("❌ render %s failed", cut.id)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
