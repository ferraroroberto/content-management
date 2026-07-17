"""CLI for the doc-capture engine.

Usage (from the repo root, app running on :8501)::

    & .\\.venv\\Scripts\\python.exe -m config.doc_capture capture [--force] [--only NAME] [--headed]
    & .\\.venv\\Scripts\\python.exe -m config.doc_capture readme
    & .\\.venv\\Scripts\\python.exe -m config.doc_capture all [--force] [--only NAME] [--headed]
"""

from __future__ import annotations

import argparse
import logging
import sys

from config.doc_capture import engine


def _add_capture_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=engine.DEFAULT_BASE_URL,
                        help=f"control-panel URL (default {engine.DEFAULT_BASE_URL})")
    parser.add_argument("--only", action="append", metavar="NAME",
                        help="capture only this feature (repeatable)")
    parser.add_argument("--force", action="store_true",
                        help="recapture even when the input hash is unchanged")
    parser.add_argument("--headed", action="store_true",
                        help="show the browser window (debugging)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m config.doc_capture",
        description="Deterministic, fail-safe screenshots of the control panel + README regen.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    _add_capture_args(sub.add_parser("capture", help="capture stale features (input-hash idempotent)"))
    sub.add_parser("readme", help="regenerate the README section between the docs-shots markers")
    _add_capture_args(sub.add_parser("all", help="capture, then regenerate the README"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    manifest = engine.load_manifest()
    if args.cmd in ("capture", "all"):
        items = engine.plan_features(manifest, force=args.force, only=args.only)
        captured = engine.capture_features(
            manifest, items, base_url=args.base_url, headless=not args.headed
        )
        if captured:
            engine.save_manifest(manifest)
    if args.cmd in ("readme", "all"):
        engine.regenerate_readme(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
