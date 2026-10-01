"""Chunk store for the recorder: one file per uploaded chunk, joined and
remuxed into the episode folder once the page says the recording is done.

The page uploads each ``MediaRecorder`` chunk as it arrives, with its sequence
number, so the store must be idempotent (a retry after a dropped connection
re-sends a chunk the server may already have) and must say which numbers it
holds (the page reconciles against that after a reconnect). Chunks are staged
off OneDrive (``recorder.staging_dir``, default the system temp folder) and
only the finished, remuxed file lands in ``<episode>/video editing/``.

Neither container ``MediaRecorder`` writes carries a usable duration (#336), so
the joined stream is remuxed with ``ffmpeg -c copy`` into an ``.mp4``, which
rewrites the index without re-encoding. The page records WebM (H.264 + Opus)
where it can, because Chrome's MP4 recorder crashes the tab at 4 GiB (#341);
MP4 holds H.264, VP9 and Opus alike, so the output is one format either way.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("recorder.store")

RECORDING_ID = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
EXTENSIONS = ("mp4", "webm")
MAX_SEQ = 1_000_000
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class MissingChunks(Exception):
    """The page asked to finish a recording the store does not fully hold."""

    def __init__(self, missing: list[int]):
        super().__init__(f"{len(missing)} chunk(s) missing, first {missing[:10]}")
        self.missing = missing


class ChunkStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, side: str, rid: str) -> Path:
        if not RECORDING_ID.match(rid):
            raise ValueError(f"bad recording id {rid!r}")
        return self.root / side / rid

    def _meta(self, folder: Path) -> dict:
        path = folder / "meta.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def _save_meta(self, folder: Path, meta: dict) -> None:
        tmp = folder / "meta.json.tmp"
        tmp.write_text(json.dumps(meta), encoding="utf-8")
        os.replace(tmp, folder / "meta.json")

    def put(self, side: str, rid: str, seq: int, data: bytes) -> bool:
        """Store chunk ``seq``; returns False when an identical copy was already there."""
        if not 0 <= seq < MAX_SEQ:
            raise ValueError(f"bad chunk number {seq}")
        folder = self._dir(side, rid)
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / "meta.json").exists():
            self._save_meta(folder, {"started": time.time()})
        path = folder / f"{seq:07d}.part"
        if path.exists() and path.stat().st_size == len(data):
            return False
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)  # a reader never sees half a chunk
        return True

    def have(self, side: str, rid: str) -> list[int]:
        folder = self._dir(side, rid)
        return sorted(int(p.stem) for p in folder.glob("*.part")) if folder.is_dir() else []

    def finished(self, side: str, rid: str) -> Optional[dict]:
        folder = self._dir(side, rid)
        return self._meta(folder).get("result") if folder.is_dir() else None

    def finish(self, side: str, rid: str, total: int, ext: str, dst_dir: Path, *, label: str,
               sidecar: Optional[dict] = None) -> dict:
        """Join chunks ``0..total-1`` in order, remux into ``dst_dir`` and
        return ``{"file", "duration_s", "bytes"}``. Idempotent: a second call
        returns the first result. ``sidecar`` (what the sync stage needs to pair
        a reference with its main recording) is written beside the file as
        ``<file>.json``."""
        folder = self._dir(side, rid)
        meta = self._meta(folder)
        if meta.get("result"):
            return meta["result"]
        if ext not in EXTENSIONS:
            raise ValueError(f"bad extension {ext!r}")
        have = set(self.have(side, rid))
        missing = [n for n in range(total) if n not in have]
        if total <= 0 or missing:
            raise MissingChunks(missing or [0])
        joined = folder / f"joined.{ext}"
        with joined.open("wb") as out:
            for n in range(total):
                with (folder / f"{n:07d}.part").open("rb") as part:
                    shutil.copyfileobj(part, out)
        started = time.strftime("%Y-%m-%d %H%M%S", time.localtime(meta.get("started", time.time())))
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"recorder - {label} - {started}.mp4"
        remux(joined, dst)
        result = {"file": dst.name, "duration_s": probe_duration(dst), "bytes": dst.stat().st_size}
        dst.with_suffix(".json").write_text(json.dumps({**(sidecar or {}), "side": side, "rid": rid, "file": dst.name,
                                                        "duration_s": result["duration_s"]}, indent=1),
                                            encoding="utf-8")
        meta["result"] = result
        self._save_meta(folder, meta)
        for path in folder.glob("*.part"):
            path.unlink()
        joined.unlink()
        logger.info("✅ %s recording %s: %s, %.1f s, %.0f MB", label, rid[:8], dst.name, result["duration_s"],
                    result["bytes"] / 1e6)
        return result


def remux(src: Path, dst: Path) -> None:
    """Rewrite the container with a proper index (no re-encode)."""
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-fflags", "+genpts", "-i", str(src),
                    "-c", "copy", "-map", "0", "-movflags", "+faststart", str(dst)],
                   check=True, capture_output=True, creationflags=NO_WINDOW)


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], check=True, capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout.strip()
    return round(float(out), 2) if out and out != "N/A" else 0.0
