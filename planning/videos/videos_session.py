"""Helpers for the cross-platform weekly-video orchestrator.

Unlike the other ``planning/<P>/*_session.py`` modules, this one does NOT own
a Playwright session. The video orchestrator drives four sister platforms
(LinkedIn, Instagram, Twitter, Threads) by opening one persistent-context
session per platform via the existing ``planning/<P>/<P>_session.py``
helpers — no new Chrome profile, no new bootstrap.

What lives here:

* ``load_videos_config()`` — reads the ``videos`` block from ``config.json``.
* ``load_notion_token()`` — reads the Notion API token (same pattern as
  every sister session).
* ``configure_logger()`` — project-wide logger setup with UTF-8 stdout.
* ``load_clip_payload(notion, editorial_row, video_cols, clip_cols)`` —
  resolves a clip relation on the editorial row, follows it into the clips
  DB, and returns a ``ClipPayload`` with everything four drivers need
  (short caption, LI long caption from the page body, video path, thumb
  path).
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning._session_base import configure_logger as _configure_logger  # noqa: E402
from planning._session_base import load_config_block, load_notion_token  # noqa: E402
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    get_page_body_text,
    retrieve_page,
)

logger = logging.getLogger("videos_session")

# Per-platform role suffixes on the editorial DB. The roles are wired in
# config under ``videos.editorial_columns`` as e.g. ``clip_rel_li`` and
# ``post_url_li``.
PLATFORMS = ("li", "ig", "tw", "th", "sb")

# A clip keeps uploading/finalizing server-side before the composer's
# Post/Schedule button enables. The per-platform image flows finalize within
# ~25 s; a video needs longer for X's chunked upload + server-side processing
# (INIT/APPEND/FINALIZE then STATUS polling to ``succeeded``) — empirically ~8 s
# for a platform-safe clip, but allow generous headroom. It is a cap, so healthy
# uploads return as soon as the button enables (issue #107).
VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS = 180000


@dataclass
class ClipPayload:
    """Everything the per-platform video drivers need for a single weekly clip.

    ``video_path`` and ``thumb_path`` are derived from the clip page's
    ``clipPC`` (folder, already terminated with a slash) and ``filePC``
    (bare filename without extension): video = ``<clipPC><filePC>.mp4``,
    thumb = ``<clipPC><filePC>.png``.

    ``caption_short`` is the clip page's ``Text`` property — used by IG,
    TW, TH, and SB.

    ``caption_long`` is the clip page's body text (concatenated rich-text
    blocks). Used by LinkedIn only. Strict: empty body is a hard error
    upstream so the LI driver always receives a non-empty string.
    """

    clip_page_id: str
    title: str
    video_path: Path
    thumb_path: Path
    caption_short: str
    caption_long: str


def configure_logger(name: str = "videos", debug: bool = False) -> logging.Logger:
    return _configure_logger(name, debug=debug)


def load_videos_config() -> dict:
    """Return the ``videos`` block from config.json."""
    return load_config_block("videos")


def first_clip_relation_id(editorial_row: dict, video_cols: dict) -> Optional[str]:
    """Return the first non-empty ``clip <P>(v)`` relation ID on the row.

    All five per-platform clip relations should point at the same clip page
    on a healthy editorial row; we follow whichever is populated first.
    """
    props = editorial_row.get("properties", {})
    for p in PLATFORMS:
        col = video_cols.get(f"clip_rel_{p}")
        if not col:
            continue
        rels = props.get(col, {}).get("relation", []) or []
        if rels:
            return rels[0].get("id")
    return None


def _clip_text_property(clip_page: dict, clip_cols: dict) -> str:
    """Extract the short caption ``Text`` property from the clip page.

    The clips DB's ``Text`` field can be either rich_text or a title-styled
    rich_text. Walk all possible shapes defensively.
    """
    col = clip_cols.get("caption_text", "Text")
    prop = clip_page.get("properties", {}).get(col, {})
    ptype = prop.get("type")
    if ptype == "rich_text":
        segs = prop.get("rich_text", []) or []
        return "".join(s.get("plain_text", "") for s in segs).strip()
    if ptype == "title":
        segs = prop.get("title", []) or []
        return "".join(s.get("plain_text", "") for s in segs).strip()
    return ""


def _clip_string_property(clip_page: dict, clip_cols: dict, role: str) -> str:
    """Read a single-string clip property (``clipPC`` / ``filePC``).

    These properties can be stored as rich_text in the clips DB, but the
    schema sometimes exposes them as formula(string) when computed. Tolerate
    both. Returns '' if the property is missing/empty.
    """
    col = clip_cols[role]
    prop = clip_page.get("properties", {}).get(col, {})
    ptype = prop.get("type")
    if ptype == "rich_text":
        segs = prop.get("rich_text", []) or []
        return "".join(s.get("plain_text", "") for s in segs).strip()
    if ptype == "title":
        segs = prop.get("title", []) or []
        return "".join(s.get("plain_text", "") for s in segs).strip()
    if ptype == "formula":
        formula = prop.get("formula", {})
        if formula.get("type") == "string":
            return str(formula.get("string") or "").strip()
    if ptype == "url":
        return str(prop.get("url") or "").strip()
    # Fallback to the generic extractor.
    val = get_field({"properties": {col: prop}}, role, clip_cols) or ""
    return str(val).strip()


def _clip_page_title(clip_page: dict) -> str:
    for prop in (clip_page.get("properties") or {}).values():
        if prop.get("type") == "title":
            segs = prop.get("title", []) or []
            return "".join(s.get("plain_text", "") for s in segs).strip()
    return ""


# Win32 file-attribute bits that mark an un-hydrated OneDrive "Files On-Demand"
# placeholder. For such a file ``Path.exists()`` is True AND ``stat().st_size``
# reports the full logical size, so neither can tell a real local file apart
# from a cloud-only stub — only these attributes can.
_FILE_ATTRIBUTE_OFFLINE = 0x00001000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_PLACEHOLDER_MASK = (
    _FILE_ATTRIBUTE_OFFLINE
    | _FILE_ATTRIBUTE_RECALL_ON_OPEN
    | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _win_file_attributes(path: Path) -> int:
    """Return the Win32 attribute bitmask for ``path`` (-1 if unavailable).

    Returns -1 on non-Windows platforms or when the query fails, so callers
    treat the file as a normal local file (no-op) rather than a placeholder.
    """
    if os.name != "nt":
        return -1
    get_attrs = ctypes.windll.kernel32.GetFileAttributesW
    get_attrs.restype = ctypes.c_uint32
    get_attrs.argtypes = [ctypes.c_wchar_p]
    attrs = get_attrs(str(path))
    return -1 if attrs == _INVALID_FILE_ATTRIBUTES else int(attrs)


def is_online_only(path: Path) -> bool:
    """True iff ``path`` is an un-hydrated OneDrive Files-On-Demand placeholder."""
    attrs = _win_file_attributes(path)
    return attrs >= 0 and bool(attrs & _PLACEHOLDER_MASK)


def _trigger_download(path: Path) -> None:
    """Ask OneDrive to materialise a placeholder on disk.

    Two complementary steps:

    1. ``attrib +P -U`` pins the file ("always keep on this device", clear the
       online-only flag) so OneDrive keeps it local and begins fetching. This is
       best-effort — it only marks intent; the OneDrive service does the work.
    2. A full streamed read forces a *synchronous* recall when the OneDrive
       provider is running, so the bytes are on disk by the time we return.

    If the read fails with the cloud-provider error (surfaced on Windows as
    ``[Errno 22] Invalid argument`` / "The cloud file provider is not running"),
    raise immediately with an actionable message rather than letting the caller
    poll for the full timeout — the file can never hydrate while OneDrive is down.

    Note: pinning leaves the file kept-locally — appropriate for a clip that's
    actively being published; the user can free up space again afterwards.
    """
    if os.name == "nt":
        try:
            subprocess.run(
                ["attrib", "+P", "-U", str(path)],
                check=True, capture_output=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as err:
            logger.debug("attrib pin failed (%s) — relying on read-through.", err)
    try:
        with open(path, "rb") as fh:
            while fh.read(8 * 1024 * 1024):
                pass
    except OSError as err:
        raise RuntimeError(
            f"Could not hydrate OneDrive placeholder {path}: {err}. This usually "
            "means the OneDrive cloud provider is not running — start OneDrive "
            "(and ensure it is online), then retry."
        ) from err


def ensure_local_file(path: Path, *, timeout_s: float = 600.0, poll_s: float = 1.0) -> None:
    """Force a OneDrive online-only placeholder to download before it is used.

    Handing a cloud-only placeholder to Playwright's ``set_input_files`` makes
    the platform read bytes that aren't on disk yet: the on-demand recall can
    stall past the uploader's timeout, or fail outright when OneDrive is paused
    / offline, so the post gets scheduled with no real media even though the run
    reports success (issue #104).

    This triggers OneDrive's download, then polls the placeholder attributes
    until they clear. Raises ``RuntimeError`` if the file is still a placeholder
    after ``timeout_s`` so the caller fails loudly instead of uploading a stub.
    No-op for already-local files and on non-Windows platforms.
    """
    if not is_online_only(path):
        return
    size_mb = path.stat().st_size / 1_048_576
    logger.info(
        "🌥️ %s is an online-only OneDrive placeholder (%.1f MB) — forcing download…",
        path.name, size_mb,
    )
    _trigger_download(path)
    # The download runs in the OneDrive service; poll until the attributes clear.
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not is_online_only(path):
            logger.info("✅ %s hydrated locally (%.1f MB).", path.name, size_mb)
            return
        time.sleep(poll_s)
    raise RuntimeError(
        f"OneDrive placeholder {path} did not finish downloading within {timeout_s:.0f}s. "
        "Pin it locally (right-click → Always keep on this device) and retry."
    )


# ---------------------------------------------------------------------------
# Platform-safe transcode (issue #107)
#
# Some weekly masters are exported very heavy (~30 Mbps / 200+ MB) and carry a
# mov_text subtitle track. X (Twitter) silently refuses such clips — its
# Schedule button stays aria-disabled indefinitely — while LI/IG/TH/Substack
# tolerate them. The durable fix is to transcode a platform-safe derivative
# (H.264 high, bitrate-capped, AAC, no subtitle/data tracks, faststart) and
# upload THAT everywhere, leaving the OneDrive master untouched. Degrades
# gracefully to the master when ffmpeg/ffprobe are missing or transcode fails.
# ---------------------------------------------------------------------------

# Defaults applied when the ``videos.transcode`` block (or a key) is absent.
_TRANSCODE_DEFAULTS = {
    "enabled": True,
    "max_size_mb": 256,
    "max_bitrate_mbps": 20,
    "target_bitrate_mbps": 10,
    "cache_dir": None,
}


def _resolve_transcode_cfg() -> dict:
    """Merge the ``videos.transcode`` config over the baked-in defaults."""
    try:
        block = load_videos_config().get("transcode") or {}
    except RuntimeError:
        block = {}
    return {**_TRANSCODE_DEFAULTS, **block}


def _probe_video(path: Path) -> Optional[dict]:
    """Return ``{size, bit_rate, has_non_av_stream}`` via ffprobe, or None on failure.

    ``size`` (bytes) and ``bit_rate`` (bits/s) come from ffprobe's format block.
    ``has_non_av_stream`` is True when any stream's ``codec_type`` is not
    video/audio (e.g. a ``mov_text`` subtitle or a data track) — X dislikes those.
    None means we could not probe (ffprobe missing or errored); callers then skip
    transcoding and log a warning rather than guess.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_format", "-show_streams",
             "-of", "json", str(path)],
            check=True, capture_output=True, timeout=120,
        )
        data = json.loads(proc.stdout or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError) as err:
        logger.warning("⚠️ ffprobe failed on %s: %s", path.name, err)
        return None

    def _to_int(v) -> int:
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    return {
        "size": _to_int(fmt.get("size")),
        "bit_rate": _to_int(fmt.get("bit_rate")),
        "has_non_av_stream": any(
            s.get("codec_type") not in ("video", "audio") for s in streams
        ),
    }


