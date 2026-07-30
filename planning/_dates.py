"""Shared date helpers for the per-platform planning schedulers.

``next_monday`` / ``parse_week_start`` / ``parse_single_date`` /
``date_to_day_title`` were byte-identical (or near-identical) copies in each
of the five scheduler modules (twitter, threads, linkedin, videos,
instagram's ``clone_to_other_platforms``). They are pure, stdlib-only, and
Playwright-free, so there is no reason for the "copied to avoid cross-package
import" workaround one of the callers used — this module has no dependency
on anything Playwright-related and is safe for every scheduler to import.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional


def next_monday(today: Optional[date] = None) -> date:
    """Return the next Monday strictly after ``today`` (or 7 days out if today IS Monday)."""
    today = today or date.today()
    days_ahead = (7 - today.weekday()) % 7  # Mon=0
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def parse_week_start(s: Optional[str]) -> date:
    if not s:
        return next_monday()
    return datetime.strptime(s, "%Y-%m-%d").date()


def parse_single_date(s: str) -> date:
    """Accept YYYYMMDD or YYYY-MM-DD."""
    s = s.strip()
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").date()
    return datetime.strptime(s, "%Y%m%d").date()


def date_to_day_title(d: date) -> str:
    return d.strftime("%Y%m%d")
