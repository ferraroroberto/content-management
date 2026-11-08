"""Single-source parser for the repo's ``YYYYMMDD`` / ``YYYY-MM-DD`` day strings.

CLI flags, launcher prompts and Notion titles all spell a day either way, so
every entry point funnels through here and shares one failure contract:
an unparseable value raises ``ValueError`` rather than guessing a day.
"""

import re
from datetime import date, datetime

DASHED_FORMAT = "%Y-%m-%d"
COMPACT_FORMAT = "%Y%m%d"

# strptime alone accepts "2026929" and "2026-9-29"; a run for a mis-read day writes
# real rows, so the digits must be zero-padded.
_DASHED = re.compile(r"\d{4}-\d{2}-\d{2}")
_COMPACT = re.compile(r"\d{8}")


def parse_day(value: str) -> date:
    """Parse zero-padded ``YYYYMMDD`` or ``YYYY-MM-DD`` into a date; raise ``ValueError`` otherwise."""
    text = value.strip()
    if _DASHED.fullmatch(text):
        return datetime.strptime(text, DASHED_FORMAT).date()
    if _COMPACT.fullmatch(text):
        return datetime.strptime(text, COMPACT_FORMAT).date()
    raise ValueError(f"Invalid date format: {value!r} (expected YYYYMMDD or YYYY-MM-DD)")
