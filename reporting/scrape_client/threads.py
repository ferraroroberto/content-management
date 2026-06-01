"""Threads Playwright scraper — fetches profile + posts via the logged-in browser.

Reuses ``planning/threads`` persistent profile via ``ThreadsSession``.

Profile page: ``https://www.threads.com/@<handle>``
  → exact follower count from the tooltip on the "31K followers" link.

Posts page: same URL (the @profile renders the "Threads" tab by default).
  → up to ~15 own threads with:
    * ``post_id``     full ``https://www.threads.com/@<handle>/post/<code>`` URL.
    * ``posted_at``   from the ``<time datetime>`` attribute on each post.
    * ``is_video``    presence of a ``<video>`` element inside the post container.
    * ``num_likes``   like-button count.
    * ``num_comments`` reply-button count.
    * ``num_reshares`` repost-button count.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from planning.threads.threads_session import (  # noqa: E402
    LoginRequiredError,
    ThreadsSession,
    load_threads_config,
)
from reporting.scrape_client.base import (  # noqa: E402
    ScrapeError,
    load_full_config,
    normalize_target_date,
    parse_int,
    parse_short_int,
    relative_time_to_iso_date,
    to_iso_date,
)

logger = logging.getLogger("threads_scrape")

_POST_PATH_RE = re.compile(r"^/@([^/]+)/post/([^/?#]+)")
MAX_POSTS = 15
SCROLLS = 8
SCROLL_PAUSE_MS = 1500


def _get_handle() -> str:
    cfg = load_full_config()
    handle = cfg.get("threads_profile", {}).get("querystring", {}).get("username")
    if not handle:
        raise ScrapeError("Missing config.threads_profile.querystring.username")
    return handle


def fetch_profile(target_date: Optional[str] = None) -> Optional[dict]:
    """Scrape Threads follower count.

    Threads embeds the exact count in a ``<span title="31,136">31.1K</span>``
    element right next to the abbreviated "31.1K followers" label. No hover
    is required — we read the ``title`` attribute directly (same pattern
    Instagram uses for its own follower count).
    """
    target_date = normalize_target_date(target_date)
    handle = _get_handle()
    url = f"https://www.threads.com/@{handle}"
    cfg = load_threads_config()
    logger.info("🚀 Threads fetch_profile — date=%s url=%s", target_date, url)
    with ThreadsSession(cfg) as s:
        try:
            s.goto_with_login_check(url)
        except LoginRequiredError as err:
            raise ScrapeError(f"Threads login required: {err}") from err

        # Allow a decimal in the abbreviated count — Threads switched the
        # label from "31K followers" to "31.1K followers" once the count
        # crossed into needing one decimal, and the old digits-only matcher
        # stopped matching (→ wait_for timed out → ScrapeError → no file,
        # issue #77). The exact value still lives in the descendant span[title].
        followers_div = s.page.get_by_text(
            re.compile(r"^\d[\d.,]*[KMk]?\s+followers?$", re.IGNORECASE)
        ).first
        try:
            followers_div.wait_for(state="visible", timeout=20000)
        except Exception as err:
            s.screenshot_failure(f"{target_date}-threads-no-followers")
            raise ScrapeError(f"'<n> followers' text never appeared on {url}: {err}") from err

        # The first descendant <span title="..."> inside this div carries
        # the exact follower count.
        try:
            span_with_title = followers_div.locator("span[title]").first
            if span_with_title.count() > 0:
                title_value = span_with_title.get_attribute("title") or ""
                num = parse_int(title_value)
                if num is not None and num > 0:
                    logger.info("✅ Threads followers: %d", num)
                    return {"num_followers": num}
        except Exception as err:
            logger.warning("⚠️ Could not read span[title] inside followers div: %s", err)

        # Fallback: parse the abbreviated label text ("31K followers").
        try:
            link_text = followers_div.inner_text(timeout=2000) or ""
        except Exception:
            link_text = ""
        m = re.search(r"([\d][\d.,KMkm]*)", link_text)
        num = parse_short_int(m.group(1)) if m else None

        if num is None:
            s.screenshot_failure(f"{target_date}-threads-followers-unparsed")
            raise ScrapeError("Could not parse Threads follower count (no span[title], no fallback).")

        logger.warning("⚠️ Threads followers fell back to rounded %d (span[title] missing).", num)
        return {"num_followers": num}


def _collect_post_codes(page, handle: str) -> list[str]:
    """Walk the @profile feed and return up to MAX_POSTS unique post codes."""
    codes: list[str] = []
    seen: set[str] = set()
    anchors = page.locator(f"a[href*='/@{handle}/post/']")
    n = anchors.count()
    for i in range(n):
        if len(codes) >= MAX_POSTS:
            break
        try:
            href = anchors.nth(i).get_attribute("href") or ""
        except Exception:
            continue
        m = _POST_PATH_RE.match(href)
        if not m:
            continue
        code = m.group(2)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def _scrape_permalink(page, handle: str, code: str) -> Optional[dict]:
    """Navigate to a post permalink and read its engagement bar.

    On the permalink page there is exactly **one** post + engagement bar,
    so no per-post container scoping is needed (the feed-view ambiguity
    that gave us 0/0/0 disappears).
    """
    permalink = f"https://www.threads.com/@{handle}/post/{code}"
    try:
        page.goto(permalink, timeout=30000, wait_until="domcontentloaded")
    except Exception as err:
        logger.warning("⚠️ Threads goto %s failed: %s", permalink, err)
        return None
    page.wait_for_timeout(1800)

    # On a Threads permalink, the engagement bar lives inside the main
    # post and each slot is a ``<div role="button">`` containing an
    # ``<svg>`` icon. Each button's ``textContent`` concatenates the
    # aria-label (e.g. ``"Like"`` / ``"Unlike"`` / ``"Reply"`` / ``"Repost"``
    # / ``"Share"``) with the count (e.g. ``"Unlike69"``). The buttons sit
    # in a horizontal row.
    try:
        texts = page.evaluate(
            r"""() => {
                const btns = Array.from(document.querySelectorAll('div[role="button"]'))
                    .filter(b => b.querySelector('svg'));
                // Group by Y coordinate to isolate the engagement bar row.
                const rows = new Map();
                for (const b of btns) {
                    const r = b.getBoundingClientRect();
                    if (r.width === 0) continue;
                    const yKey = Math.round(r.y / 8) * 8;
                    if (!rows.has(yKey)) rows.set(yKey, []);
                    rows.get(yKey).push(b);
                }
                // Find the FIRST row with >=3 buttons whose textContent
                // matches the engagement-bar signature (contains "Like"/"Unlike"
                // and "Repost"). This filters out top-nav rows like
                // ["Back", "Notification setting", "More"].
                let bar = null;
                const sortedYs = Array.from(rows.keys()).sort((a, b) => a - b);
                const ENGAGEMENT_WORDS = /\b(Like|Unlike|Reply|Replies|Repost|Reposted)\b/i;
                for (const y of sortedYs) {
                    const buttons = rows.get(y);
                    if (buttons.length < 3) continue;
                    const concat = buttons.map(b => b.textContent || '').join(' | ');
                    if (ENGAGEMENT_WORDS.test(concat)) { bar = buttons; break; }
                }
                if (!bar) return [];
                bar.sort((a, b) => a.getBoundingClientRect().x - b.getBoundingClientRect().x);
                return bar.slice(0, 4).map(b => (b.textContent || '').trim());
            }"""
        )
    except Exception as err:
        logger.warning("⚠️ Threads engagement JS-extract failed on %s: %s", code, err)
        texts = []

    def _strip_label(s):
        # ``Unlike69`` -> ``69``, ``Reply1`` -> ``1``, ``Share`` -> ``""``.
        m = re.search(r"(\d[\d,. ]*)", s or "")
        return parse_int(m.group(1)) if m else None

    likes = comments = reshares = 0
    if isinstance(texts, list):
        if len(texts) >= 1:
            v = _strip_label(texts[0]); likes = v if v is not None else 0
        if len(texts) >= 2:
            v = _strip_label(texts[1]); comments = v if v is not None else 0
        if len(texts) >= 3:
            v = _strip_label(texts[2]); reshares = v if v is not None else 0

    posted_at = None
    try:
        time_el = page.locator("time").first
        if time_el.count() > 0:
            iso = time_el.get_attribute("datetime")
            if iso:
                posted_at = to_iso_date(iso)
            if posted_at is None:
                txt = time_el.inner_text(timeout=1000) or ""
                posted_at = relative_time_to_iso_date(txt)
    except Exception:
        pass

    try:
        is_video = 1 if page.locator("video").count() > 0 else 0
    except Exception:
        is_video = 0

    return {
        "post_id": permalink,
        "posted_at": posted_at,
        "is_video": is_video,
        "num_likes": likes,
        "num_comments": comments,
        "num_reshares": reshares,
    }


def fetch_posts(target_date: Optional[str] = None) -> Optional[dict]:
    """Walk the feed for post URLs, then visit each permalink for engagement.

    The permalink-walk avoids the feed-view DOM ambiguity (multiple post
    toolbars on the same page) that produced 0/0/0 counts. Cost: ~1.5s per
    post extra navigation, acceptable for a once-daily job.
    """
    target_date = normalize_target_date(target_date)
    handle = _get_handle()
    url = f"https://www.threads.com/@{handle}"
    cfg = load_threads_config()
    logger.info("🚀 Threads fetch_posts — date=%s feed=%s", target_date, url)
    with ThreadsSession(cfg) as s:
        try:
            s.goto_with_login_check(url)
        except LoginRequiredError as err:
            raise ScrapeError(f"Threads login required: {err}") from err

        try:
            s.page.wait_for_selector("a[href*='/post/']", timeout=20000)
        except Exception as err:
            s.screenshot_failure(f"{target_date}-threads-no-post-links")
            raise ScrapeError(f"No /post/ links appeared on {url}: {err}") from err

        # Harvest codes BETWEEN scrolls — Threads virtualizes off-screen
        # posts out of the DOM, so if we scroll first then collect, the
        # NEWEST posts (which were at the top) are gone by the time we look.
        seen: set[str] = set()
        codes: list[str] = []

        def harvest():
            for new_code in _collect_post_codes(s.page, handle):
                if new_code not in seen:
                    seen.add(new_code)
                    codes.append(new_code)

        harvest()  # initial top of feed (newest posts)
        for _ in range(SCROLLS):
            if len(codes) >= MAX_POSTS:
                break
            s.page.mouse.wheel(0, 4000)
            s.page.wait_for_timeout(SCROLL_PAUSE_MS)
            harvest()

        if not codes:
            s.screenshot_failure(f"{target_date}-threads-no-own-posts")
            raise ScrapeError("Threads profile had post links but none matched the handle.")
        logger.info("ℹ️ Threads codes collected from feed: %d", len(codes))

        posts: list[dict] = []
        for code in codes:
            rec = _scrape_permalink(s.page, handle, code)
            if rec is None:
                continue
            posts.append(rec)
            logger.debug(
                "📌 Threads %s — likes=%d comments=%d reshares=%d posted_at=%s video=%d",
                code, rec["num_likes"], rec["num_comments"], rec["num_reshares"],
                rec["posted_at"], rec["is_video"],
            )

        if not posts:
            s.screenshot_failure(f"{target_date}-threads-no-permalinks-scraped")
            raise ScrapeError("Threads: collected post codes but all permalinks failed to scrape.")

        logger.info("✅ Threads posts scraped: %d", len(posts))
        return {"posts": posts}
