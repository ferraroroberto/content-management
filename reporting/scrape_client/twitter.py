"""Twitter/X Playwright scraper — fetches profile + posts via the logged-in browser.

Reuses ``planning/twitter`` persistent profile via ``TwitterSession``.

Profile page: ``https://x.com/<handle>``
  → exact follower count from the tooltip that appears when hovering the
    abbreviated "17.5K Followers" link.

Posts page: same URL (the profile's Posts tab is the default landing).
  → up to ~15 own tweets (replies / retweets of others are filtered out by
    requiring the status link to point at the same ``<handle>``), with:
    * ``post_id``     full ``https://x.com/<handle>/status/<id>`` URL.
    * ``posted_at``   decoded from the status id snowflake.
    * ``is_video``    presence of a ``<video>`` element inside the article.
    * ``num_likes``   ``data-testid="like"`` button text.
    * ``num_comments`` ``data-testid="reply"`` button text.
    * ``num_reshares`` ``data-testid="retweet"`` button text.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from planning.twitter.twitter_session import (  # noqa: E402
    LoginRequiredError,
    TwitterSession,
    load_twitter_config,
)
from reporting.scrape_client.base import (  # noqa: E402
    ScrapeError,
    load_full_config,
    normalize_target_date,
    parse_int,
    parse_short_int,
    snowflake_to_iso_date,
)

logger = logging.getLogger("twitter_scrape")

_STATUS_PATH_RE = re.compile(r"^/([^/]+)/status/(\d+)")
MAX_POSTS = 15
SCROLLS = 8
SCROLL_PAUSE_MS = 1500


def _get_handle() -> str:
    cfg = load_full_config()
    handle = cfg.get("twitter_profile", {}).get("querystring", {}).get("username")
    if not handle:
        raise ScrapeError("Missing config.twitter_profile.querystring.username")
    return handle


def fetch_profile(target_date: Optional[str] = None) -> Optional[dict]:
    target_date = normalize_target_date(target_date)
    handle = _get_handle()
    url = f"https://x.com/{handle}"
    cfg = load_twitter_config()
    logger.info("🚀 Twitter fetch_profile — date=%s url=%s", target_date, url)
    with TwitterSession(cfg) as s:
        try:
            s.goto_with_login_check(url)
        except LoginRequiredError as err:
            raise ScrapeError(f"Twitter login required: {err}") from err

        # Match the followers link by text + role rather than by literal href,
        # since X uses the canonical-case form (e.g. ``/FerraroRoberto/...``)
        # in the DOM regardless of the lowercase username we look up.
        followers_link = (
            s.page.get_by_role("link").filter(has_text=re.compile(r"\bFollowers\b", re.IGNORECASE)).first
        )
        try:
            followers_link.wait_for(state="visible", timeout=20000)
        except Exception:
            # Belt-and-braces fallback to href-based selectors, case-insensitive.
            for href_pat in (
                f"a[href$='/verified_followers' i][href*='/{handle}/' i]",
                f"a[href$='/followers' i][href*='/{handle}/' i]",
                "a[href$='/verified_followers']",
                "a[href$='/followers']",
            ):
                cand = s.page.locator(href_pat).first
                try:
                    cand.wait_for(state="visible", timeout=4000)
                    followers_link = cand
                    break
                except Exception:
                    continue
            else:
                s.screenshot_failure(f"{target_date}-twitter-no-followers-link")
                raise ScrapeError(f"Could not find followers link on {url}")

        # Hover to summon the exact-count tooltip.
        try:
            followers_link.hover()
            s.page.wait_for_timeout(1500)
        except Exception as err:
            logger.warning("⚠️ Hover on followers link failed: %s", err)

        # The tooltip text contains the exact number, e.g. "17,527".
        tooltip_text = ""
        for sel in (
            "[role='tooltip']",
            "div[data-testid='HoverCard']",
            "div[id*='popover']",
        ):
            try:
                loc = s.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    tooltip_text = loc.inner_text(timeout=2000) or ""
                    if tooltip_text:
                        break
            except Exception:
                continue

        num = parse_int(tooltip_text) if tooltip_text else None

        # Fallback to the abbreviated link text ("17.5K").
        if num is None:
            try:
                link_text = followers_link.inner_text(timeout=2000) or ""
            except Exception:
                link_text = ""
            num = parse_short_int(link_text.split()[0] if link_text else None)

        if num is None:
            s.screenshot_failure(f"{target_date}-twitter-followers-unparsed")
            raise ScrapeError("Could not parse Twitter follower count (tooltip + fallback both empty).")

        logger.info("✅ Twitter followers: %d", num)
        return {"num_followers": num}


def _extract_count(article, testid: str) -> Optional[int]:
    """Read the integer count next to the engagement button identified by ``data-testid``.

    When the logged-in user has already liked / retweeted their own post, X
    swaps the testid from ``like``→``unlike`` / ``retweet``→``unretweet`` (the
    button now toggles "off" the action). We accept either form. The
    ``aria-label`` on the button always carries the count, e.g.
    ``"15 Likes. Liked"`` or ``"1 repost. Repost"``.
    """
    try:
        btn = article.locator(f"[data-testid='{testid}'], [data-testid='un{testid}']").first
        if btn.count() == 0:
            return None
        aria = btn.get_attribute("aria-label") or ""
        m = re.search(r"([\d][\d,. ]*)", aria)
        if m:
            v = parse_int(m.group(1))
            if v is not None:
                return v
        txt = btn.inner_text(timeout=1500) or ""
        return parse_int(txt) or 0
    except Exception as err:
        logger.debug("Could not read %s count: %s", testid, err)
        return None


def _article_status_url(article, handle: str) -> Optional[tuple[str, str]]:
    """Return ``(post_url, status_id)`` for the tweet article, or None if foreign / no link."""
    try:
        links = article.locator(f"a[href*='/status/']")
        n = links.count()
    except Exception:
        return None
    for i in range(n):
        try:
            href = links.nth(i).get_attribute("href") or ""
        except Exception:
            continue
        m = _STATUS_PATH_RE.match(href)
        if not m:
            continue
        author = m.group(1)
        status_id = m.group(2)
        if author.lower() != handle.lower():
            return None  # this is someone else's tweet (reply/quote thread)
        return f"https://x.com{href.split('?')[0]}", status_id
    return None


def fetch_posts(target_date: Optional[str] = None) -> Optional[dict]:
    target_date = normalize_target_date(target_date)
    handle = _get_handle()
    url = f"https://x.com/{handle}"
    cfg = load_twitter_config()
    logger.info("🚀 Twitter fetch_posts — date=%s url=%s", target_date, url)
    with TwitterSession(cfg) as s:
        try:
            s.goto_with_login_check(url)
        except LoginRequiredError as err:
            raise ScrapeError(f"Twitter login required: {err}") from err

        try:
            s.page.wait_for_selector("article", timeout=20000)
        except Exception as err:
            s.screenshot_failure(f"{target_date}-twitter-no-articles")
            raise ScrapeError(f"No <article> elements appeared on {url}: {err}") from err

        posts: list[dict] = []
        seen_ids: set[str] = set()

        for scroll_iter in range(SCROLLS + 1):
            articles = s.page.locator("article")
            n = articles.count()
            for i in range(n):
                if len(posts) >= MAX_POSTS:
                    break
                art = articles.nth(i)
                resolved = _article_status_url(art, handle)
                if not resolved:
                    continue
                post_url, status_id = resolved
                if status_id in seen_ids:
                    continue
                seen_ids.add(status_id)

                num_likes = _extract_count(art, "like") or 0
                num_comments = _extract_count(art, "reply") or 0
                num_reshares = _extract_count(art, "retweet") or 0
                try:
                    is_video = 1 if art.locator("video").count() > 0 else 0
                except Exception:
                    is_video = 0

                record = {
                    "post_id": post_url,
                    "posted_at": snowflake_to_iso_date(status_id),
                    "is_video": is_video,
                    "num_likes": num_likes,
                    "num_comments": num_comments,
                    "num_reshares": num_reshares,
                }
                posts.append(record)
                logger.debug(
                    "📌 Twitter post %s — posted_at=%s video=%d likes=%d comments=%d reshares=%d",
                    status_id, record["posted_at"], is_video,
                    num_likes, num_comments, num_reshares,
                )

            if len(posts) >= MAX_POSTS:
                break
            s.page.mouse.wheel(0, 4000)
            s.page.wait_for_timeout(SCROLL_PAUSE_MS)

        if not posts:
            s.screenshot_failure(f"{target_date}-twitter-no-own-posts")
            raise ScrapeError("Twitter profile rendered articles but none belonged to the handle.")

        logger.info("✅ Twitter posts scraped: %d", len(posts))
        return {"posts": posts}
