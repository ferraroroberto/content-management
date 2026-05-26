"""Instagram Playwright scraper — fetches profile + posts via the logged-in browser.

Reuses ``planning/instagram`` persistent profile via ``InstagramSession``.

Profile page: ``https://www.instagram.com/<handle>/``
  → exact follower count from the tooltip on the "132K followers" link.

Posts page: same URL — the profile renders the post grid. Each tile is an
  ``<a href="/p/<code>/">`` whose hover overlay reads "❤ N  💬 M" (likes +
  comments). For ``posted_at`` and ``is_video`` we visit each permalink and
  read the ``<time datetime>`` attribute / ``<video>`` element presence.

Instagram does not expose a reshare counter for organic posts, so the
``num_reshares`` column is intentionally omitted from the emitted payload
(matching the existing ``instagram_posts`` mapping in ``config/mapping.json``).
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from planning.instagram.instagram_session import (  # noqa: E402
    InstagramSession,
    LoginRequiredError,
    load_instagram_config,
)
from reporting.scrape_client.base import (  # noqa: E402
    ScrapeError,
    load_full_config,
    normalize_target_date,
    parse_int,
    parse_short_int,
    to_iso_date,
)

logger = logging.getLogger("instagram_scrape")

# Instagram now uses handle-prefixed paths for grid tiles, e.g.
# ``/ferraroroberto/p/<code>/``. The canonical public URL is still
# ``/p/<code>/``, so we tolerate both forms.
_POST_PATH_RE = re.compile(r"/(p|reel)/([^/?#]+)")
MAX_POSTS = 12  # Instagram grid loads 12 by default
SCROLLS = 4
SCROLL_PAUSE_MS = 1500


def _get_handle() -> str:
    cfg = load_full_config()
    handle = cfg.get("instagram_profile", {}).get("querystring", {}).get("username")
    if not handle:
        raise ScrapeError("Missing config.instagram_profile.querystring.username")
    return handle


def fetch_profile(target_date: Optional[str] = None) -> Optional[dict]:
    target_date = normalize_target_date(target_date)
    handle = _get_handle()
    url = f"https://www.instagram.com/{handle}/"
    cfg = load_instagram_config()
    logger.info("🚀 Instagram fetch_profile — date=%s url=%s", target_date, url)
    with InstagramSession(cfg) as s:
        try:
            s.goto_with_login_check(url)
        except LoginRequiredError as err:
            raise ScrapeError(f"Instagram login required: {err}") from err

        # The followers metric sits in the header ``<ul>`` triplet
        # (posts / followers / following). It's an <a> linking to /followers/.
        followers_link = s.page.locator(f"a[href='/{handle}/followers/']").first
        if followers_link.count() == 0:
            followers_link = s.page.get_by_text(re.compile(r"\bfollowers?\b", re.IGNORECASE)).first
        try:
            followers_link.wait_for(state="visible", timeout=20000)
        except Exception as err:
            s.screenshot_failure(f"{target_date}-instagram-no-followers")
            raise ScrapeError(f"'followers' element never appeared on {url}: {err}") from err

        # The exact count lives in the link's ``title`` attribute (e.g.
        # ``title="132,368"``) — readable without hover. Hover is still a
        # belt-and-braces fallback because IG occasionally A/B-tests layout.
        title_attr = ""
        try:
            # The exact-count holder is usually a <span title="N"> wrapped by the link.
            span_with_title = followers_link.locator("span[title]").first
            if span_with_title.count() > 0:
                title_attr = span_with_title.get_attribute("title") or ""
        except Exception:
            pass
        if not title_attr:
            try:
                title_attr = followers_link.get_attribute("title") or ""
            except Exception:
                title_attr = ""

        num = parse_int(title_attr) if title_attr else None

        if num is None:
            # Hover-and-tooltip fallback.
            try:
                followers_link.hover()
                s.page.wait_for_timeout(1500)
            except Exception:
                pass
            tooltip_text = ""
            for sel in ("[role='tooltip']", "div[id*='tooltip']", "div[id*='popover']"):
                try:
                    loc = s.page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        tooltip_text = loc.inner_text(timeout=2000) or ""
                        if tooltip_text:
                            break
                except Exception:
                    continue
            num = parse_int(tooltip_text) if tooltip_text else None

        if num is None:
            # Final fallback: parse the abbreviated link text ("132K").
            try:
                txt = followers_link.inner_text(timeout=2000) or ""
            except Exception:
                txt = ""
            m = re.search(r"([\d][\d.,KMkm]*)", txt)
            num = parse_short_int(m.group(1)) if m else None

        if num is None:
            s.screenshot_failure(f"{target_date}-instagram-followers-unparsed")
            raise ScrapeError("Could not parse Instagram follower count.")

        logger.info("✅ Instagram followers: %d", num)
        return {"num_followers": num}


def _collect_grid_tiles(page, handle: str) -> list[tuple[str, str, str]]:
    """Return up to MAX_POSTS unique tiles as ``(code, kind, href)`` tuples.

    ``kind`` is ``"p"`` for ordinary posts / carousels, ``"reel"`` for reels.
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    # ``href*='/p/'`` form catches both ``/p/<code>/`` and the newer
    # handle-prefixed ``/<handle>/p/<code>/`` form IG started using in 2024+.
    anchors = page.locator("a[href*='/p/'], a[href*='/reel/']")
    n = anchors.count()
    for i in range(n):
        if len(out) >= MAX_POSTS:
            break
        try:
            href = anchors.nth(i).get_attribute("href") or ""
        except Exception:
            continue
        m = _POST_PATH_RE.search(href)
        if not m:
            continue
        kind = m.group(1)
        code = m.group(2)
        if code in seen:
            continue
        seen.add(code)
        out.append((code, kind, href))
    return out


