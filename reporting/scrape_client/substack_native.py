"""Substack native-API fetchers for the reporting pipeline.

Selected when ``substack_profile.source == "native"`` in ``config.json`` (routed
by ``reporting/social_client/social_api_client.py``). This is the lighter,
browser-free alternative to the Playwright scraper in
``reporting/scrape_client/substack.py`` — which is kept as the ``"playwright"``
source and is not removed.

Only ``fetch_profile`` (follower count) is implemented natively today; note
engagement still goes through the Playwright path until the Notes endpoints are
reverse-engineered. The return envelope is identical to the Playwright scraper's,
so everything downstream (``save_results`` → ``data_processor`` →
``profile_aggregator`` → ``notion_update``) is unchanged.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from planning.substack.api_client import (  # noqa: E402
    SessionExpiredError,
    fetch_follower_count,
)
from reporting.scrape_client.base import ScrapeError, normalize_target_date  # noqa: E402

logger = logging.getLogger("substack_native")


def fetch_profile(target_date: Optional[str] = None) -> Optional[dict]:
    """Return ``{"num_followers": N}`` via the native HTTP API (no browser).

    Mirrors ``reporting.scrape_client.substack.fetch_profile`` exactly so the two
    sources are drop-in interchangeable via the ``source`` config flag.
    """
    target_date = normalize_target_date(target_date)
    logger.info("🚀 Substack native fetch_profile — date=%s", target_date)
    try:
        count = fetch_follower_count()
    except SessionExpiredError as err:
        raise ScrapeError(f"Substack native session expired: {err}") from err
    except Exception as err:  # noqa: BLE001
        raise ScrapeError(f"Substack native follower fetch failed: {err}") from err
    logger.info("✅ Substack followers (native): %d", count)
    return {"num_followers": count}
