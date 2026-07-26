"""Substack native-API fetchers for the reporting pipeline.

Selected when ``substack_profile.source == "native"`` in ``config.json`` (routed
by ``reporting/social_client/social_api_client.py``). This is the lighter,
browser-free alternative to the Playwright scraper in
``reporting/scrape_client/substack.py`` — which is kept as the ``"playwright"``
source and is not removed.

``fetch_profile`` (follower count) and ``fetch_posts`` (note engagement) are both
implemented natively. The return envelopes are identical to the Playwright
scraper's, so everything downstream (``save_results`` → ``data_processor`` →
``profile_aggregator`` → ``notion_update``) is unchanged.

This module deliberately does **not** import
``reporting.scrape_client.substack`` — that module pulls in Playwright via
``substack_session``, which would defeat the whole point of the browser-free
path. The constants and record shape it shares with the Playwright scraper are
therefore restated here; see :data:`MAX_POSTS`.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from planning.substack.api_client import (  # noqa: E402
    SessionExpiredError,
    fetch_follower_count,
    fetch_own_notes,
)
from reporting.scrape_client.base import ScrapeError, normalize_target_date  # noqa: E402

logger = logging.getLogger("substack_native")

# Mirrors ``reporting.scrape_client.substack.MAX_POSTS`` — restated rather than
# imported so this module stays Playwright-free (see the module docstring).
MAX_POSTS = 10


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


def note_posted_date(iso_ts: Optional[str]) -> Optional[str]:
    """Convert a note's UTC ``date`` to a **local** ``YYYY-MM-DD``.

    The API returns UTC (``2026-07-26T04:03:14.591Z``) but the Playwright path
    read the timestamp Substack rendered in the *browser's local* timezone, and
    the posts consolidator matches on local days (``posted_at = date - 1 day``).
    Slicing the first ten characters of the UTC string would therefore shift any
    note posted after local midnight-minus-offset onto the wrong day. Convert
    first, then take the date.
    """
    if not iso_ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d")


def note_is_teaser(attachments: list[dict]) -> bool:
    """True when a note embeds a newsletter post preview (issue #84's teaser).

    The Playwright equivalent looked for an ``<a href*='/p/'>`` card inside the
    note container. Natively the same signal is an attachment: either a ``post``
    attachment, or a ``link`` attachment whose ``linkMetadata.url`` is a ``/p/``
    permalink. Plain outbound links (a personal site, say) are *not* teasers, so
    the URL test matters — ordinary daily notes do carry link attachments.
    """
    for att in attachments or []:
        if att.get("type") == "post":
            return True
        url = (att.get("linkMetadata") or {}).get("url") or ""
        if "/p/" in url:
            return True
    return False


def note_record(comment: dict, handle: str) -> dict:
    """Map one raw Note payload to the reporting record shape.

    Field-for-field equivalent to the Playwright scraper's
    ``_scrape_note_permalink`` return value, including the transient
    ``is_teaser`` key that :func:`fetch_posts` pops. The three engagement
    numbers are the same values Substack's own feed renders in the note
    toolbar (``reaction_count`` / ``children_count`` / ``restacks``).

    Pure — no session, no network — so it is unit-testable on fixtures.
    """
    attachments = comment.get("attachments") or []
    types = {att.get("type") for att in attachments}
    return {
        "post_id": f"https://substack.com/@{handle}/note/c-{comment.get('id')}",
        "posted_at": note_posted_date(comment.get("date")),
        "is_video": 1 if "video" in types else 0,
        "num_likes": int(comment.get("reaction_count") or 0),
        "num_comments": int(comment.get("children_count") or 0),
        "num_reshares": int(comment.get("restacks") or 0),
        "is_teaser": note_is_teaser(attachments),
    }


def fetch_posts(target_date: Optional[str] = None) -> Optional[dict]:
    """Return ``{"posts": [...]}`` — note engagement via the native API.

    Mirrors ``reporting.scrape_client.substack.fetch_posts`` exactly so the two
    sources are drop-in interchangeable via the ``source`` config flag, but
    replaces the feed scroll + one page load per note with a couple of GETs.
    """
    target_date = normalize_target_date(target_date)
    logger.info("🚀 Substack native fetch_posts — date=%s", target_date)
    try:
        # Over-fetch like the Playwright path does, so dropping teasers still
        # leaves a full MAX_POSTS window of real daily notes.
        handle, comments = fetch_own_notes(limit=MAX_POSTS + 2)
    except SessionExpiredError as err:
        raise ScrapeError(f"Substack native session expired: {err}") from err
    except Exception as err:  # noqa: BLE001
        raise ScrapeError(f"Substack native note fetch failed: {err}") from err

    if not comments:
        raise ScrapeError("Substack native: no notes returned by the profile feed.")
    logger.info("ℹ️ Substack notes fetched (native): %d", len(comments))

    posts: list[dict] = []
    for comment in comments:
        if len(posts) >= MAX_POSTS:
            break
        rec = note_record(comment, handle)
        if rec.pop("is_teaser", False):
            logger.info(
                "⏭️ Substack native: skipping newsletter-teaser note %s.",
                comment.get("id"),
            )
            continue
        posts.append(rec)
        logger.debug(
            "📌 Substack native %s — likes=%d comments=%d reshares=%d posted_at=%s video=%d",
            comment.get("id"), rec["num_likes"], rec["num_comments"],
            rec["num_reshares"], rec["posted_at"], rec["is_video"],
        )

    if not posts:
        raise ScrapeError("Substack native: every note was filtered out as a teaser.")
    logger.info("✅ Substack notes (native): %d", len(posts))
    return {"posts": posts}
