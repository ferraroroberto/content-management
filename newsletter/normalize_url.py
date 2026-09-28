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
import logging
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from config.loader import load_full_config
from config.logger_config import configure_root_logging
from newsletter._normalizer_base import NotionArticleNormalizer, run_cli


class NotionURLNormalizer(NotionArticleNormalizer):
    """Strip tracking query params + fragments from article ``link`` URLs."""

    changed_log_prefix = "Cleaned: "
    unchanged_log_text = "Already clean"

    def __init__(self):
        self.config = self._load_config()
        self.domains_preserving_params = set(self.config.get("domains_preserving_params", []))
        self._setup_api_credentials(self.config["notion_api_key"], self.config["database_id"])
        logging.info("✅ URL normalizer initialized")
        logging.info(f"📊 Database ID: {self.database_id}")
        logging.info(f"🛡️ Preserved domains: {sorted(self.domains_preserving_params)}")

    @staticmethod
    def _load_config() -> Dict[str, Any]:
        proj = load_full_config()
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

    def _transform(self, original: str) -> str:
        return self._clean_url(original)

    def _patch_properties(self, new_value: str) -> Dict[str, Any]:
        return {"link": {"url": new_value}}

    def _result_row(self, page_id: str, last_edited: str, original: str, new_value: str) -> Dict[str, Any]:
        return {"page_id": page_id, "original_url": original, "cleaned_url": new_value}

    def process_database(self, days: int, dry_run: bool = False,
                         testing_mode: bool = False) -> List[Dict[str, Any]]:
        note = self._validity_note if testing_mode else None
        return super().process_database(days, dry_run, note=note)

    def _validity_note(self, url: str) -> str:
        ok, msg = self._check_url_validity(url)
        return f" [{'✅' if ok else '❌'} {msg}]"


# --------------------------------------------------------------- callable entry


def run(days: int = 14, dry_run: bool = False, testing_mode: bool = False,
        debug: bool = False) -> List[Dict[str, Any]]:
    configure_root_logging(debug)
    return NotionURLNormalizer().process_database(
        days=days, dry_run=dry_run, testing_mode=testing_mode,
    )


def main() -> int:
    def _test(args: argparse.Namespace) -> None:
        normaliser = NotionURLNormalizer()
        out = normaliser._clean_url(args.test)
        logging.info("Original: %s", args.test)
        logging.info("Cleaned:  %s", out)
        logging.info("Changed:  %s", "yes" if args.test != out else "no")
        if args.testing:
            ok, msg = normaliser._check_url_validity(out)
            logging.info("Validation: %s %s", "✅" if ok else "❌", msg)

    def _run(args: argparse.Namespace) -> None:
        run(days=args.days, dry_run=args.dry_run,
            testing_mode=args.testing, debug=args.debug)

    return run_cli(
        __doc__,
        test_help="Clean one URL and exit",
        on_test=_test,
        on_run=_run,
        extra_args=lambda p: p.add_argument(
            "--testing", action="store_true",
            help="HEAD/GET each cleaned URL to verify it resolves"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
