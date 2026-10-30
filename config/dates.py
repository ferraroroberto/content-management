"""Single-source parser for the repo's ``YYYYMMDD`` / ``YYYY-MM-DD`` day strings.

CLI flags, launcher prompts and Notion titles all spell a day either way, so
every entry point funnels through here and shares one failure contract:
an unparseable value raises ``ValueError`` rather than guessing a day.
"""

from datetime import date, datetime

DASHED_FORMAT = "%Y-%m-%d"
COMPACT_FORMAT = "%Y%m%d"


def parse_day(value: str) -> date:
    """Parse ``YYYYMMDD`` or ``YYYY-MM-DD`` into a date; raise ``ValueError`` otherwise."""
    text = value.strip()
    fmt = DASHED_FORMAT if "-" in text else COMPACT_FORMAT
    return datetime.strptime(text, fmt).date()
