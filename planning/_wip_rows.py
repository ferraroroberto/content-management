"""Shared WIP-row query + payload resolution for the planning schedulers.

``iter_wip_pages`` is the query / day-parse skeleton every scheduler's fetch
(Twitter, Threads, Instagram, LinkedIn, Videos) is built on.

``fetch_wip_tw_rows`` (twitter) and ``fetch_wip_th_rows`` (threads) were a
~100-line near-identical block, differing only by a ``_tw``/``_th``
column-name suffix on ``ScheduleRow``'s fields; ``PostPayload``,
``_resolve_image_path`` and ``_illustration_filename`` were verbatim
duplicates. Both platforms read the same ``editorial_columns`` keys
(``wip_checkbox`` / ``title_day`` / ``illustration_rel`` / ``caption_text`` /
``post_url``), so once ``ScheduleRow``'s fields are generic there is nothing
platform-specific left in the fetch itself.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from planning._dates import date_to_day_title
from reporting.notion.editorial import get_field, query_rows_by_filter, retrieve_page


@dataclass
class ScheduleRow:
    page_id: str
    day: date
    illustration_ids: list[str]
    text: str
    existing_post_url: Optional[str]

    @property
    def day_title(self) -> str:
        return date_to_day_title(self.day)


@dataclass
class IgRow:
    """Snapshot of one editorial row's IG side, as read from Notion."""

    page_id: str
    day: date
    illustration_ig_ids: list[str]
    text_ig: str
    repost_ig: bool
    thread_ig: bool
    post_ig_ids: list[str]
    existing_post_url: Optional[str]
    raw_properties: dict  # for force-check on target columns

    @property
    def day_title(self) -> str:
        return date_to_day_title(self.day)


@dataclass
class PostPayload:
    image_path: Path
    caption: str


def _title_day(page: dict, title_col: str) -> Optional[date]:
    """Parse the YYYYMMDD title back into a date; None if empty / unparseable."""
    title_prop = page.get("properties", {}).get(title_col, {}) or {}
    segs = title_prop.get("title", []) or []
    text = "".join(seg.get("plain_text", "") for seg in segs).strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def iter_wip_pages(
    notion,
    db_id: str,
    *,
    wip_col: str,
    title_col: str,
    days: Optional[list[date]],
    logger: logging.Logger,
) -> Iterator[tuple[dict, date]]:
    """Yield ``(raw_page, day)`` for every WIP-checked editorial row in scope.

    If ``days`` is None, one unfiltered query returns every WIP row (the
    ``--all-wip`` mode) and each row's day comes from its YYYYMMDD title;
    otherwise the DB is queried once per day with a title-equals filter. Rows
    whose title is not a YYYYMMDD day are logged and skipped. Every scheduler's
    fetch builds its own row type from the yielded raw pages, so the query /
    day-parse skeleton lives only here.
    """

    def _valid(results: list[dict], default_day: Optional[date]) -> Iterator[tuple[dict, date]]:
        for r in results:
            row_day = default_day or _title_day(r, title_col)
            if row_day is None:
                logger.warning(
                    "⚠️  Skipping row %s: day title is empty / not YYYYMMDD. "
                    "Likely a stale scratch/template row — consider archiving "
                    "it in Notion (%s).",
                    r.get("id"), r.get("url") or "(no url)",
                )
                continue
            yield r, row_day

    if days is None:
        results = query_rows_by_filter(
            notion,
            db_id,
            filter_obj={"property": wip_col, "checkbox": {"equals": True}},
        )
        yield from _valid(results, None)
        return
    for d in days:
        results = query_rows_by_filter(
            notion,
            db_id,
            filter_obj={
                "and": [
                    {"property": title_col, "title": {"equals": date_to_day_title(d)}},
                    {"property": wip_col, "checkbox": {"equals": True}},
                ]
            },
        )
        yield from _valid(results, d)


