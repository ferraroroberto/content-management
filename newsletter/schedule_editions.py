"""Top up the buffer of future newsletter editions in Notion (issue #230).

``newsletter/pipeline.py``'s archive step targets the first *future* edition
row that still has room for the article's topic (``notion_io.pick_newsletter``,
which filters ``Date >= today``). When the buffer of future rows runs dry the
archive step stops outright::

    ❌ No future newsletter has room for topic '<topic>' — stopping

Creating those rows is pure arithmetic on the existing table — the next number
continues the sequence, the next date is one cadence step on from the latest —
so this module does it. It reads the whole newsletter DB once, derives the
buffer state, and creates however many rows are missing.

The sequence generator (:func:`plan_editions`) is deliberately pure: no Notion,
no config, no clock. It is what ``tests/test_schedule_editions.py`` pins.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from config.loader import load_full_config
from config.logger_config import setup_logger
from newsletter import notion_io

logger = logging.getLogger("newsletter_archive.schedule_editions")

#: How many future editions the buffer aims to hold (~two months of runway).
DEFAULT_TARGET = 8

#: Weeks are the newsletter's cadence; every existing row is a Saturday.
CADENCE_DAYS = 7

_NUMBER_RE = re.compile(r"^N?(\d+)$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# pure helpers


def parse_edition_number(raw: str) -> Optional[int]:
    """``"N233"`` / ``"233"`` -> ``233``; anything else -> ``None``.

    Deliberately laxer than ``build_newsletter.normalize_newsletter_number``
    (which insists on exactly three digits) because this one *reads* whatever
    Notion holds. Formatting on the way back out is the strict side.
    """
    match = _NUMBER_RE.match((raw or "").strip())
    return int(match.group(1)) if match else None


def format_edition_number(value: int) -> str:
    """``233`` -> ``"N233"``, zero-padded to three digits.

    The padding is load-bearing: ``normalize_newsletter_number``
    (``newsletter/build_newsletter.py``) rejects anything that isn't exactly
    three digits, so an unpadded ``N34`` would break ④ Build downstream.
    """
    return f"N{value:03d}"


def plan_editions(
    *, latest_number: int, latest_date: date, count: int,
    cadence_days: int = CADENCE_DAYS,
) -> List[Tuple[str, date]]:
    """The ``count`` editions that follow ``latest_number`` / ``latest_date``.

    Returns ``[(number, date), …]`` in ascending order, where the *k*-th entry
    (1-based) is ``N{latest_number + k}`` dated ``latest_date + k·cadence``.
    Carrying the cadence off the latest row inherits its weekday rather than
    recomputing it, so the Saturday alignment holds without a calendar rule.

    ``count <= 0`` is a legitimate no-op and returns ``[]``.
    """
    if count <= 0:
        return []
    return [
        (format_edition_number(latest_number + k),
         latest_date + timedelta(days=cadence_days * k))
        for k in range(1, count + 1)
    ]


@dataclass(frozen=True)
class BufferState:
    """What the newsletter DB currently holds, as the scheduler sees it."""

    latest_number: int
    latest_date: date
    future_count: int
    total: int

    @property
    def latest_label(self) -> str:
        return format_edition_number(self.latest_number)

    def shortfall(self, target: int = DEFAULT_TARGET) -> int:
        return max(0, target - self.future_count)


def summarize_editions(
    rows: Iterable[Dict[str, Any]], *, today: date,
) -> Optional[BufferState]:
    """Fold raw Notion rows into a :class:`BufferState`.

    Takes the highest *number* and the highest *date* independently rather than
    reading both off one row: that is what makes a duplicate number impossible
    even if the table's numbering and dating ever disagree.

    Returns ``None`` when no row carries both a parsable number and a date —
    there is no sequence to continue from, and inventing one is the caller's
    problem to report, not this function's to guess.
    """
    numbers: List[int] = []
    dates: List[date] = []
    future = 0
    total = 0
    for row in rows:
        number = parse_edition_number(notion_io._read_title(row, "number"))  # noqa: SLF001
        raw_date = (row.get("properties", {}).get("Date", {}).get("date") or {}).get("start")
        parsed: Optional[date] = None
        if raw_date:
            try:
                # Notion dates may carry a time/offset; the day is all we use.
                parsed = date.fromisoformat(raw_date[:10])
            except ValueError:
                logger.warning("⚠️ Unparsable Date on edition %s: %r", number, raw_date)
        if number is None and parsed is None:
            continue
        total += 1
        if number is not None:
            numbers.append(number)
        if parsed is not None:
            dates.append(parsed)
            if parsed >= today:
                future += 1
    if not numbers or not dates:
        return None
    return BufferState(
        latest_number=max(numbers), latest_date=max(dates),
        future_count=future, total=total,
    )


# ---------------------------------------------------------------------------
# Notion-backed


def read_buffer_state(
    client: Any = None, *, newsletter_db_id: Optional[str] = None,
    today: Optional[date] = None,
) -> Optional[BufferState]:
    """Read the live buffer state from Notion.

    With no arguments it builds its own client from ``config.json``, so the
    Streamlit tab can call it for the caption without duplicating the wiring.
    """
    if client is None or newsletter_db_id is None:
        cfg = load_full_config()
        archive_cfg = cfg["newsletter_archive"]
        client = client or notion_io.init_client(cfg["notion"]["api_token"])
        newsletter_db_id = newsletter_db_id or archive_cfg["newsletter_db_id"]
    rows = notion_io.iter_newsletter_editions(client, newsletter_db_id=newsletter_db_id)
    return summarize_editions(rows, today=today or date.today())


def run(*, count: Optional[int] = None, target: int = DEFAULT_TARGET,
        dry_run: bool = False, debug: bool = False) -> int:
    """Create the missing future editions. Returns a process exit code.

    ``count`` creates exactly that many; without it the buffer is topped up to
    ``target``. The maximum is re-read here, at write time — never taken from
    whatever the app's cached caption last showed — so two runs can't collide
    on the same number.
    """
    setup_logger(
        "newsletter_archive", file_logging=True,
        level=logging.DEBUG if debug else logging.INFO,
    )
    cfg = load_full_config()
    if "newsletter_archive" not in cfg:
        logger.error("❌ config.json is missing the 'newsletter_archive' section")
        return 1
    newsletter_db_id = cfg["newsletter_archive"]["newsletter_db_id"]
    client = notion_io.init_client(cfg["notion"]["api_token"])

    logger.info("⏬ Reading newsletter editions from Notion…")
    state = read_buffer_state(client, newsletter_db_id=newsletter_db_id)
    if state is None:
        logger.error("❌ No newsletter edition with both a number and a Date — "
                     "nothing to continue the sequence from")
        return 1

    logger.info("📰 Latest %s (%s) · %d future edition(s) of %d total",
                state.latest_label, state.latest_date.isoformat(),
                state.future_count, state.total)

    to_create = count if count is not None else state.shortfall(target)
    if to_create <= 0:
        logger.info("✅ Buffer already full — %d future edition(s) ≥ target %d; "
                    "nothing to create", state.future_count, target)
        return 0

    plan = plan_editions(latest_number=state.latest_number,
                         latest_date=state.latest_date, count=to_create)
    verb = "Would create" if dry_run else "Creating"
    logger.info("🗓️  %s %d edition(s):", verb, len(plan))
    for number, edition_date in plan:
        logger.info("   %s → %s (%s)", number, edition_date.isoformat(),
                    edition_date.strftime("%A"))

    if dry_run:
        logger.info("🧪 [DRY-RUN] Nothing written to Notion")
        return 0

    for number, edition_date in plan:
        notion_io.create_newsletter_edition(
            client, newsletter_db_id=newsletter_db_id,
            number=number, edition_date=edition_date,
        )
    logger.info("🎉 Created %d future edition(s) — buffer now %d",
                len(plan), state.future_count + len(plan))
    return 0
