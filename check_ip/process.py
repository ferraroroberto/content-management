"""Pure result-processing helpers for the illustration copyright check.

Ported from the sibling repo's ``linkedin/check_ip/data_processor.py`` — the
date-extraction and platform-identification logic is the reusable core of that
pipeline and is carried over unchanged in behaviour. What did *not* come across
is the pandas plumbing: duplicate marking and the per-image tallies are SQL
now (``check_ip.db.mark_duplicates`` / ``refresh_image_counts``).

Everything here is a pure function over strings, so it tests without a store.
"""

from __future__ import annotations

import datetime
import logging
import re
from datetime import timezone
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger("check_ip.process")

# Platform patterns, in the original's order — the first match wins.
PLATFORM_PATTERNS = (
    ("LinkedIn", r"linkedin\.com/posts/|linkedin\.com/pulse/|linkedin\.com/.*/activity-\d+"),
    ("Facebook", r"facebook\.com/.*/photos/\d+"),
    ("Pinterest", r"pinterest\.com/.*/pin/\d+"),
    ("Instagram", r"instagram\.com/.*/p/[a-zA-Z0-9]+"),
    ("Twitter/X", r"(?:twitter|x)\.com/.*/status/\d+"),
)

# Both platforms pack a millisecond timestamp into the high bits of the post id.
# Twitter's is offset by its own epoch; LinkedIn's is a plain shift.
_TWITTER_EPOCH_MS = 1288834974657
_SNOWFLAKE_SHIFT = 22

MATCH_TYPES = {"exact_matches": "Exact Match", "visual_matches": "Similar Match"}


def extract_post_date(url: str) -> Optional[str]:
    """Recover a post's publish time from a Twitter/X or LinkedIn URL.

    Returns a formatted UTC string, or ``None`` for any other platform (and for
    a malformed id) — the caller stores that as an unknown date rather than
    guessing one.
    """
    twitter_match = re.search(r"(?:twitter|x)\.com/.*/status/(\d+)", url or "")
    if twitter_match:
        try:
            tweet_id = int(twitter_match.group(1))
            stamp_ms = (tweet_id >> _SNOWFLAKE_SHIFT) + _TWITTER_EPOCH_MS
            return datetime.datetime.fromtimestamp(stamp_ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        except (ValueError, OSError, OverflowError) as err:
            logger.debug("⚠️ could not read a Twitter date from %s: %s", url, err)
            return None

    linkedin_match = re.search(r"activity[:-](\d+)", url or "")
    if linkedin_match:
        try:
            post_id = int(linkedin_match.group(1))
            stamp_ms = post_id >> _SNOWFLAKE_SHIFT
            return datetime.datetime.fromtimestamp(stamp_ms / 1000, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            )
        except (ValueError, OSError, OverflowError) as err:
            logger.debug("⚠️ could not read a LinkedIn date from %s: %s", url, err)
            return None

    return None


def identify_source(url: str) -> Optional[str]:
    """Name the social platform a URL belongs to, or ``None`` for the open web."""
    for platform, pattern in PLATFORM_PATTERNS:
        if re.search(pattern, url or "", re.IGNORECASE):
            return platform
    return None


def is_recently_processed(
    last_processed_date: Optional[str],
    linkedin_count: int,
    linkedin_high_threshold: int,
    high_linkedin_days: int,
    standard_days: int,
) -> bool:
    """Whether an image is still inside its re-search window.

    Images that already show up on LinkedIn a lot are re-searched far more
    often than the rest — that is where new reuse actually appears. An
    unparseable date means "never processed", so the image is searched again.
    """
    if not last_processed_date:
        return False
    threshold_days = (
        high_linkedin_days if (linkedin_count or 0) >= linkedin_high_threshold else standard_days
    )
    try:
        last = datetime.datetime.strptime(str(last_processed_date), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return False
    return (datetime.datetime.now() - last).days < threshold_days


def collect_image_paths(images_folder: Path, allowed_extensions: Iterable[str]) -> list[Path]:
    """Every image in the folder with an allowed extension, in stable order."""
    allowed = {e.lower() for e in allowed_extensions}
    return sorted(p for p in Path(images_folder).glob("*.*") if p.suffix.lower() in allowed)


def build_rows(
    search_data: dict,
    search_type: str,
    img_filename: str,
    img_url: str,
    search_date: str,
    existing: set[tuple[str, str]],
) -> tuple[list[dict], int, int]:
    """Turn one Google Lens response into result rows.

    ``existing`` holds the (found_link, match_type) pairs already stored for
    this image — the same URL legitimately arrives from both the exact-match
    and the visual-match search, and those are two distinct findings, so the
    match type is part of the identity.

    Returns (rows, added, skipped).
    """
    match_type = MATCH_TYPES.get(search_type)
    if match_type is None:
        return [], 0, 0

    rows: list[dict] = []
    skipped = 0
    for index, res in enumerate(search_data.get(search_type, []) or [], start=1):
        link = res.get("link") or ""
        if not link:
            continue
        if (link, match_type) in existing:
            skipped += 1
            continue
        existing.add((link, match_type))
        rows.append(
            {
                "local_image": img_filename,
                "uploaded_url": img_url,
                "found_link": link,
                "title": res.get("title", ""),
                "duplicate": 0,
                "match_type": match_type,
                "source": identify_source(link),
                "post_date": extract_post_date(link),
                "search_date": search_date,
                "order": index,
            }
        )
    return rows, len(rows), skipped
