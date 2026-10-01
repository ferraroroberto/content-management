"""Episode descriptor (``episode.json``) and podcast config loading.

``episode.json`` sits in the episode folder (OneDrive, private). Keys:

* ``guest`` — full name as used in file names; ``guest_display`` — name used
  in copy (e.g. with a title); ``guest_first``; ``guest_pronoun_possessive``.
* ``tracks`` — ``{"guest": <rel path>, "host": <rel path>}``; "host" is always
  the owner's own track, whoever asks the questions.
* ``host_is_interviewee`` — true when the guest interviews the owner, which
  switches the copy to first person.
* ``start_s`` / ``end_s`` — the usable window (pre-show chat trimmed).
* ``adjective``, ``date``, ``card_label``, ``card_when``, ``guest_headshot``,
  ``youtube_url``, ``website_slug`` — copy and cover inputs, all optional.
* ``links`` — ``[{"label", "url"}]`` for the website page's "Where to find"
  list (kept in ``Episode.extra``); a LinkedIn placeholder until filled.

When the ``sync`` stage has aligned a recorder session, ``<package>/sync.json``
names the aligned tracks and they replace ``tracks`` (``episode.json`` itself
is never rewritten).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config.loader import load_block

SPEAKERS = ("guest", "host")
SYNC_FILE = "sync.json"


def load_podcast_config() -> dict:
    """Return the ``podcast`` block of ``config.json``."""
    return load_block("podcast")


@dataclass
class Episode:
    folder: Path
    guest: str
    guest_display: str
    guest_first: str
    guest_pronoun_possessive: str
    tracks: dict[str, Path]
    host_is_interviewee: bool = False
    start_s: float = 0.0
    end_s: Optional[float] = None
    adjective: str = "inspiring"
    date: str = ""
    card_label: str = "PODCAST CONVERSATION"
    card_when: str = ""
    guest_headshot: Optional[Path] = None
    youtube_url: str = ""
    website_slug: str = ""
    host_name: str = "Roberto Ferraro"
    host_first: str = "Roberto"
    package_dirname: str = "podcast package"
    extra: dict = field(default_factory=dict)

    @property
    def package(self) -> Path:
        return self.folder / self.package_dirname

    @property
    def base_name(self) -> str:
        return f"{self.guest} - {self.host_name}"

    def label(self, speaker: str) -> str:
        """Transcript label for a speaker key."""
        return self.guest_first if speaker == "guest" else self.host_first

    def in_window(self, start: float, end: float) -> bool:
        return start >= self.start_s and (self.end_s is None or end <= self.end_s)


def load_episode(folder: Path, cfg: Optional[dict] = None) -> Episode:
    """Load ``<folder>/episode.json`` and resolve its paths against ``folder``."""
    cfg = cfg if cfg is not None else load_podcast_config()
    folder = Path(folder)
    spec_path = folder / "episode.json"
    if not spec_path.exists():
        raise FileNotFoundError(f"No episode.json in {folder}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    tracks = {k: folder / v for k, v in spec["tracks"].items()}
    synced = folder / cfg.get("package_dirname", "podcast package") / SYNC_FILE
    if synced.exists():
        tracks.update({k: folder / v for k, v in json.loads(synced.read_text(encoding="utf-8"))["tracks"].items()})
    missing = [k for k in SPEAKERS if k not in tracks]
    if missing:
        raise ValueError(f"episode.json tracks missing {missing}")
    host = cfg.get("host", {})
    headshot = spec.get("guest_headshot")
    known = {f.name for f in Episode.__dataclass_fields__.values()}
    return Episode(
        folder=folder,
        guest=spec["guest"],
        guest_display=spec.get("guest_display", spec["guest"]),
        guest_first=spec.get("guest_first", spec["guest"].split()[0]),
        guest_pronoun_possessive=spec.get("guest_pronoun_possessive", "their"),
        tracks=tracks,
        host_is_interviewee=bool(spec.get("host_is_interviewee", False)),
        start_s=float(spec.get("start_s", 0.0)),
        end_s=spec.get("end_s"),
        adjective=spec.get("adjective", "inspiring"),
        date=spec.get("date", ""),
        card_label=spec.get("card_label", "PODCAST CONVERSATION"),
        card_when=spec.get("card_when", ""),
        guest_headshot=(folder / headshot) if headshot else None,
        youtube_url=spec.get("youtube_url", ""),
        website_slug=spec.get("website_slug", ""),
        host_name=host.get("name", "Roberto Ferraro"),
        host_first=host.get("first", "Roberto"),
        package_dirname=cfg.get("package_dirname", "podcast package"),
        extra={k: v for k, v in spec.items() if k not in known and k != "tracks"},
    )


def work_dir(cfg: dict, episode: Episode) -> Path:
    """Scratch dir for large intermediates (WAVs, frames) — kept off OneDrive."""
    root = Path(cfg["work_dir"]) if cfg.get("work_dir") else Path(tempfile.gettempdir()) / "cm-podcast"
    path = root / episode.folder.name.replace(" ", "_")
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_episodes(cfg: Optional[dict] = None) -> list[Path]:
    """Episode folders under ``episodes_root`` that carry an ``episode.json``."""
    cfg = cfg if cfg is not None else load_podcast_config()
    root = Path(cfg["episodes_root"])
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if (p / "episode.json").exists())
