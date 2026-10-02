"""The clip review from the command line — the same ``review.json`` the Podcast
tab writes (issue #339), for a review walked through in a terminal (issue #350).

    python -m podcast.review_cli "<episode folder>" show
    python -m podcast.review_cli "<episode folder>" approve 1 3 5
    python -m podcast.review_cli "<episode folder>" drop 7
    python -m podcast.review_cli "<episode folder>" pending 7
    python -m podcast.review_cli "<episode folder>" feedback 4 "at 0:12 'happy' should be 'crappy'"

``feedback`` opens (or extends) a round; ``podcast_pipeline.py --stages revise``
applies it. Nothing here renders or calls a model.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from podcast import review_state
from podcast.clips import clip_length, load_clips
from podcast.episode import load_episode, load_podcast_config

logger = logging.getLogger("podcast.review_cli")

STATUS_OF = {"approve": "approved", "drop": "dropped", "pending": "pending"}


def show(ep, clips: list[dict]) -> None:
    state = review_state.load_review(ep)
    for c in clips:
        entry = review_state.clip_state(state, c["number"])
        videos = c.get("videos") or {}
        logger.info("ℹ️ %2d. %s · %s · v%d · %.0f s · 1x1 %s · 9x16 %s", c["number"], c.get("title", ""),
                    entry["status"], entry["version"], clip_length(c), videos.get("1x1", "-"), videos.get("9x16", "-"))
        last = review_state.open_round(entry)
        if last:
            logger.info("      open feedback: %s", last["feedback"].replace("\n", " / "))
        unhandled = (entry.get("rounds") or [{}])[-1].get("unhandled")
        if unhandled:
            logger.warning("⚠️     not applied last round: %s", "; ".join(unhandled))
    logger.info("ℹ️ review: %s", review_state.summary(clips, state))


def main(argv: Optional[list[str]] = None, cfg: Optional[dict] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episode", help="episode folder containing episode.json")
    parser.add_argument("action", choices=["show", *STATUS_OF, "feedback"])
    parser.add_argument("args", nargs="*", help="clip numbers; for feedback: one number, then the note")
    args = parser.parse_args(argv)

    ep = load_episode(Path(args.episode), cfg if cfg is not None else load_podcast_config())
    clips = load_clips(ep)
    if not clips:
        logger.error("❌ no clips yet in %s: run the pipeline first", ep.package)
        return 2
    if args.action == "show":
        show(ep, clips)
        return 0
    known = {c["number"] for c in clips}
    if args.action == "feedback":
        if len(args.args) < 2 or not args.args[0].isdigit():
            logger.error("❌ feedback needs a clip number and the note")
            return 2
        numbers, note = [int(args.args[0])], " ".join(args.args[1:])
    else:
        if not args.args or not all(a.isdigit() for a in args.args):
            logger.error("❌ %s needs one or more clip numbers", args.action)
            return 2
        numbers, note = [int(a) for a in args.args], ""
    unknown = [n for n in numbers if n not in known]
    if unknown:
        logger.error("❌ no clip %s (clips: %s)", unknown, sorted(known))
        return 2
    for number in numbers:
        if note:
            review_state.request_changes(ep, number, note)
        else:
            review_state.set_status(ep, number, STATUS_OF[args.action])
        logger.info("✅ clip %d: %s", number, "feedback recorded" if note else STATUS_OF[args.action])
    logger.info("ℹ️ review: %s", review_state.summary(clips, review_state.load_review(ep)))
    return 0


if __name__ == "__main__":
    from config.logger_config import setup_logger  # noqa: PLC0415

    setup_logger("podcast", file_logging=False)
    sys.exit(main())
