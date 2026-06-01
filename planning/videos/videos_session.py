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

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).parent.parent.parent))
from config.logger_config import setup_logger  # noqa: E402
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    get_page_body_text,
    retrieve_page,
)

logger = logging.getLogger("videos_session")

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "config.json"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Per-platform role suffixes on the editorial DB. The roles are wired in
# config under ``videos.editorial_columns`` as e.g. ``clip_rel_li`` and
# ``post_url_li``.
PLATFORMS = ("li", "ig", "tw", "th", "sb")


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
    level = logging.DEBUG if debug else logging.INFO
    return setup_logger(name, level=level, file_logging=True)


def load_videos_config() -> dict:
    """Return the ``videos`` block from config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    block = cfg.get("videos")
    if not block:
        raise RuntimeError("Missing 'videos' block in config.json")
    return block


def load_notion_token() -> str:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        cfg = json.load(fp)
    token = cfg.get("notion", {}).get("api_token")
    if not token:
        raise RuntimeError("Missing 'notion.api_token' in config.json")
    return token


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


def load_clip_payload(notion, editorial_row: dict, video_cols: dict, clip_cols: dict) -> ClipPayload:
    """Resolve the shared clip relation off the editorial row and build a payload.

    Raises ``RuntimeError`` if no clip relation is set, if the resolved
    clip page is missing ``clipPC`` / ``filePC``, or if the assembled .mp4
    file does not exist on disk. The LI long caption (page body) is read
    here; the orchestrator will fail the LI status if it's empty (strict
    per user spec — no fallback to the short ``Text`` caption).
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
    if not thumb_path.exists():
        # Thumb is optional — some platforms auto-generate one. Log but don't fail.
        logger.warning("⚠️ Thumb not found (continuing without): %s", thumb_path)

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
    "ClipPayload",
    "configure_logger",
    "first_clip_relation_id",
    "load_clip_payload",
    "load_notion_token",
    "load_videos_config",
]
