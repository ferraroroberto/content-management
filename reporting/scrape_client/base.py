"""Shared helpers for the Playwright-based scrape client.

Every per-platform scraper module in this package reuses the existing
``planning/<platform>/<platform>_session.py`` session classes so the
real-Chrome / stealth-launch contract from ``config/chrome_launch.py`` stays
the single source of truth.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "config.json"


class ScrapeError(RuntimeError):
    """Raised when a required field cannot be extracted from the page."""


def load_full_config() -> dict:
    """Load the full config.json."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        return json.load(fp)


def load_platform_block(platform: str) -> dict:
    """Return the platform's own config block (e.g. ``config['linkedin']``)."""
    cfg = load_full_config()
    block = cfg.get(platform)
    if not block:
        raise ScrapeError(
            f"Missing '{platform}' config block in config.json — needed for "
            "user_data_dir + handle/profile URL."
        )
    return block


_INT_RE = re.compile(r"-?\d[\d,.  \s]*")


def parse_int(text: Optional[str]) -> Optional[int]:
    """Parse the first integer found in ``text``, stripping thousands separators.

    Handles ``"111,518"``, ``"31 075"`` (NBSP), ``"31.075"`` (dot-grouped),
    and ``"17527 Followers"``. Returns ``None`` if nothing parseable was found.
    """
    if text is None:
        return None
    match = _INT_RE.search(text)
    if not match:
        return None
    raw = match.group(0)
    raw = re.sub(r"[,.  \s]", "", raw)
    try:
        return int(raw)
    except ValueError:
        return None


_K_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([KkMm])\s*$")


def parse_short_int(text: Optional[str]) -> Optional[int]:
    """Parse abbreviations like ``"17.5K"``, ``"132K"``, ``"1.9M"``.

    Used only as a fallback when the exact value isn't reachable.
    """
    if text is None:
        return None
    m = _K_RE.match(text)
    if not m:
        return parse_int(text)
    base = float(m.group(1).replace(",", "."))
    mult = 1_000_000 if m.group(2).lower() == "m" else 1_000
    return int(round(base * mult))


def to_iso_date(value) -> Optional[str]:
    """Convert various date representations to ``YYYY-MM-DD``.

    Accepts:
    * ``int`` / numeric string Unix epoch seconds (or epoch millis).
    * ISO-8601 strings (with or without timezone).
    * ``None`` → ``None``.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:  # millis
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Numeric string?
        try:
            ts = float(s)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
        if m:
            return m.group(1)
    return None


_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
        start=1,
    )
}
_HUMAN_DATE_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s+(\d{4})")


def human_date_to_iso_date(text: Optional[str]) -> Optional[str]:
    """Parse a human-readable date to ``YYYY-MM-DD``.

    Handles strings like ``"May 30, 2026, 6:03 AM"`` or ``"September 5, 2025"``
    (e.g. the ``title`` attribute Substack now puts on a note's timestamp
    anchor, having dropped the old ``<time datetime>`` element). Returns
    ``None`` if no ``Month DD, YYYY`` fragment is present.
    """
    if not text:
        return None
    m = _HUMAN_DATE_RE.search(text)
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].lower())
    if not month:
        return None
    try:
        return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(2)):02d}"
    except ValueError:
        return None


def linkedin_activity_to_iso_date(activity_id) -> Optional[str]:
    """Decode a LinkedIn activity/URN numeric id to ``YYYY-MM-DD``.

    LinkedIn activity ids encode ``ms_since_unix_epoch`` in the high bits via
    ``id >> 22``. (Same scheme as Twitter but on the Unix epoch instead of
    Twitter's 2010-11-04 epoch.)
    """
    try:
        aid = int(activity_id)
    except (TypeError, ValueError):
        return None
    if aid <= 0:
        return None
    ts_ms = aid >> 22
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def snowflake_to_iso_date(snowflake_id: str) -> Optional[str]:
    """Decode a Twitter/X snowflake status ID to a ``YYYY-MM-DD`` date.

    Twitter snowflakes encode ``(ms_since_2010-11-04T01:42:54.657Z) << 22``.
    The constant ``1288834974657`` is Twitter's epoch in ms.
    """
    try:
        sid = int(snowflake_id)
    except (TypeError, ValueError):
        return None
    if sid <= 0:
        return None
    ts_ms = (sid >> 22) + 1288834974657
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def normalize_target_date(date_str: Optional[str]) -> str:
    """Accept ``YYYY-MM-DD`` / ``YYYYMMDD`` / ``None`` → return ``YYYY-MM-DD``."""
    if not date_str:
        return datetime.now().strftime("%Y-%m-%d")
    s = date_str.strip()
    if "-" in s:
        # Validate
        return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
    return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")


def relative_time_to_iso_date(text: str, *, now: Optional[datetime] = None) -> Optional[str]:
    """Convert relative time text like ``"7h"``, ``"3d"``, ``"1d ago"``, ``"6d"`` to a date.

    Falls back to ``None`` if the text isn't a recognised relative form. Use only
    when the platform exposes no machine-readable timestamp.
    """
    if not text:
        return None
    s = text.strip().lower()
    now = now or datetime.now()
    m = re.match(r"^(\d+)\s*([smhdw])\b", s)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    delta = {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
        "w": timedelta(weeks=n),
    }[unit]
    return (now - delta).strftime("%Y-%m-%d")
