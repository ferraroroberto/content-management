"""LinkedIn Playwright scraper — fetches profile + posts via the logged-in browser.

Reuses the existing ``planning/linkedin`` persistent profile via
``LinkedInSession`` so the real-Chrome + stealth contract from
``config/chrome_launch.py`` stays the single source of truth.

Profile page: ``https://www.linkedin.com/in/<handle>/``
  → follower count from the "111,518 followers" line in the bio.

Posts page: ``https://www.linkedin.com/in/<handle>/recent-activity/all/``
  → up to ~15 most-recent posts, with:
    * ``post_id``     canonical post URL built from the URN activity id.
    * ``posted_at``   derived from the URN activity id (``id >> 22`` → ms).
    * ``is_video``    presence of a ``<video>`` element inside the post.
    * ``num_likes``   "You and N others" + 1, or numeric reactions count.
    * ``num_comments`` "N comments".
    * ``num_reshares`` "N reposts".
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent.parent))
from planning.linkedin.linkedin_session import (  # noqa: E402
    LinkedInSession,
    LoginRequiredError,
    load_linkedin_config,
)
from reporting.scrape_client.base import (  # noqa: E402
    ScrapeError,
    linkedin_activity_to_iso_date,
    load_full_config,
    normalize_target_date,
    parse_int,
)

logger = logging.getLogger("linkedin_scrape")

# "111,518 followers" / "1,234 followers".
# IMPORTANT: must require a *separating whitespace* between the digit run and the
# word "followers" so we don't match JSON-LD blobs like
# ``"follower_count":111518`` (no space, no comma).
_FOLLOWERS_RE = re.compile(r"([\d][\d,. ]*)\s+followers?\b", re.IGNORECASE)
# "You and 118 others"; also matches "and 1,234 others"; the leading "You"
# is sometimes absent (LinkedIn collapses it to just the avatar grid).
_REACTIONS_OTHERS_RE = re.compile(r"\band\s+([\d][\d,. ]*)\s+others?\b", re.IGNORECASE)
# Single-named like "Alex Smith and 47 others" — still uses "N others".
# Pure numeric reactions count fallback, e.g. just "1,234".
_COMMENTS_RE = re.compile(r"([\d][\d,. ]*)\s*comments?", re.IGNORECASE)
_REPOSTS_RE = re.compile(r"([\d][\d,. ]*)\s*reposts?", re.IGNORECASE)

# Match URN like ``urn:li:activity:7464533218260680704``.
_URN_ID_RE = re.compile(r"urn:li:activity:(\d+)")

MAX_POSTS = 15
SCROLLS = 6
SCROLL_PAUSE_MS = 1500


def _get_handle_url() -> str:
    """Profile URL — pulled from the existing rapidapi block to avoid duplication."""
    cfg = load_full_config()
    url = cfg.get("linkedin_profile", {}).get("querystring", {}).get("linkedin_url")
    if not url:
        raise ScrapeError("Missing config.linkedin_profile.querystring.linkedin_url")
    return url.rstrip("/")


def _extract_followers(text: str) -> Optional[int]:
    m = _FOLLOWERS_RE.search(text or "")
    return parse_int(m.group(1)) if m else None


def fetch_profile(target_date: Optional[str] = None) -> Optional[dict]:
    """Scrape LinkedIn follower count.

    Important wrinkle: when the logged-in user visits their *own*
    ``/in/<handle>/`` profile, LinkedIn replaces the public bio line
    "111,518 followers · 500+ connections" with a self-analytics widget
    that does NOT show the follower count. The number is reliably visible
    on ``/in/<handle>/recent-activity/all/`` in the left sidebar
    ("Followers   111,516"), so we use that URL instead.
    """
    target_date = normalize_target_date(target_date)
    activity_url = _get_handle_url() + "/recent-activity/all/"
    cfg = load_linkedin_config()
    logger.info("🚀 LinkedIn fetch_profile — date=%s url=%s", target_date, activity_url)
    with LinkedInSession(cfg) as s:
        try:
            s.goto_with_login_check(activity_url)
        except LoginRequiredError as err:
            raise ScrapeError(f"LinkedIn login required: {err}") from err

        try:
            s.page.locator("main").wait_for(state="visible", timeout=20000)
        except Exception as err:
            s.screenshot_failure(f"{target_date}-profile-main-not-visible")
            raise ScrapeError(f"<main> never became visible on {activity_url}: {err}") from err

        # The sidebar renders "Followers" then the count on a separate line.
        # Read the whole main panel text and pull a "Followers\s+<n>" match.
        try:
            main_text = s.page.locator("main").inner_text(timeout=4000) or ""
        except Exception:
            main_text = ""

        # Two patterns to try, in order of robustness:
        #   1. "Followers\n111,516"     — the sidebar layout.
        #   2. "111,518 followers"       — the bio line (visitor view, future-proofing).
        m = re.search(r"\bFollowers\b\s*\n?\s*([\d][\d,. ]*)", main_text, re.IGNORECASE)
        if not m:
            m = _FOLLOWERS_RE.search(main_text)
        if m:
            count = parse_int(m.group(1))
            if count is not None and count > 0:
                logger.info("✅ LinkedIn followers: %d", count)
                return {"num_followers": count}

        # Final fallback: any anchor whose href contains /followers and whose
        # text contains a plausible integer.
        try:
            anchors = s.page.locator("a[href*='/followers']")
            n = min(anchors.count(), 6)
            for i in range(n):
                try:
                    txt = anchors.nth(i).inner_text(timeout=1500) or ""
                except Exception:
                    continue
                v = parse_int(txt)
                if v is not None and v > 100:
                    logger.info("✅ LinkedIn followers (anchor-scan): %d", v)
                    return {"num_followers": v}
        except Exception:
            pass

        s.screenshot_failure(f"{target_date}-profile-followers-not-parsed")
        raise ScrapeError(f"Could not parse follower count from {activity_url}.")


def _scroll_to_load(page, *, scrolls: int = SCROLLS, pause_ms: int = SCROLL_PAUSE_MS) -> None:
    """Scroll the recent-activity feed a few times so more posts hydrate."""
    for _ in range(scrolls):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(pause_ms)


def _extract_post_engagement(post_text: str) -> dict:
    """Pull likes / comments / reshares from a post container's inner text."""
    out = {"num_likes": None, "num_comments": None, "num_reshares": None}

    # likes = "and N others" + 1, OR plain numeric "<N>" near the heart icon.
    m = _REACTIONS_OTHERS_RE.search(post_text)
    if m:
        n = parse_int(m.group(1))
        if n is not None:
            # "You and N others" → N+1; "Alex Smith and N others" → N+1 too
            # (the named person is also a reactor).
            out["num_likes"] = n + 1
    if out["num_likes"] is None:
        # Look for a bare number on a line by itself that's plausibly a reactions count.
        # This is best-effort; the "and N others" pattern is the primary signal.
        pass

    m = _COMMENTS_RE.search(post_text)
    if m:
        out["num_comments"] = parse_int(m.group(1))

    m = _REPOSTS_RE.search(post_text)
    if m:
        out["num_reshares"] = parse_int(m.group(1))

    return out


