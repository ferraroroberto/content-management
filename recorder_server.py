#!/usr/bin/env python
"""Serve the podcast recorder for one episode (issue #341).

    python recorder_server.py "<episode folder>" [--port 8470] [--host 127.0.0.1]

Prints the two links (host side, guest side) and serves until stopped. The
links stay the same across restarts (``<episode folder>/recorder.json``).
Recordings land in ``<episode folder>/video editing/``. A guest outside this
machine needs an HTTPS URL for the camera to work: see ``recorder/README.md``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from config.console import force_utf8_stdio  # noqa: E402

force_utf8_stdio()
import uvicorn  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from config.logger_config import setup_logger  # noqa: E402
from podcast.episode import load_podcast_config  # noqa: E402
from recorder.server import create_app, ensure_links, ice_servers  # noqa: E402
from recorder.store import ChunkStore  # noqa: E402

DEFAULT_PORT = 8470


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episode", help="episode folder (absolute, or a folder name under podcast.episodes_root)")
    parser.add_argument("--host", help="bind address (default: podcast.recorder.host or 127.0.0.1)")
    parser.add_argument("--port", type=int, help=f"port (default: podcast.recorder.port or {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    logger = setup_logger("recorder", file_logging=False, level=logging.INFO)

    load_dotenv(Path(__file__).parent / ".env")  # the TURN credential lives there
    cfg = load_podcast_config()
    rcfg = cfg.get("recorder") or {}
    folder = Path(args.episode)
    if not folder.is_absolute():
        folder = Path(cfg["episodes_root"]) / folder
    if not folder.is_dir():
        logger.error("❌ no episode folder %s", folder)
        return 2
    staging = Path(rcfg.get("staging_dir") or Path(tempfile.gettempdir()) / "cm-recorder") / folder.name
    host, port = args.host or rcfg.get("host", "127.0.0.1"), args.port or rcfg.get("port", DEFAULT_PORT)
    links = ensure_links(folder)
    base = (rcfg.get("public_url") or f"http://{host}:{port}").rstrip("/")
    for token, side in sorted(links.items(), key=lambda kv: kv[1]):
        logger.info("🔗 %s link: %s/r/%s", side, base, token)
    logger.info("ℹ️ recordings go to %s; chunks are staged in %s", folder / "video editing", staging)
    ice = ice_servers(rcfg)
    logger.info("ℹ️ call relay: %s", "STUN + TURN" if len(ice) > 1 else "STUN only (no TURN configured: strict "
                "networks may not connect)")
    uvicorn.run(create_app(folder, ChunkStore(staging), links, ice), host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
