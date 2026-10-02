"""Write a new episode's ``episode.json`` from the tracks in its folder (issue #349).

    python -m podcast.init_episode "<episode folder>" [--guest "Full Name"] [--adjective brilliant]
        [--link "LinkedIn=https://..."] [--host-track <file>] [--guest-track <file>] [--force]

The tracks are found in ``<episode folder>/video editing/``:

* **recorder** sessions: ``recorder - <side> - <YYYY-MM-DD HHMMSS>.mp4``, one
  main recording per side (the ``remote-ref`` files are the sync stage's, not
  tracks). The side is in the name, the duration in the ``.json`` sidecar.
* **Riverside** downloads: one ``riverside_<speaker>_raw-synced-video…`` /
  ``<speaker>_raw-synced-video…`` / ``riverside_<session>-<speaker>`` file per
  speaker. The speaker whose first name is ``podcast.host.first`` is the host.

Everything that can be read off the files is filled in: the tracks, ``date``
(the recorder's file name, else the container's creation time, else the file
date), ``start_s`` / ``end_s`` (0 to the shorter track), ``website_slug`` (from
the guest's name), ``host_is_interviewee: false``. What only the owner knows
comes from flags or is left as a ``TODO`` placeholder, listed at the end.
An existing ``episode.json`` is never overwritten without ``--force``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

from podcast.episode import SPEAKERS, load_podcast_config

logger = logging.getLogger("podcast.init")

VIDEO_DIR = "video editing"
TODO = "TODO"
RECORDER_MAIN = re.compile(r"^recorder - (host|guest) - (\d{4}-\d{2}-\d{2}) \d{6}\.mp4$")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}


class InitError(Exception):
    """The folder's tracks cannot be told apart without the owner's help."""


def recorder_tracks(video_dir: Path) -> dict[str, list[Path]]:
    """Main recorder recordings per side."""
    found: dict[str, list[Path]] = {side: [] for side in SPEAKERS}
    for path in sorted(video_dir.glob("recorder - *.mp4")):
        match = RECORDER_MAIN.match(path.name)
        if match:
            found[match.group(1)].append(path)
    return found


def riverside_speaker(stem: str) -> Optional[str]:
    """The speaker token of a Riverside file name, or None for other files."""
    if "_raw-synced-video" in stem:
        token = stem.split("_raw-synced-video", 1)[0]
        return token[len("riverside_"):] if token.startswith("riverside_") else token
    if stem.startswith("riverside_") and "-" in stem:
        return stem.rsplit("-", 1)[1]
    return None


def riverside_tracks(video_dir: Path, host_first: str) -> dict[str, list[Path]]:
    """Riverside per-speaker tracks, split into host and guest by first name."""
    found: dict[str, list[Path]] = {side: [] for side in SPEAKERS}
    host = host_first.strip().lower()
    for path in sorted(p for p in video_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT):
        token = riverside_speaker(path.stem)
        if token is None:
            continue
        first = next((w for w in re.split(r"[\s_.\-]+", token.lower()) if w), "")
        found["host" if first == host else "guest"].append(path)
    return found


def _resolve(folder: Path, name: str) -> Path:
    """A ``--host-track`` / ``--guest-track`` value: a path relative to the
    episode folder, or a file name inside ``video editing/``."""
    for candidate in (folder / name, folder / VIDEO_DIR / name):
        if candidate.is_file():
            return candidate
    raise InitError(f"track not found: {name}")


def discover(folder: Path, host_first: str, overrides: dict[str, Optional[str]]) -> tuple[str, dict[str, Path]]:
    """``(layout, {side: track})``; raises ``InitError`` when a side has no
    track or several, naming the candidates and the flag that settles it."""
    video_dir = folder / VIDEO_DIR
    if not video_dir.is_dir():
        raise InitError(f"no '{VIDEO_DIR}' folder in {folder}")
    found = recorder_tracks(video_dir)
    layout = "recorder"
    if not any(found.values()):
        found, layout = riverside_tracks(video_dir, host_first), "riverside"
    tracks: dict[str, Path] = {}
    problems = []
    for side in SPEAKERS:
        if overrides.get(side):
            tracks[side] = _resolve(folder, overrides[side])
        elif len(found[side]) == 1:
            tracks[side] = found[side][0]
        elif not found[side]:
            problems.append(f"no {side} track found; pass --{side}-track <file>")
        else:
            names = ", ".join(p.name for p in found[side])
            problems.append(f"{len(found[side])} {side} tracks ({names}); pass --{side}-track <file>")
    if problems:
        raise InitError(f"{layout} layout: " + "; ".join(problems))
    return layout, tracks


def _ffprobe_format(path: Path) -> dict:
    from podcast.media import NO_WINDOW, _tool  # noqa: PLC0415

    out = subprocess.run([_tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:format_tags=creation_time",
                          "-of", "json", str(path)], check=True, capture_output=True, timeout=120,
                         creationflags=NO_WINDOW)
    return json.loads(out.stdout or b"{}").get("format") or {}


def duration(path: Path) -> float:
    """Seconds: the recorder sidecar's figure when there is one, else ffprobe."""
    sidecar = path.with_suffix(".json")
    if sidecar.exists():
        seconds = json.loads(sidecar.read_text(encoding="utf-8")).get("duration_s")
        if seconds:
            return float(seconds)
    return float(_ffprobe_format(path).get("duration") or 0.0)