def _post_is_video(post_locator) -> int:
    """Return 1 if the post container has a ``<video>`` element, else 0."""
    try:
        if post_locator.locator("video").count() > 0:
            return 1
    except Exception:
        pass
    return 0


def fetch_posts(target_date: Optional[str] = None) -> Optional[dict]:
    target_date = normalize_target_date(target_date)
    activity_url = _get_handle_url() + "/recent-activity/all/"
    cfg = load_linkedin_config()
    logger.info("🚀 LinkedIn fetch_posts — date=%s url=%s", target_date, activity_url)
    with LinkedInSession(cfg) as s:
        try:
            s.goto_with_login_check(activity_url)
        except LoginRequiredError as err:
            raise ScrapeError(f"LinkedIn login required: {err}") from err

        try:
            s.page.wait_for_selector("[data-urn^='urn:li:activity:']", timeout=20000)
        except Exception as err:
            s.screenshot_failure(f"{target_date}-activity-no-posts")
            raise ScrapeError(f"No activity posts appeared at {activity_url}: {err}") from err

        _scroll_to_load(s.page)

        post_locator = s.page.locator("[data-urn^='urn:li:activity:']")
        count = post_locator.count()
        logger.info("ℹ️ LinkedIn recent-activity: %d post containers visible", count)
        if count == 0:
            s.screenshot_failure(f"{target_date}-activity-zero-posts")
            raise ScrapeError("LinkedIn recent-activity page rendered no posts.")

        posts: list[dict] = []
        seen_ids: set[str] = set()
        for i in range(count):
            if len(posts) >= MAX_POSTS:
                break
            post = post_locator.nth(i)
            try:
                urn = post.get_attribute("data-urn") or ""
            except Exception:
                continue
            m = _URN_ID_RE.search(urn)
            if not m:
                continue
            activity_id = m.group(1)
            if activity_id in seen_ids:
                continue
            seen_ids.add(activity_id)

            post_url = f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"
            posted_at = linkedin_activity_to_iso_date(activity_id)

            try:
                post_text = post.inner_text(timeout=3000)
            except Exception as err:
                logger.warning("⚠️ Could not read inner_text for activity %s: %s", activity_id, err)
                continue
            eng = _extract_post_engagement(post_text)
            is_video = _post_is_video(post)

            record = {
                "post_id": post_url,
                "posted_at": posted_at,
                "is_video": is_video,
                "num_likes": eng["num_likes"] or 0,
                "num_comments": eng["num_comments"] or 0,
                "num_reshares": eng["num_reshares"] or 0,
            }
            posts.append(record)
            logger.debug(
                "📌 LinkedIn post %s — posted_at=%s video=%d likes=%s comments=%s reshares=%s",
                activity_id, posted_at, is_video,
                record["num_likes"], record["num_comments"], record["num_reshares"],
            )

        if not posts:
            s.screenshot_failure(f"{target_date}-activity-no-parseable")
            raise ScrapeError("LinkedIn recent-activity page had containers but none parsed.")

        logger.info("✅ LinkedIn posts scraped: %d", len(posts))
        return {"posts": posts}