def fetch_wip_rows(
    notion,
    db_id: str,
    ed_cols: dict,
    days: Optional[list[date]],
    *,
    logger: logging.Logger,
) -> list[ScheduleRow]:
    """Fetch WIP rows. If ``days`` is None, returns every WIP row
    (used by ``--all-wip`` mode); otherwise filters by title-equals per day."""
    illust_col = ed_cols["illustration_rel"]
    text_col = ed_cols["caption_text"]
    post_url_col = ed_cols["post_url"]

    rows: list[ScheduleRow] = []
    for r, row_day in iter_wip_pages(
        notion, db_id,
        wip_col=ed_cols["wip_checkbox"],
        title_col=ed_cols["title_day"],
        days=days,
        logger=logger,
    ):
        props = r.get("properties", {})
        illust_rels = props.get(illust_col, {}).get("relation", []) or []
        text_rt = props.get(text_col, {}).get("rich_text", []) or []
        text_val = "".join(seg.get("plain_text", "") for seg in text_rt).strip()
        url_obj = props.get(post_url_col, {})
        existing_url = url_obj.get("url") if url_obj.get("type") == "url" else None
        rows.append(
            ScheduleRow(
                page_id=r["id"],
                day=row_day,
                illustration_ids=[rel["id"] for rel in illust_rels],
                text=text_val,
                existing_post_url=existing_url,
            )
        )
    rows.sort(key=lambda r: r.day)
    return rows


def fetch_wip_ig_rows(
    notion,
    db_id: str,
    ed_cols: dict,
    days: Optional[list[date]],
    *,
    logger: logging.Logger,
) -> list[IgRow]:
    """Fetch WIP-IG rows for the Instagram scheduler and the IG clone step.

    If ``days`` is None, returns every WIP-IG row (``--all-wip`` mode);
    otherwise filters by title-equals per day. Both callers read the same IG
    columns, so a renamed Notion column is fixed here once.
    """
    illust_col = ed_cols["illustration_rel"]
    text_col = ed_cols["caption_text"]
    repost_col = ed_cols["repost_checkbox"]
    thread_col = ed_cols["thread_checkbox"]
    post_col = ed_cols["post_rel"]
    post_url_col = ed_cols["post_url"]

    rows: list[IgRow] = []
    for r, row_day in iter_wip_pages(
        notion, db_id,
        wip_col=ed_cols["wip_checkbox"],
        title_col=ed_cols["title_day"],
        days=days,
        logger=logger,
    ):
        props = r.get("properties", {})
        illust_rels = props.get(illust_col, {}).get("relation", []) or []
        post_rels = props.get(post_col, {}).get("relation", []) or []
        text_rt = props.get(text_col, {}).get("rich_text", []) or []
        text_val = "".join(seg.get("plain_text", "") for seg in text_rt).strip()
        url_obj = props.get(post_url_col, {})
        existing_url = url_obj.get("url") if url_obj.get("type") == "url" else None
        rows.append(
            IgRow(
                page_id=r["id"],
                day=row_day,
                illustration_ig_ids=[rel["id"] for rel in illust_rels],
                text_ig=text_val,
                repost_ig=bool(props.get(repost_col, {}).get("checkbox", False)),
                thread_ig=bool(props.get(thread_col, {}).get("checkbox", False)),
                post_ig_ids=[rel["id"] for rel in post_rels],
                existing_post_url=existing_url,
                raw_properties=props,
            )
        )
    rows.sort(key=lambda r: r.day)
    return rows


def resolve_image_path(
    folder: str,
    image_filename: Optional[str],
    *,
    missing: str = "Illustration row has no filename.",
) -> Path:
    """Resolve <folder>/<name>.png. Accepts a name with or without extension.

    The Notion formula output occasionally has comma-joined names — the first
    wins. An empty or blank first name raises ``FileNotFoundError(missing)``
    instead of resolving to ``folder`` itself; ``missing`` lets a caller keep
    its own wording for that case.
    """
    first = str(image_filename or "").split(",")[0].strip()
    if not first:
        raise FileNotFoundError(missing)
    if not first.lower().endswith(".png"):
        first = f"{first}.png"
    candidate = Path(folder) / first
    if not candidate.exists():
        raise FileNotFoundError(f"Illustration not found: {candidate}")
    return candidate


def illustration_filename(notion, illustration_page_id: str, illust_cols: dict) -> str:
    page = retrieve_page(notion, illustration_page_id)
    name = get_field(page, "image_filename", illust_cols) or ""
    return str(name).strip()


def resolve_payload(notion, cfg: dict, row: ScheduleRow, *, platform_label: str) -> PostPayload:
    """Build the (image path, caption) for the day's scheduled post.

    ``platform_label`` (e.g. ``"TW"``, ``"TH"``) names the platform in the
    empty-illustration error message only.
    """
    illust_cols = cfg["illustration_columns"]
    folder = cfg["illustrations_folder"]
    if not row.illustration_ids:
        raise RuntimeError(f"{row.day_title}: illustration {platform_label} is empty.")
    fname = illustration_filename(notion, row.illustration_ids[0], illust_cols)
    return PostPayload(
        image_path=resolve_image_path(folder, fname),
        caption=row.text,
    )
