"""Substack Playwright scraper — fetches profile + posts via the logged-in browser.

Reuses ``planning/substack`` persistent profile via ``SubstackSession``.

Profile follower count: drives ``substack.stats_audience_url`` and reads the
``"Total followers (N)"`` line — this is the same logic that used to live in
``planning/substack/update_substack_followers.py``, folded in here so all
five platforms share a uniform shape.

Posts: ``https://substack.com/@<handle>`` activity feed. The first entry is
always the newsletter teaser (the user explicitly told us to skip it) so we
drop ``index == 0`` and start from the second item. For each note:
  * ``post_id``       full ``https://substack.com/@<handle>/note/c-<id>`` URL.
  * ``posted_at``     from the ``<time datetime>`` attribute on the note.
  * ``is_video``      ``<video>`` element presence inside the note container.
  * ``num_likes``     reaction count from the note's engagement toolbar.
  * ``num_comments``  reply count.
  * ``num_reshares``  restack count.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from planning.substack.substack_session import (  # noqa: E402
    LoginRequiredError,
    SubstackSession,
    load_substack_config,
)
from reporting.scrape_client.base import (  # noqa: E402
    ScrapeError,
    human_date_to_iso_date,
    normalize_target_date,
    parse_int,
)

logger = logging.getLogger("substack_scrape")

_TOTAL_FOLLOWERS_RE = re.compile(r"Total followers\s*\(([\d,]+)\)", re.IGNORECASE)
_NOTE_PATH_RE = re.compile(r"^/@([^/]+)/note/(c-\d+)")

MAX_POSTS = 10
SCROLLS = 6
SCROLL_PAUSE_MS = 1500


def fetch_profile(target_date: Optional[str] = None) -> Optional[dict]:
    """Scrape the Substack 'Total followers (N)' counter.

    Folded-in equivalent of the previous
    ``planning/substack/update_substack_followers.py``, returning the count
    as the canonical envelope payload instead of writing directly to Notion.
    """
    target_date = normalize_target_date(target_date)
    cfg = load_substack_config()
    stats_url = cfg.get("stats_audience_url")
    if not stats_url:
        raise ScrapeError("Missing config.substack.stats_audience_url")
    logger.info("🚀 Substack fetch_profile — date=%s url=%s", target_date, stats_url)

    with SubstackSession(cfg) as s:
        try:
            s.goto_with_login_check(stats_url)
        except LoginRequiredError as err:
            raise ScrapeError(f"Substack login required: {err}") from err

        try:
            s.page.get_by_text(re.compile(r"Total followers\s*\(", re.IGNORECASE)).first.wait_for(
                state="visible", timeout=20000
            )
        except Exception as err:
            s.screenshot_failure(f"{target_date}-substack-no-total-followers")
            raise ScrapeError(f"'Total followers (…)' never appeared on {stats_url}: {err}") from err

        candidates = s.page.get_by_text(re.compile(r"Total followers\s*\(", re.IGNORECASE)).all()
        for el in candidates:
            try:
                text = el.inner_text(timeout=2000) or ""
            except Exception:
                continue
            m = _TOTAL_FOLLOWERS_RE.search(text)
            if not m:
                continue
            count = parse_int(m.group(1))
            if count is not None:
                logger.info("✅ Substack followers: %d", count)
                return {"num_followers": count}

        s.screenshot_failure(f"{target_date}-substack-total-followers-unparsed")
        raise ScrapeError("Could not parse 'Total followers (N)' from Substack stats page.")


def _get_handle() -> str:
    cfg = load_substack_config()
    handle = cfg.get("handle")
    if not handle:
        raise ScrapeError("Missing config.substack.handle")
    return handle


def _collect_note_codes(page, handle: str, skip_first: bool = True) -> list[str]:
    """Walk the @profile feed and return up to MAX_POSTS unique note codes.

    The first unique code is the newsletter teaser (per the user's
    instruction) and is dropped when ``skip_first=True``.
    """
    codes: list[str] = []
    seen: set[str] = set()
    skipped_first = False
    anchors = page.locator(f"a[href*='/@{handle}/note/']")
    n = anchors.count()
    for i in range(n):
        if len(codes) >= MAX_POSTS:
            break
        try:
            href = anchors.nth(i).get_attribute("href") or ""
        except Exception:
            continue
        m = _NOTE_PATH_RE.match(href)
        if not m:
            continue
        code = m.group(2)
        if code in seen:
            continue
        seen.add(code)
        if skip_first and not skipped_first:
            skipped_first = True
            logger.debug("⏭️ Substack: skipping first note %s (newsletter teaser).", code)
            continue
        codes.append(code)
    return codes


def _scrape_note_permalink(page, handle: str, code: str) -> Optional[dict]:
    """Open a note's permalink and read its date + engagement bar.

    A note permalink no longer renders a single note — it renders a *feed*
    (the subject note plus recommended/related notes), each with its own
    engagement toolbar. So a page-global ``button[aria-label="Like"].first``
    grabbed whichever note happened to render first (constant across
    permalinks → identical, wrong counts for every note), and the old
    ``<time datetime>`` element is gone entirely (→ ``posted_at`` was always
    ``None``, which dropped every record downstream — issue #77).

    We pin the subject note via its own timestamp anchor
    (``<a title="May 30, 2026, 6:03 AM" href="…/note/<code>">``), walk up to
    the smallest ancestor that owns the note's ``Like`` button, and read the
    date + engagement scoped to that container.
    """
    permalink = f"https://substack.com/@{handle}/note/{code}"
    try:
        page.goto(permalink, timeout=30000, wait_until="domcontentloaded")
    except Exception as err:
        logger.warning("⚠️ Substack goto %s failed: %s", permalink, err)
        return None
    page.wait_for_timeout(2000)

    try:
        data = page.evaluate(
            r"""(code) => {
                const anchor = document.querySelector(`a[title][href*='/note/${code}']`);
                if (!anchor) return null;
                // Smallest ancestor that owns this note's Like button.
                let node = anchor, container = null;
                while (node && node !== document.body) {
                    if (node.querySelector && node.querySelector("button[aria-label='Like']")) {
                        container = node;
                        break;
                    }
                    node = node.parentElement;
                }
                const read = (role) => {
                    if (!container) return null;
                    const b = container.querySelector(`button[aria-label='${role}']`);
                    return b ? (b.textContent || '').trim() : null;
                };
                return {
                    title: anchor.getAttribute('title'),
                    like: read('Like'),
                    comment: read('Comment'),
                    restack: read('Restack'),
                    video: container ? !!container.querySelector('video') : false,
                };
            }""",
            code,
        )
    except Exception as err:
        logger.warning("⚠️ Substack note %s extract failed: %s", code, err)
        return None

    if not data:
        logger.warning("⚠️ Substack note %s not found in permalink feed.", code)
        return None

    posted_at = human_date_to_iso_date(data.get("title") or "")
    likes = parse_int(data.get("like"))
    comments = parse_int(data.get("comment"))
    reshares = parse_int(data.get("restack"))

    return {
        "post_id": permalink,
        "posted_at": posted_at,
        "is_video": 1 if data.get("video") else 0,
        "num_likes": likes if likes is not None else 0,
        "num_comments": comments if comments is not None else 0,
        "num_reshares": reshares if reshares is not None else 0,
    }


def fetch_posts(target_date: Optional[str] = None) -> Optional[dict]:
    """Walk the feed for note URLs, then visit each permalink for engagement.

    Same permalink-walk strategy as the Threads scraper — one note per page
    eliminates the multi-toolbar feed-view ambiguity.
    """
    target_date = normalize_target_date(target_date)
    handle = _get_handle()
    url = f"https://substack.com/@{handle}"
    cfg = load_substack_config()
    logger.info("🚀 Substack fetch_posts — date=%s feed=%s", target_date, url)
    with SubstackSession(cfg) as s:
        try:
            s.goto_with_login_check(url)
        except LoginRequiredError as err:
            raise ScrapeError(f"Substack login required: {err}") from err

        try:
            s.page.wait_for_selector("a[href*='/note/c-']", timeout=20000)
        except Exception as err:
            s.screenshot_failure(f"{target_date}-substack-no-notes")
            raise ScrapeError(f"No note links appeared on {url}: {err}") from err

        for _ in range(SCROLLS):
            s.page.mouse.wheel(0, 4000)
            s.page.wait_for_timeout(SCROLL_PAUSE_MS)

        codes = _collect_note_codes(s.page, handle, skip_first=True)
        if not codes:
            s.screenshot_failure(f"{target_date}-substack-no-notes-collected")
            raise ScrapeError("Substack: no note codes after skipping the newsletter teaser.")
        logger.info("ℹ️ Substack note codes collected: %d", len(codes))

        posts: list[dict] = []
        for code in codes:
            rec = _scrape_note_permalink(s.page, handle, code)
            if rec is None:
                continue
            posts.append(rec)
            logger.debug(
                "📌 Substack %s — likes=%d comments=%d reshares=%d posted_at=%s video=%d",
                code, rec["num_likes"], rec["num_comments"], rec["num_reshares"],
                rec["posted_at"], rec["is_video"],
            )

        if not posts:
            s.screenshot_failure(f"{target_date}-substack-no-permalinks-scraped")
            raise ScrapeError("Substack: collected note codes but all permalinks failed to scrape.")

        logger.info("✅ Substack notes scraped: %d", len(posts))
        return {"posts": posts}
