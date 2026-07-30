"""Shared WIP-row fetch + payload resolution for the twitter/threads schedulers.

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
from typing import Optional

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
class PostPayload:
    image_path: Path
    caption: str


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
    wip_col = ed_cols["wip_checkbox"]
    title_col = ed_cols["title_day"]
    illust_col = ed_cols["illustration_rel"]
    text_col = ed_cols["caption_text"]
    post_url_col = ed_cols["post_url"]

    rows: list[ScheduleRow] = []

    def _row_day(r: dict) -> Optional[date]:
        title_prop = r.get("properties", {}).get(title_col, {}) or {}
        segs = title_prop.get("title", []) or []
        text = "".join(seg.get("plain_text", "") for seg in segs).strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None

    def _ingest(results, default_day: Optional[date]):
        for r in results:
            props = r.get("properties", {})
            row_day = default_day or _row_day(r)
            if row_day is None:
                logger.warning("⚠️  Skipping row %s: unparseable day title.", r.get("id"))
                continue
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

    if days is None:
        results = query_rows_by_filter(
            notion,
            db_id,
            filter_obj={"property": wip_col, "checkbox": {"equals": True}},
        )
        _ingest(results, default_day=None)
    else:
        for d in days:
            title = date_to_day_title(d)
            results = query_rows_by_filter(
                notion,
                db_id,
                filter_obj={
                    "and": [
                        {"property": title_col, "title": {"equals": title}},
                        {"property": wip_col, "checkbox": {"equals": True}},
                    ]
                },
            )
            _ingest(results, default_day=d)

    rows.sort(key=lambda r: r.day)
    return rows


def resolve_image_path(folder: str, image_filename: str) -> Path:
    """Resolve <folder>/<name>.png. Accepts a name with or without extension."""
    if not image_filename:
        raise FileNotFoundError("Illustration row has no filename.")
    first = str(image_filename).split(",")[0].strip()
    if first and not first.lower().endswith(".png"):
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