def _needs_transcode(probe: dict, tcfg: dict) -> tuple[bool, str]:
    """Decide whether a probed master trips a platform-safety threshold."""
    size_mb = probe["size"] / 1_048_576
    bitrate_mbps = probe["bit_rate"] / 1_000_000
    reasons: list[str] = []
    if probe["has_non_av_stream"]:
        reasons.append("non-AV stream (subtitle/data) present")
    if tcfg["max_size_mb"] and size_mb > tcfg["max_size_mb"]:
        reasons.append(f"size {size_mb:.0f} MB > {tcfg['max_size_mb']} MB")
    if tcfg["max_bitrate_mbps"] and bitrate_mbps > tcfg["max_bitrate_mbps"]:
        reasons.append(
            f"bitrate {bitrate_mbps:.1f} Mbps > {tcfg['max_bitrate_mbps']} Mbps"
        )
    return bool(reasons), "; ".join(reasons)


def _transcode_cache_path(src: Path, tcfg: dict) -> Path:
    """Cache path keyed by source mtime+size so a re-exported master invalidates it."""
    root = tcfg.get("cache_dir")
    cache_dir = Path(root) if root else Path(tempfile.gettempdir()) / "cm-video-transcode"
    st = src.stat()
    return cache_dir / f"clip_{src.stem}_{st.st_mtime_ns}_{st.st_size}.mp4"


