"""Shared workflow for the Notion article normalisers.

``normalize_url.NotionURLNormalizer`` and ``normalize_names.NotionNameNormalizer``
were twins: the same "query articles created in the last N days -> transform one
property -> dry-run or update -> stats" loop, the same Notion-error handling and
the same CLI shell. Only the property that is read, the transform and the
Notion patch differ, so those are the hooks a subclass fills in; everything else
lives here once.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from config.logger_config import configure_root_logging
from newsletter import notion_io


class NotionArticleNormalizer:
    """Base class: subclasses implement the per-property hooks below."""

    #: Prefix on the live "changed" log line (``📝 <prefix>"a" → "b"``).
    changed_log_prefix = ""
    #: Text of the "already normalised" log line.
    unchanged_log_text = "Already normalised"

    notion_api_key: str
    database_id: str
    client: Any

    # ----------------------------------------------------- per-property hooks

    def _extract_page_info(self, page: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
        """Return ``(page_id, last_edited_time, current_value)`` or None to skip the page."""
        raise NotImplementedError

    def _transform(self, original: str) -> str:
        """Return the normalised value for ``original``."""
        raise NotImplementedError

    def _patch_properties(self, new_value: str) -> Dict[str, Any]:
        """Return the Notion ``properties`` payload that writes ``new_value``."""
        raise NotImplementedError

    def _result_row(self, page_id: str, last_edited: str, original: str, new_value: str) -> Dict[str, Any]:
        """Return the per-page dict ``process_database`` reports back."""
        raise NotImplementedError

    # -------------------------------------------------------------- shared I/O

    def _setup_api_credentials(self, notion_api_key: str, database_id: str) -> None:
        if not all([notion_api_key, database_id]):
            raise ValueError("Missing notion_api_key or articles_db_id in config")
        self.notion_api_key = notion_api_key
        self.database_id = database_id
        self.client = notion_io.init_client(notion_api_key)

    def _query_notion_database(self, days: int) -> List[Dict[str, Any]]:
        filter_date = datetime.utcnow() - timedelta(days=days)
        filter_date_str = filter_date.isoformat() + "Z"
        logging.info(
            f"🔍 Querying articles created since {filter_date_str} ({days} days back)"
        )
        query_filter = {"and": [{"property": "created", "created_time": {"after": filter_date_str}}]}
        sorts = [{"property": "created", "direction": "descending"}]
        try:
            pages = notion_io.query_database(
                self.client, self.database_id, query_filter=query_filter, sorts=sorts,
            )
        except Exception as e:
            logging.error(f"❌ Notion API error: {e}")
            raise
        logging.info(f"📊 Total pages retrieved: {len(pages)}")
        return pages

    def _update_page(self, page_id: str, new_value: str) -> bool:
        try:
            notion_io.update_page(self.client, page_id, self._patch_properties(new_value))
            return True
        except Exception as e:
            logging.error(f"❌ Failed to update page {page_id[:8]}…: {e}")
            return False

    # -------------------------------------------------------------- processing

    def process_database(
        self,
        days: int,
        dry_run: bool = False,
        *,
        note: Optional[Callable[[str], str]] = None,
    ) -> List[Dict[str, Any]]:
        """Normalise every article created in the last ``days`` days.

        ``note(new_value)`` optionally returns a suffix appended to each log line
        (``normalize_url``'s ``--testing`` reachability check).
        """
        pages = self._query_notion_database(days)
        results: List[Dict[str, Any]] = []
        stats = {"processed": 0, "updated": 0, "unchanged": 0, "would_update": 0}
        if dry_run:
            logging.info("🔍 DRY RUN MODE: no Notion writes")
        for page in pages:
            info = self._extract_page_info(page)
            if not info:
                continue
            page_id, last_edited, original = info
            new_value = self._transform(original)
            suffix = note(new_value) if note else ""
            results.append(self._result_row(page_id, last_edited, original, new_value))
            stats["processed"] += 1
            if original != new_value:
                if dry_run:
                    stats["would_update"] += 1
                    logging.info(f'📝 [DRY RUN] "{original}" → "{new_value}"{suffix}')
                elif self._update_page(page_id, new_value):
                    stats["updated"] += 1
                    logging.info(f'📝 {self.changed_log_prefix}"{original}" → "{new_value}"{suffix}')
                else:
                    logging.error(f'❌ Failed to update: "{original}"')
            else:
                stats["unchanged"] += 1
                logging.info(f'✅ {self.unchanged_log_text}: "{original}"{suffix}')
        changed_word, changed_key = ("would update", "would_update") if dry_run else ("updated", "updated")
        logging.info(
            f"✅ Processed {stats['processed']} pages — {changed_word} {stats[changed_key]}, "
            f"unchanged {stats['unchanged']}"
        )
        return results


def run_cli(
    description: str,
    *,
    test_help: str,
    on_test: Callable[[argparse.Namespace], None],
    on_run: Callable[[argparse.Namespace], None],
    extra_args: Optional[Callable[[argparse.ArgumentParser], None]] = None,
    days_help: Optional[str] = None,
) -> int:
    """Shared ``main()`` shell: parse the common flags, dispatch ``--test`` or a run.

    ``on_test(args)`` handles ``--test <value>``; ``on_run(args)`` runs the normaliser.
    Any exception is logged as a fatal error and turned into exit code 1.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--days", type=int, default=14, help=days_help)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--test", type=str, help=test_help)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show changes without writing to Notion")
    if extra_args:
        extra_args(parser)
    args = parser.parse_args()
    configure_root_logging(args.debug)
    try:
        if args.test:
            on_test(args)
            return 0
        on_run(args)
        logging.info("✅ Done")
        return 0
    except Exception as e:
        logging.error(f"❌ Fatal: {e}")
        if args.debug:
            logging.exception("Traceback:")
        return 1