def recording_date(path: Path) -> str:
    """``YYYY-MM-DD`` of the recording: the recorder's file name, else the
    container's creation time, else the file's modification date."""
    match = RECORDER_MAIN.match(path.name)
    if match:
        return match.group(2)
    created = (_ffprobe_format(path).get("tags") or {}).get("creation_time", "")
    if re.match(r"\d{4}-\d{2}-\d{2}", created):
        return created[:10]
    logger.warning("⚠️ %s has no creation time; using the file date", path.name)
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def parse_links(values: list[str]) -> list[dict]:
    links = []
    for value in values:
        label, sep, url = value.partition("=")
        if not sep or not label.strip() or not url.strip():
            raise InitError(f"--link needs LABEL=URL, got {value!r}")
        links.append({"label": label.strip(), "url": url.strip()})
    return links


def build_spec(folder: Path, cfg: dict, *, guest: Optional[str] = None, guest_display: Optional[str] = None,
               pronoun: Optional[str] = None, adjective: Optional[str] = None, links: Optional[list[dict]] = None,
               host_track: Optional[str] = None, guest_track: Optional[str] = None) -> tuple[dict, list[str]]:
    """The ``episode.json`` content and the fields left as placeholders."""
    host_first = (cfg.get("host") or {}).get("first")
    if not host_first:
        raise InitError("podcast.host.first is not set in config.json (it tells the host's track apart)")
    layout, tracks = discover(folder, host_first, {"host": host_track, "guest": guest_track})
    lengths = {side: duration(path) for side, path in tracks.items()}
    todo = []
    if not guest:
        guest = f"{TODO} guest name"
        todo += ["guest", "guest_display", "guest_first", "website_slug"]
    if not adjective:
        adjective = f"{TODO} adjective"
        todo.append("adjective")
    if not pronoun:
        todo.append("guest_pronoun_possessive (defaulted to 'their')")
    if not links:
        links = [{"label": "LinkedIn", "url": TODO}]
        todo.append("links")
    todo.append("youtube_url (once the episode is on YouTube)")
    spec = {
        "guest": guest,
        "guest_display": guest_display or guest,
        "guest_first": guest.split()[0],
        "guest_pronoun_possessive": pronoun or "their",
        "tracks": {side: tracks[side].relative_to(folder).as_posix() for side in SPEAKERS},
        "host_is_interviewee": False,
        "start_s": 0,
        "end_s": round(min(lengths.values()), 1),
        "adjective": adjective,
        "date": recording_date(tracks["host"]),
        "website_slug": slugify(guest),
        "youtube_url": "",
        "links": links,
    }
    logger.info("ℹ️ %s layout: host %s (%.0f s), guest %s (%.0f s)", layout, tracks["host"].name, lengths["host"],
                tracks["guest"].name, lengths["guest"])
    return spec, todo


def write_episode(folder: Path, spec: dict, *, force: bool = False) -> Path:
    path = folder / "episode.json"
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to overwrite it")
    path.write_text(json.dumps(spec, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: Optional[list[str]] = None, cfg: Optional[dict] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("episode", help="episode folder, with the tracks in 'video editing/'")
    parser.add_argument("--guest", help="the guest's full name, as used in file names")
    parser.add_argument("--guest-display", help="the name used in copy, e.g. with a title (default: --guest)")
    parser.add_argument("--pronoun", help="the guest's possessive pronoun (default: their)")
    parser.add_argument("--adjective", help="'my conversation with the <adjective> <guest>'")
    parser.add_argument("--link", action="append", default=[], metavar="LABEL=URL",
                        help="a 'where to find' link for the website page; repeatable")
    parser.add_argument("--host-track", help="the owner's track, when discovery finds none or several")
    parser.add_argument("--guest-track", help="the guest's track, when discovery finds none or several")
    parser.add_argument("--force", action="store_true", help="overwrite an existing episode.json")
    args = parser.parse_args(argv)

    folder = Path(args.episode)
    try:
        cfg = cfg if cfg is not None else load_podcast_config()
        spec, todo = build_spec(folder, cfg, guest=args.guest, guest_display=args.guest_display,
                                pronoun=args.pronoun, adjective=args.adjective, links=parse_links(args.link),
                                host_track=args.host_track, guest_track=args.guest_track)
        path = write_episode(folder, spec, force=args.force)
    except FileExistsError as exc:
        logger.error("❌ %s", exc)
        return 1
    except (InitError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        logger.error("❌ %s", exc)
        return 2
    logger.info("✅ wrote %s", path)
    if todo:
        logger.warning("⚠️ still to fill by hand: %s", ", ".join(todo))
    return 0


if __name__ == "__main__":
    from config.logger_config import setup_logger  # noqa: PLC0415

    setup_logger("podcast", file_logging=False)  # "podcast.init" and "podcast.media" propagate to it
    sys.exit(main())
