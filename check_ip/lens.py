"""Google Lens reverse-image search via SerpAPI.

Ported from the sibling repo's ``linkedin/check_ip/search_client.py``. The one
real change is where the response goes: the payload is written whole to a
sidecar file under the store and ``api_history`` keeps only the metadata plus
a path to it. The Excel version pushed the payload into a spreadsheet cell,
which capped it at 32,767 characters and truncated 47% of what it recorded.

Each call costs SerpAPI credit, so callers gate their own runs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from check_ip import db  # noqa: E402

logger = logging.getLogger("check_ip.lens")

SEARCH_TYPES = ("exact_matches", "visual_matches")


class LensClient:
    """One SerpAPI Google Lens search per call, logged to the store."""

    def __init__(self, api_key: str, endpoint: str = "https://serpapi.com/search"):
        self.api_key = api_key
        self.endpoint = endpoint

    def validate(self) -> bool:
        """Confirm the key works before a run spends a thousand calls on it."""
        try:
            response = requests.get(self.endpoint,
                                    params={"engine": "google", "q": "test", "api_key": self.api_key},
                                    timeout=60)
        except requests.RequestException as err:
            logger.error("❌ could not reach SerpAPI: %s", err)
            return False
        if response.status_code == 200:
            logger.info("✅ SerpAPI key accepted")
            return True
        logger.error("❌ SerpAPI rejected the key: HTTP %s", response.status_code)
        return False

    def search(
        self,
        conn: sqlite3.Connection,
        image_url: str,
        local_image: str,
        search_type: str = "exact_matches",
    ) -> Optional[dict]:
        """Run one search and record it. Returns the parsed response, or None."""
        params = {
            "engine": "google_lens",
            "url": image_url,
            "type": search_type,
            "api_key": self.api_key,
            "no_cache": True,  # a cached hit would hide new reuse, which is the point
        }
        try:
            response = requests.get(self.endpoint, params=params, timeout=180)
        except requests.RequestException as err:
            logger.error("❌ %s search failed for %s: %s", search_type, local_image, err)
            return None

        if response.status_code != 200:
            logger.error("❌ SerpAPI returned HTTP %s for %s (%s)",
                         response.status_code, local_image, search_type)
            return None

        try:
            data = response.json()
        except ValueError as err:
            logger.error("❌ SerpAPI sent unparseable JSON for %s: %s", local_image, err)
            return None

        self._record(conn, data, local_image, image_url, search_type)
        return data

    def _record(self, conn: sqlite3.Connection, data: dict, local_image: str,
                image_url: str, search_type: str) -> None:
        """Log the call: payload to a sidecar file, metadata to the store."""
        meta = data.get("search_metadata", {}) or {}
        params = data.get("search_parameters", {}) or {}
        api_id = meta.get("id")

        raw_path = None
        if api_id:
            target = db.raw_sidecar(api_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            raw_path = str(target.relative_to(db.store_dir())).replace("\\", "/")

        playground = (
            f"https://serpapi.com/playground?engine=google_lens"
            f"&url={urllib.parse.quote(image_url)}&type={search_type}"
        )

        conn.execute(
            """
            insert into api_history
                (api_id, search_type, local_image, imgur_url, search_date, api_status,
                 json_endpoint, created_at, processed_at, total_time_taken, engine, url,
                 search_engine_query, playground_link, search_type_param, raw_path, raw_truncated)
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            on conflict(api_id) do update set
                raw_path = coalesce(excluded.raw_path, api_history.raw_path)
            """,
            (
                api_id,
                f"{search_type.replace('_', ' ').title()} Search",
                local_image,
                image_url,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                meta.get("status"),
                meta.get("json_endpoint"),
                meta.get("created_at"),
                meta.get("processed_at"),
                meta.get("total_time_taken"),
                params.get("engine"),
                params.get("url"),
                meta.get("google_lens_url"),
                playground,
                search_type,
                raw_path,
            ),
        )
        conn.commit()