def _run_ffmpeg_transcode(src: Path, dst: Path, tcfg: dict) -> bool:
    """Transcode ``src`` → ``dst`` to platform-safe specs. Returns True on success.

    Drops subtitle (``-sn``) / data (``-dn``) tracks and maps a single video +
    optional audio stream, re-encodes H.264 high with a hard bitrate cap and AAC
    audio, and writes ``+faststart`` for streaming. On any failure the partial
    output is removed and False is returned so the caller falls back to the master.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    target = tcfg["target_bitrate_mbps"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-b:v", f"{target}M", "-maxrate", f"{target}M", "-bufsize", f"{target * 2}M",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", "-sn", "-dn",
        str(dst),
    ]
    logger.info("🎞️ Transcoding %s → platform-safe (%d Mbps cap)…", src.name, target)
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as err:
        stderr = getattr(err, "stderr", b"") or b""
        logger.error(
            "❌ ffmpeg transcode failed for %s: %s — falling back to master.",
            src.name, stderr.decode("utf-8", "replace")[-500:] or err,
        )
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def ensure_platform_safe_clip(video_path: Path, tcfg: Optional[dict] = None) -> Path:
    """Return a platform-safe derivative of ``video_path``, or the master unchanged.

    Transcode triggers on a size/bitrate threshold or a non-audio/video stream
    (issue #107 — X rejects heavy/subtitled clips). The derivative is cached in
    the system temp dir keyed by source mtime+size and reused across re-runs.
    Degrades to the master (logged) when transcode is disabled, ffmpeg/ffprobe
    are unavailable, the clip is already safe, or ffmpeg fails — never raises,
    since 4/5 platforms tolerate the heavy master.
    """
    tcfg = tcfg or _resolve_transcode_cfg()
    if not tcfg.get("enabled", True):
        return video_path
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        logger.warning(
            "⚠️ ffmpeg/ffprobe not found on PATH — skipping platform-safe transcode; "
            "uploading the master %s as-is (X may reject a heavy/subtitled clip).",
            video_path.name,
        )
        return video_path

    probe = _probe_video(video_path)
    if probe is None:
        return video_path  # already warned
    needed, reason = _needs_transcode(probe, tcfg)
    if not needed:
        logger.info("✅ %s already platform-safe — no transcode.", video_path.name)
        return video_path

    cache = _transcode_cache_path(video_path, tcfg)
    if cache.exists() and cache.stat().st_size > 0:
        logger.info("♻️ Reusing cached platform-safe clip: %s", cache)
        return cache

    logger.info("🎬 %s needs transcode: %s", video_path.name, reason)
    if _run_ffmpeg_transcode(video_path, cache, tcfg):
        before_mb = probe["size"] / 1_048_576
        after_mb = cache.stat().st_size / 1_048_576
        logger.info(
            "✅ Transcoded %s: %.0f MB → %.0f MB (%s).",
            video_path.name, before_mb, after_mb, cache.name,
        )
        return cache
    return video_path


def load_clip_payload(notion, editorial_row: dict, video_cols: dict, clip_cols: dict) -> ClipPayload:
    """Resolve the shared clip relation off the editorial row and build a payload.

    Raises ``RuntimeError`` if no clip relation is set, if the resolved
    clip page is missing ``clipPC`` / ``filePC``, if the assembled .mp4
    file does not exist on disk, or if it is an online-only OneDrive
    placeholder that cannot be hydrated within the budget (issue #104 —
    every platform driver consumes this one ``video_path``, so hydration
    happens here). The LI long caption (page body) is read here; the
    orchestrator will fail the LI status if it's empty (strict per user
    spec — no fallback to the short ``Text`` caption).
    """
    rel_id = first_clip_relation_id(editorial_row, video_cols)
    if not rel_id:
        raise RuntimeError("No ``clip <P>(v)`` relation populated on editorial row.")

    clip_page = retrieve_page(notion, rel_id)
    title = _clip_page_title(clip_page)

    folder = _clip_string_property(clip_page, clip_cols, "clip_pc")
    fname = _clip_string_property(clip_page, clip_cols, "file_pc")
    if not folder:
        raise RuntimeError(f"Clip page {title!r} has empty {clip_cols['clip_pc']}.")
    if not fname:
        raise RuntimeError(f"Clip page {title!r} has empty {clip_cols['file_pc']}.")

    # ``clipPC`` already has a trailing slash per the screenshot. Use plain
    # string concatenation rather than Path() (which normalizes separators
    # in ways that lose the trailing slash semantics) and only fall back to
    # joining if the trailing separator is missing.
    sep = "\\" if "\\" in folder else "/"
    if not folder.endswith(("\\", "/")):
        folder = folder + sep
    video_str = f"{folder}{fname}.mp4"
    thumb_str = f"{folder}{fname}.png"
    video_path = Path(video_str)
    thumb_path = Path(thumb_str)

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    # Materialise the clip locally before any driver feeds it to set_input_files —
    # an online-only OneDrive placeholder passes .exists() but uploads as no/partial
    # media (issue #104). Raises loudly if it can't hydrate within the budget.
    ensure_local_file(video_path)
    # Transcode a platform-safe derivative if the master is too heavy / carries
    # subtitle or data tracks — X rejects such clips (issue #107). All five
    # consumers feed off this one ``video_path``, so doing it here covers every
    # platform uniformly; the OneDrive master is never modified.
    video_path = ensure_platform_safe_clip(video_path)
    if not thumb_path.exists():
        # Thumb is optional — some platforms auto-generate one. Log but don't fail.
        logger.warning("⚠️ Thumb not found (continuing without): %s", thumb_path)
    else:
        ensure_local_file(thumb_path)

    caption_short = _clip_text_property(clip_page, clip_cols)

    # The LinkedIn long caption is stored on the clip page in two places:
    #   (a) the page body — historically a single ``code`` block (language=
    #       "plain text") to preserve whitespace and emoji exactly.
    #   (b) the ``TextLI`` property on the clips DB — a plain rich_text
    #       cache the user wants populated so callers (and the user) can read
    #       the LI caption without expanding the page.
    # Strategy: prefer the cached ``TextLI`` if non-empty. Otherwise read the
    # body via API, and if the body has content, write it back into
    # ``TextLI`` so the next read is cheap and consistent.
    caption_li_col = clip_cols.get("caption_li")
    caption_li_cached = ""
    if caption_li_col:
        caption_li_cached = _clip_string_property(clip_page, clip_cols, "caption_li")

    body_text = get_page_body_text(notion, rel_id).strip()
    if caption_li_cached:
        caption_long = caption_li_cached
    else:
        caption_long = body_text
        if body_text and caption_li_col:
            try:
                from reporting.notion.editorial import set_field as _set
                _set(notion, rel_id, "caption_li", body_text, clip_cols, "rich_text")
                logger.info(
                    "🔁 Cached LinkedIn long caption (%d chars) into clip property %r.",
                    len(body_text), caption_li_col,
                )
            except Exception as err:
                logger.warning(
                    "⚠️ Could not cache LI caption into %s: %s", caption_li_col, err,
                )

    logger.info(
        "🎬 Clip %r resolved: video=%s caption_short=%d chars caption_long=%d chars",
        title, video_path.name, len(caption_short), len(caption_long),
    )
    return ClipPayload(
        clip_page_id=rel_id,
        title=title,
        video_path=video_path,
        thumb_path=thumb_path,
        caption_short=caption_short,
        caption_long=caption_long,
    )


__all__ = [
    "PLATFORMS",
    "VIDEO_UPLOAD_FINALIZE_TIMEOUT_MS",
    "ClipPayload",
    "configure_logger",
    "ensure_local_file",
    "ensure_platform_safe_clip",
    "first_clip_relation_id",
    "is_online_only",
    "load_clip_payload",
    "load_notion_token",
    "load_videos_config",
]