def _hover_for_overlay(page, anchor) -> dict:
    """Hover an IG grid tile and read likes + comments off the overlay.

    The overlay typically renders two ``<span>`` items adjacent to a heart icon
    and a speech-bubble icon. We collect numeric tokens from the overlay text
    and assign them in order (likes first, comments second).
    """
    out = {"num_likes": None, "num_comments": None}
    try:
        anchor.scroll_into_view_if_needed(timeout=2000)
        anchor.hover()
        page.wait_for_timeout(700)
        text = anchor.inner_text(timeout=1500) or ""
    except Exception as err:
        logger.debug("hover overlay read failed: %s", err)
        return out
    nums = re.findall(r"([\d][\d,. ]*)", text or "")
    parsed: list[int] = []
    for raw in nums:
        v = parse_int(raw)
        if v is not None:
            parsed.append(v)
    if len(parsed) >= 1:
        out["num_likes"] = parsed[0]
    if len(parsed) >= 2:
        out["num_comments"] = parsed[1]
    return out


def _permalink_time_and_video(page) -> tuple[Optional[str], int]:
    """Read ``<time datetime>`` and detect a ``<video>`` on the post detail."""
    posted_at = None
    try:
        time_el = page.locator("time[datetime]").first
        if time_el.count() > 0:
            iso = time_el.get_attribute("datetime")
            if iso:
                posted_at = to_iso_date(iso)
    except Exception:
        pass
    is_video = 0
    try:
        if page.locator("video").count() > 0:
            is_video = 1
    except Exception:
        pass
    return posted_at, is_video


def fetch_posts(target_date: Optional[str] = None) -> Optional[dict]:
    target_date = normalize_target_date(target_date)
    handle = _get_handle()
    url = f"https://www.instagram.com/{handle}/"
    cfg = load_instagram_config()
    logger.info("🚀 Instagram fetch_posts — date=%s url=%s", target_date, url)
    with InstagramSession(cfg) as s:
        try:
            s.goto_with_login_check(url)
        except LoginRequiredError as err:
            raise ScrapeError(f"Instagram login required: {err}") from err

        # IG lazy-loads tile thumbnails — the anchors are attached to the DOM
        # before the images become visible. We wait on ``state="attached"``
        # (the default state filter ``"visible"`` would time out even when the
        # grid is fully rendered). Modern IG uses handle-prefixed tile hrefs
        # like ``/<handle>/p/<code>/`` so we match on ``href*='/p/'`` rather
        # than ``href^='/p/'``.
        try:
            s.page.wait_for_selector(
                "a[href*='/p/'], a[href*='/reel/']",
                state="attached",
                timeout=20000,
            )
        except Exception as err:
            s.screenshot_failure(f"{target_date}-instagram-no-grid")
            raise ScrapeError(f"No tiles appeared on {url}: {err}") from err

        # Scroll a bit to surface more tiles.
        for _ in range(SCROLLS):
            s.page.mouse.wheel(0, 3000)
            s.page.wait_for_timeout(SCROLL_PAUSE_MS)
            if len(_collect_grid_tiles(s.page, handle)) >= MAX_POSTS:
                break

        tiles = _collect_grid_tiles(s.page, handle)
        if not tiles:
            s.screenshot_failure(f"{target_date}-instagram-zero-tiles")
            raise ScrapeError("Instagram profile rendered the grid but no tiles parsed.")
        logger.info("ℹ️ Instagram tiles collected: %d", len(tiles))

        # Step 1 — hover each tile for the likes/comments overlay (engagement).
        engagement: dict[str, dict] = {}
        for code, kind, href in tiles:
            anchor = s.page.locator(f"a[href='{href}']").first
            if anchor.count() == 0:
                continue
            eng = _hover_for_overlay(s.page, anchor)
            engagement[code] = eng

        # Step 2 — visit each permalink to read <time datetime> + detect video.
        posts: list[dict] = []
        for code, kind, href in tiles:
            permalink = f"https://www.instagram.com{href}"
            try:
                s.page.goto(permalink, timeout=30000, wait_until="domcontentloaded")
                s.page.wait_for_timeout(1000)
            except Exception as err:
                logger.warning("⚠️ Could not open IG permalink %s: %s", permalink, err)
                continue
            posted_at, is_video = _permalink_time_and_video(s.page)
            eng = engagement.get(code, {})
            record = {
                "post_id": permalink,
                "posted_at": posted_at,
                "is_video": is_video if is_video is not None else 0,
                "num_likes": eng.get("num_likes") or 0,
                "num_comments": eng.get("num_comments") or 0,
            }
            posts.append(record)
            logger.debug(
                "📌 Instagram post %s — posted_at=%s video=%d likes=%d comments=%d",
                code, posted_at, record["is_video"], record["num_likes"], record["num_comments"],
            )

        if not posts:
            s.screenshot_failure(f"{target_date}-instagram-no-posts-parsed")
            raise ScrapeError("Instagram tiles found but no permalinks parsed.")

        logger.info("✅ Instagram posts scraped: %d", len(posts))
        return {"posts": posts}
