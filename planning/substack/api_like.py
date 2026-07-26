r"""Manual CLI — react to / remove a reaction from a Substack Note (P2, issue #186).

Uses the native HTTP API (cookie auth). NOT part of the daily cron — this repo
has no existing "like" workflow to slot into, so this is a small standalone
tool alongside api_pull.py / api_create.py.

Usage (from the repo root):
    & .\.venv\Scripts\python.exe -m planning.substack.api_like --note NOTE_ID_OR_URL
    & .\.venv\Scripts\python.exe -m planning.substack.api_like --note NOTE_ID_OR_URL --unlike

    --note VALUE   a Note id, or its https://substack.com/@handle/note/c-<id> URL
    --unlike       remove the reaction instead of adding it
"""

from __future__ import annotations

import argparse
import re

from planning.substack.api_client import react_to_note, unreact_to_note
from planning.substack.substack_session import configure_logger

_NOTE_ID_IN_URL = re.compile(r"/note/c-(\d+)")


def _resolve_note_id(value: str) -> str:
    """Accept either a bare note id or its public permalink."""
    match = _NOTE_ID_IN_URL.search(value)
    return match.group(1) if match else value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="React to (or remove a reaction from) a Substack Note.")
    parser.add_argument("--note", required=True, help="Note id, or its .../note/c-<id> URL.")
    parser.add_argument("--unlike", action="store_true", help="Remove the reaction instead of adding it.")
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = configure_logger("substack_api_like", debug=args.debug)
    note_id = _resolve_note_id(args.note)

    if args.unlike:
        unreact_to_note(note_id)
        logger.info("💔 Removed reaction from note %s", note_id)
    else:
        react_to_note(note_id)
        logger.info("❤️ Reacted to note %s", note_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
