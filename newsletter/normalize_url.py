#!/usr/bin/env python3
"""Notion article URL normaliser.

Strips query parameters and fragments from each article's ``link`` URL,
EXCEPT for domains in the preserve list (YouTube / Vimeo / Twitter / X —
those use query strings to identify the video / tweet).

Optionally also pings each cleaned URL (HEAD then GET fallback) to confirm
the page still resolves.

Originally from ``E:\\automation\\automation\\notion\\normalize_url.py``;
migrated into the newsletter package as part of issue #18. Config now comes
from ``config/config.json`` (Notion token, articles DB id, and
``newsletter_archive.url_preserve_domains``).

CLI:
    python -m newsletter.normalize_url --days 14
    python -m newsletter.normalize_url --days 7 --dry-run --testing
    python -m newsletter.normalize_url --test "https://example.com?utm_source=x"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECT_CONFIG = REPO_ROOT / "config" / "config.json"


class NotionURLNormalizer:
    """Strip tracking query params + fragments from article ``link`` URLs."""

    def __init__(self):
        self.config = self._load_config()
        self.notion_api_key = self.config["notion_api_key"]
        self.database_id = self.config["database_id"]
        self.domains_preserving_params = set(self.config.get("domains_preserving_params", []))
        if not all([self.notion_api_key, self.database_id]):
            raise ValueError("Missing notion_api_key or articles_db_id in config")
        self.headers = {
            "Authorization": f"Bearer {self.notion_api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
        logging.info("✅ URL normalizer initialized")
        logging.info(f"📊 Database ID: {self.database_id}")
        logging.info(f"🛡️ Preserved domains: {sorted(self.domains_preserving_params)}")

    @staticmethod
    def _load_config() -> Dict[str, Any]:
        with PROJECT_CONFIG.open("r", encoding="utf-8") as f:
            proj = json.load(f)
        archive = proj.get("newsletter_archive", {})
        return {
            "notion_api_key": proj["notion"]["api_token"],
            "database_id": archive["articles_db_id"],
            "domains_preserving_params": archive.get("url_preserve_domains", []),
        }

    def _clean_url(self, original_url: str) -> str:
        if not original_url:
            return original_url
        try:
            parsed = urlparse(original_url)
            domain = parsed.netloc.lower()
            if any(p in domain for p in self.domains_preserving_params):
                return original_url
            return urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", "")
            )
        except Exception as e:
            logging.warning(f"⚠️ Failed to parse URL '{original_url}': {e}")
            return original_url

    def _check_url_validity(self, url: str) -> Tuple[bool, str]:
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                )
            }
            r = requests.head(url, headers=headers, allow_redirects=True, timeout=10)
            if r.status_code == 405:
                r = requests.get(url, headers=headers, stream=True, timeout=10)
            if 200 <= r.status_code < 400:
                return True, f"OK ({r.status_code})"
            return False, f"Error: {r.status_code}"
        except requests.RequestException as e:
            return False, f"Failed: {type(e).__name__}"

    def _query_notion_database(self, days: int) -> List[Dict[str, Any]]:
        filter_date = datetime.utcnow() - timedelta(days=days)
        filter_date_str = filter_date.isoformat() + "Z"
        logging.info(
            f"🔍 Querying articles created since {filter_date_str} ({days} days back)"
        )
        body: Dict[str, Any] = {
            "filter": {"and": [{"property": "created", "created_time": {"after": filter_date_str}}]},
            "sorts": [{"property": "created", "direction": "descending"}],
        }
        pages: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            if cursor:
                body["start_cursor"] = cursor
            try:
                resp = requests.post(
                    f"https://api.notion.com/v1/databases/{self.database_id}/query",
                    headers=self.headers, json=body, timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.RequestException as e:
                logging.error(f"❌ Notion API error: {e}")
                raise
            results = data.get("results", [])
            if not results:
                break
            pages.extend(results)
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        logging.info(f"📊 Total pages retrieved: {len(pages)}")
        return pages

    @staticmethod
    def _extract_page_info(page: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
        page_id = page.get("id", "")
        last_edited = page.get("last_edited_time", "")
        prop = page.get("properties", {}).get("link", {})
        if not prop or prop.get("type") != "url":
            return None
        url_content = (prop.get("url") or "").strip()
        if not url_content:
            return None
        return page_id, last_edited, url_content

    def _update_page_url(self, page_id: str, new_url: str) -> bool:
        body = {"properties": {"link": {"url": new_url}}}
        try:
            resp = requests.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=self.headers, json=body, timeout=30,
            )
            resp.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"❌ Failed to update page {page_id[:8]}…: {e}")
            return False

    def process_database(self, days: int, dry_run: bool = False,
                         testing_mode: bool = False) -> List[Dict[str, Any]]:
        pages = self._query_notion_database(days)
        results: List[Dict[str, Any]] = []
        stats = {"processed": 0, "updated": 0, "unchanged": 0, "would_update": 0}
        if dry_run:
            logging.info("🔍 DRY RUN MODE: no Notion writes")
        for page in pages:
            info = self._extract_page_info(page)
            if not info:
                continue
            page_id, _, original_url = info
            cleaned = self._clean_url(original_url)
            validation = ""
            if testing_mode:
                ok, msg = self._check_url_validity(cleaned)
                validation = f" [{'✅' if ok else '❌'} {msg}]"
            results.append({"page_id": page_id, "original_url": original_url, "cleaned_url": cleaned})
            stats["processed"] += 1
            if original_url != cleaned:
                if dry_run:
                    stats["would_update"] += 1
                    logging.info(f'📝 [DRY RUN] "{original_url}" → "{cleaned}"{validation}')
                else:
                    if self._update_page_url(page_id, cleaned):
                        stats["updated"] += 1
                        logging.info(f'📝 Cleaned: "{original_url}" → "{cleaned}"{validation}')
                    else:
                        logging.error(f'❌ Failed to update: "{original_url}"')
            else:
                stats["unchanged"] += 1
                logging.info(f'✅ Already clean: "{original_url}"{validation}')
        if dry_run:
            logging.info(f"✅ Processed {stats['processed']} pages — would update {stats['would_update']}, unchanged {stats['unchanged']}")
        else:
            logging.info(f"✅ Processed {stats['processed']} pages — updated {stats['updated']}, unchanged {stats['unchanged']}")
        return results


# --------------------------------------------------------------- callable entry


def run(days: int = 14, dry_run: bool = False, testing_mode: bool = False,
        debug: bool = False) -> List[Dict[str, Any]]:
    _setup_logging(debug)
    return NotionURLNormalizer().process_database(
        days=days, dry_run=dry_run, testing_mode=testing_mode,
    )


def _setup_logging(debug: bool = False):
    level = logging.DEBUG if debug else logging.INFO
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
    else:
        logging.getLogger().setLevel(level)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--test", type=str, help="Clean one URL and exit")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--testing", action="store_true",
                        help="HEAD/GET each cleaned URL to verify it resolves")
    args = parser.parse_args()
    _setup_logging(args.debug)
    try:
        if args.test:
            normaliser = NotionURLNormalizer()
            out = normaliser._clean_url(args.test)
            logging.info("Original: %s", args.test)
            logging.info("Cleaned:  %s", out)
            logging.info("Changed:  %s", "yes" if args.test != out else "no")
            if args.testing:
                ok, msg = normaliser._check_url_validity(out)
                logging.info("Validation: %s %s", "✅" if ok else "❌", msg)
            return 0
        run(days=args.days, dry_run=args.dry_run,
            testing_mode=args.testing, debug=args.debug)
        logging.info("✅ Done")
        return 0
    except Exception as e:
        logging.error(f"❌ Fatal: {e}")
        if args.debug:
            logging.exception("Traceback:")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
