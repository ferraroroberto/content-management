"""Scrape comments from my own LinkedIn posts into Supabase.

Pipeline:
1. Read editorial DB rows where `date >= today - (days-1)` AND `link LI` is
   set — i.e. a window of N total calendar days including today.
2. For each post URL, open in the planning/linkedin chrome_user_data session.
3. Click "Load more comments" until exhausted, expand replies.
4. Extract per-comment: commenter URL, display name, text, posted_at.
5. Upsert into `comments` + `commenters` tables.

Selectors for the comment DOM are unvalidated on first run — every
extraction step is wrapped in try/except and logged so we can iterate
live without losing the run. The scraper is idempotent: re-running on the
same posts upserts.

Reuses `planning/linkedin/chrome_user_data` — no separate bootstrap.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(REPO_ROOT))

from config.console import force_utf8_stdio  # noqa: E402
from config.logger_config import setup_logger  # noqa: E402
from engagement.db.client import (  # noqa: E402
    load_config,
    mark_replied_as_ignored,
    migrate_fallback_ids_to_urn,
    upsert_commenters,
    upsert_comments,
)
from planning.linkedin.linkedin_session import (  # noqa: E402
    LinkedInSession,
    LoginRequiredError,
    load_linkedin_config,
)
from reporting.notion.editorial import (  # noqa: E402
    get_field,
    init_notion_client,
    query_rows_by_filter,
)

logger = logging.getLogger("engagement.linkedin.scrape")


# ---------- Notion side: pull my recent post URLs ----------

def _yyyymmdd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


def fetch_recent_li_posts(days: int) -> list[dict]:
    """Return [{post_url, posted_at, day_yyyymmdd}, ...] for the last `days`
    calendar days **including today** where `link LI` is set. `days=1` means
    just today; `days=5` means today + the four prior days."""
    li_cfg = load_linkedin_config()
    notion = init_notion_client(load_config()["notion"]["api_token"])
    db_id = li_cfg["editorial_db_id"]
    columns = li_cfg["editorial_columns"]
    today = datetime.now(timezone.utc).date()
    earliest = today - timedelta(days=max(0, days - 1))

    title_col = columns["title_day"]
    link_col = columns["post_url"]
    date_col = columns["date"]

    filter_obj = {
        "and": [
            {"property": link_col, "rich_text": {"is_not_empty": True}},
            {"property": date_col, "date": {"on_or_after": earliest.isoformat()}},
        ]
    }
    rows = query_rows_by_filter(notion, db_id, filter_obj)
    out: list[dict] = []
    for row in rows:
        url = get_field(row, "post_url", columns)
        if not url:
            continue
        date_val = row.get("properties", {}).get(date_col, {}).get("date") or {}
        posted_at = date_val.get("start")
        title = get_field(row, "title_day", columns)
        out.append(
            {
                "post_url": str(url).strip(),
                "post_posted_at": posted_at,
                "day": title or "",
            }
        )
    logger.info("📰 found %d LI posts in last %d day(s) including today", len(out), days)
    return out


# ---------- LinkedIn DOM scraping ----------

# LinkedIn migrated to a componentized framework — CSS class names are obfuscated
# and shuffle between deploys (same trap as the composer; see memory note
# `reference_linkedin_composer_selectors`). The stable hooks are:
#
#   - comment list container: data-testid ends with "FeedType_FEED_DETAIL"
#   - comment text:           data-testid="expandable-text-box"
#   - profile links:          <a href="https://www.linkedin.com/in/<handle>/">
#
# Extraction runs as a single page.evaluate() so we don't fight per-element
# Playwright locator chains against an obfuscated DOM.
SEL_COMMENT_LIST_CONTAINER = "[data-testid$='FeedType_FEED_DETAIL']"
SEL_COMMENT_TEXT_NODES = "[data-testid='expandable-text-box']"

SEL_LOAD_MORE = [
    "button:has-text('Load more comments')",
    "button:has-text('Show more comments')",
    "button:has-text('Show previous comments')",
    "button.comments-comments-list__load-more-comments-button",
]
SEL_LOAD_REPLIES = [
    "button:has-text('See previous replies')",
    "button:has-text('Show previous replies')",
    "button:has-text('more replies')",
    "button:has-text('more reply')",
]
SEL_TIME = ["time", ".comments-comment-meta__data"]

# Sort dropdown — switch from "Most relevant" → "Most recent" so we don't
# miss recently-arrived comments below the relevance threshold.
SEL_SORT_TRIGGER = [
    "button:has-text('Most relevant')",
    "[role='button']:has-text('Most relevant')",
]
SEL_SORT_RECENT_OPTION = [
    "div[role='menuitem']:has-text('Most recent')",
    "button:has-text('Most recent')",
    "li:has-text('Most recent')",
]


# JS extractor — runs in the page context, walks the DOM by stable hooks
# (data-testid + href patterns), tolerates the obfuscated class soup. Returns
# one record per comment/reply. Author replies (mine) are filtered out here.
_JS_EXTRACT_COMMENTS = r"""
(args) => {
    const listSel = args.listSel;
    const textSel = args.textSel;
    const myHandle = (args.myHandle || '').toLowerCase();

    // LinkedIn renders multiple `data-testid$="FeedType_FEED_DETAIL"` regions
    // on a post page (main thread + recommended-posts rail). Walk all of them
    // and merge — dedup later by (account_url, text-prefix).
    const lists = Array.from(document.querySelectorAll(listSel));
    if (!lists.length) return { error: 'no_list_container', records: [] };

    // Walk up to the SMALLEST ancestor that:
    //   - contains exactly ONE text-box (this comment, not a sibling), AND
    //   - contains a profile link that PRECEDES the text-box in DOM order.
    //
    // The preceding-order check matters because @mentions inside the comment
    // text are themselves <a href="/in/..."> links — without it, my own
    // replies like "thanks Abhishek" got attributed to Abhishek (the mention)
    // instead of me (the avatar above). The header link is always before the
    // text, the mention is always inside it.
    function blockOf(textNode) {
        let node = textNode;
        for (let i = 0; i < 20 && node; i++) {
            const tbs = node.querySelectorAll && node.querySelectorAll(textSel);
            if (tbs && tbs.length === 1) {
                const links = node.querySelectorAll('a[href*="/in/"]');
                for (const lk of links) {
                    const rel = lk.compareDocumentPosition(textNode);
                    if (rel & Node.DOCUMENT_POSITION_FOLLOWING) {
                        return { block: node, link: lk };
                    }
                }
            }
            node = node.parentElement;
        }
        return { block: null, link: null };
    }

    function cleanName(raw) {
        if (!raw) return '';
        // LinkedIn renders a long composite inside the anchor:
        //   "Priya Hunt Verified Profile 1st\nPriya Hunt \n • 1st\n\n<headline>"
        // The clean name is the SECOND line of that block, or the first if there's no badge prefix.
        const lines = raw.split('\n').map(s => s.trim()).filter(Boolean);
        if (lines.length >= 2 && /(Profile|Premium|Verified|1st|2nd|3rd)/.test(lines[0])) {
            return lines[1].replace(/\s+•.*$/, '').trim();
        }
        return lines[0] || '';
    }

    function parseRelative(s) {
        if (!s) return null;
        const m = String(s).trim().toLowerCase().match(/^(\d+)\s*(s|m|h|d|w|mo|y)\b/);
        if (!m) return null;
        const n = parseInt(m[1], 10);
        const unit = m[2];
        const ms = ({ s: 1e3, m: 6e4, h: 3.6e6, d: 8.64e7, w: 6.048e8, mo: 2.592e9, y: 3.1536e10 })[unit];
        return new Date(Date.now() - n * ms).toISOString();
    }

    // Collect every text-box in DOM order, tagging each as mine vs. other.
    const textNodes = [];
    for (const list of lists) {
        for (const tn of list.querySelectorAll(textSel)) textNodes.push(tn);
    }

    function extractOne(tn) {
        const { block, link } = blockOf(tn);
        if (!block || !link) return null;

        const text = (tn.innerText || tn.textContent || '').trim();
        if (!text) return null;

        let href = link.getAttribute('href') || '';
        if (href.startsWith('/')) href = 'https://www.linkedin.com' + href;
        href = href.split('?')[0].replace(/\/$/, '');
        const handle = (href.match(/\/in\/([^/]+)/) || [])[1] || '';

        const badges = Array.from(block.querySelectorAll('p, span, div'))
            .filter(el => (el.innerText || '').trim() === 'Author');
        const isAuthor = badges.length > 0 || (myHandle && handle.toLowerCase() === myHandle.toLowerCase());

        const displayName = cleanName(link.innerText || link.getAttribute('aria-label') || '');

        // Prefer the <time datetime="..."> attribute (exact ISO timestamp LinkedIn
        // embeds for every comment) over the display label ("1d", "2h") which
        // maps multiple comments to the same coarse bucket. Fall back to label
        // parsing only when the attribute is absent (e.g. very old comments
        // rendered without a <time> element).
        let postedAt = null;
        const candidates = block.querySelectorAll('time, span');
        for (const c of candidates) {
            if (c.tagName === 'TIME') {
                const dt = c.getAttribute('datetime');
                if (dt) { postedAt = dt; break; }
            }
            const t = (c.innerText || '').trim();
            if (/^\d+\s*(s|m|h|d|w|mo|y)\b/i.test(t)) {
                postedAt = parseRelative(t);
                break;
            }
        }

        // The LinkedIn URN lives on the OUTERMOST per-comment ancestor as
        // componentkey="replaceableComment_urn:li:comment:(urn:li:ugcPost:NNN,COMMENTID)".
        // That URN lets us build a permalink (...?commentUrn=...) so links go
        // straight to the comment instead of the post. Inner componentkeys are
        // just per-element UUIDs — useless for permalinks.
        let ck = '';
        const urnAncestor = block.closest && block.closest('[componentkey^="replaceableComment_"]');
        if (urnAncestor) {
            ck = urnAncestor.getAttribute('componentkey') || '';
        }
        if (!ck) {
            ck = link.getAttribute('componentkey')
              || (block.closest && block.closest('[componentkey]') && block.closest('[componentkey]').getAttribute('componentkey'))
              || '';
        }

        return {
            account_url: href,
            display_name: displayName,
            text: text,
            posted_at_iso: postedAt,
            componentkey: ck,
            is_author: isAuthor,
        };
    }

    // Build the ordered stream of (mine|other) records, dedup on (url, text-prefix).
    const ordered = [];
    const seenKeys = new Set();
    for (const tn of textNodes) {
        const rec = extractOne(tn);
        if (!rec) continue;
        const dedupKey = (rec.is_author ? 'AUTHOR' : rec.account_url) + '||' + rec.text.slice(0, 200);
        if (seenKeys.has(dedupKey)) continue;
        seenKeys.add(dedupKey);
        ordered.push(rec);
    }

    // Walk the ordered list. Non-author entries become persisted comment rows.
    // Author entries attach as `my_reply_text` to the MOST RECENT non-author
    // record above them. If an author entry has no preceding non-author, drop.
    const out = [];
    let lastOther = null;
    for (const rec of ordered) {
        if (rec.is_author) {
            if (lastOther && !lastOther.my_reply_text) {
                lastOther.my_reply_text = rec.text;
                lastOther.my_replied_at_iso = rec.posted_at_iso;
            }
            continue;
        }
        rec.my_reply_text = null;
        rec.my_replied_at_iso = null;
        out.push(rec);
        lastOther = rec;
    }
    return { error: null, records: out };
}
"""


def _first_locator(scope, selectors: list[str]):
    for sel in selectors:
        loc = scope.locator(sel).first
        try:
            if loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


def _try_switch_to_most_recent(page: Page) -> bool:
    """Best-effort: click the sort dropdown and pick 'Most recent'."""
    for trigger_sel in SEL_SORT_TRIGGER:
        try:
            trigger = page.locator(trigger_sel).first
            if not (trigger.count() and trigger.is_visible()):
                continue
            trigger.scroll_into_view_if_needed()
            trigger.click()
            page.wait_for_timeout(400)
            for opt_sel in SEL_SORT_RECENT_OPTION:
                opt = page.locator(opt_sel).first
                if opt.count() and opt.is_visible():
                    opt.click()
                    page.wait_for_timeout(1200)
                    logger.info("↕️ switched comment sort → Most recent")
                    return True
        except Exception as err:
            logger.debug("sort switch failed on %s: %s", trigger_sel, err)
            continue
    logger.debug("sort switch: no trigger matched (staying on default order)")
    return False


def _click_all(page: Page, selectors: list[str], *, max_clicks: int, settle_ms: int, label: str) -> int:
    """Repeatedly click any visible button matching `selectors` until none remain."""
    clicks = 0
    for _ in range(max_clicks):
        clicked = False
        for sel in selectors:
            try:
                # `all()` so we can fire every visible instance per pass — replies
                # buttons can show up multiple times under different parent comments.
                buttons = page.locator(sel)
                count = buttons.count()
                for i in range(count):
                    btn = buttons.nth(i)
                    try:
                        if btn.is_visible():
                            btn.scroll_into_view_if_needed()
                            btn.click()
                            clicks += 1
                            clicked = True
                            page.wait_for_timeout(settle_ms)
                    except Exception:
                        continue
                if clicked:
                    break
            except Exception as err:  # pragma: no cover
                logger.debug("%s click error on %s: %s", label, sel, err)
        if not clicked:
            break
    if clicks:
        logger.info("🔽 %s — %d click(s)", label, clicks)
    return clicks


def _expand_all_comments(page: Page, *, max_clicks: int = 30, settle_ms: int = 1200) -> int:
    """Expand top-level comments + nested replies until both pools are exhausted."""
    top = _click_all(page, SEL_LOAD_MORE, max_clicks=max_clicks, settle_ms=settle_ms, label="load-more-comments")
    replies = _click_all(page, SEL_LOAD_REPLIES, max_clicks=max_clicks, settle_ms=settle_ms, label="see-previous-replies")
    # Second pass — expanding replies sometimes reveals more "Load more comments" too.
    if replies:
        top += _click_all(page, SEL_LOAD_MORE, max_clicks=max_clicks, settle_ms=settle_ms, label="load-more-comments-pass-2")
    return top + replies


_URN_RE = re.compile(r"urn:li:comment:\(.+?\)")


def _extract_comment_id(component_key: Optional[str], fallback_seed: str) -> str:
    """Prefer the LinkedIn URN from the `replaceableComment_<URN>` componentkey
    (gives us a stable id + lets ui.py build a comment permalink). Fall back to
    a hash so re-scrapes still upsert idempotently when URN can't be found."""
    if component_key:
        # Strip the JS-side prefix if it leaked through, then regex-match the URN.
        ck = component_key.replace("replaceableComment_", "", 1)
        m = _URN_RE.search(ck)
        if m:
            return m.group(0)
    import hashlib
    return "fallback:" + hashlib.sha1(fallback_seed.encode("utf-8", errors="replace")).hexdigest()[:24]


def _my_handle_from_config() -> str:
    """Best-effort: pull the LinkedIn handle from config (used to skip my own replies in the extractor)."""
    cfg = load_config()
    # Common locations the handle shows up under in this repo's config.
    for path in (
        ("linkedin_profile", "querystring", "linkedin_url"),
        ("linkedin_posts", "querystring", "linkedin_url"),
    ):
        node = cfg
        for k in path:
            node = (node or {}).get(k) if isinstance(node, dict) else None
        if isinstance(node, str):
            m = re.search(r"/in/([^/?]+)", node)
            if m:
                return m.group(1)
    return ""


def _dump_debug(page: Page, post_url: str, *, prefix: str) -> None:
    dump_dir = REPO_ROOT / "results" / "engagement" / "debug"
    dump_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", post_url)[-80:]
    stamp = datetime.now().strftime("%H%M%S")
    try:
        (dump_dir / f"{prefix}-{stamp}-{slug}.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(dump_dir / f"{prefix}-{stamp}-{slug}.png"), full_page=True)
        logger.warning("📁 dumped %s-%s-...html + .png", prefix, stamp)
    except Exception as err:
        logger.warning("dump failed: %s", err)


def scrape_post_comments(page: Page, post_url: str, post_posted_at: Optional[str]) -> tuple[list[dict], list[dict]]:
    """Return (comments_rows, commenters_rows) for a single post URL."""
    logger.info("➡️ scraping %s", post_url)
    page.goto(post_url, wait_until="domcontentloaded", timeout=45_000)

    # Comments are lazy-loaded — scroll the bottom of the post into view to
    # trigger the comments-section hydration, then wait for the network to
    # settle before we look for comment articles.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeoutError:
        logger.debug("networkidle wait timed out — proceeding anyway")
    page.wait_for_timeout(1_500)

    # Scroll a few page-heights down — LinkedIn hydrates the comments only
    # once the comment region is close to the viewport.
    for _ in range(4):
        try:
            page.mouse.wheel(0, 1200)
            page.wait_for_timeout(500)
        except Exception:
            break
    # Then jump to the "Comment" action button if present — most reliable anchor.
    for anchor_sel in ("button:has-text('Comment')", "a:has-text('comments')"):
        try:
            anchor = page.locator(anchor_sel).first
            if anchor.count() and anchor.is_visible():
                anchor.scroll_into_view_if_needed()
                page.wait_for_timeout(800)
                break
        except Exception:
            continue
    page.wait_for_timeout(1_500)

    # Wait for the comment-list container before trying to extract.
    try:
        page.wait_for_selector(SEL_COMMENT_LIST_CONTAINER, timeout=10_000)
    except PWTimeoutError:
        logger.warning("⚠️ comment list container not found within 10s on %s", post_url)

    _try_switch_to_most_recent(page)

    # Scroll until expandable-text-box count stabilises. The post body itself
    # is rendered as ONE text-box, so we need count >= 2 before considering
    # the comments loaded (otherwise we exit early on a post-only page).
    # Phase 1: 8 unconditional scrolls to trigger lazy-load. Phase 2: 10 more,
    # exit early on stability with count >= 2.
    for _ in range(8):
        try:
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(600)
        except Exception:
            break
    last_count, stable_passes = -1, 0
    for _ in range(10):
        try:
            count = page.locator(SEL_COMMENT_TEXT_NODES).count()
        except Exception:
            count = 0
        if count >= 2 and count == last_count:
            stable_passes += 1
            if stable_passes >= 2:
                break
        else:
            stable_passes = 0
        last_count = count
        try:
            page.mouse.wheel(0, 1500)
            page.wait_for_timeout(700)
        except Exception:
            break
    logger.info("📊 comment text-boxes on page after scroll: %d", last_count)

    _expand_all_comments(page)

    # Final settle before extraction.
    page.wait_for_timeout(1_000)

    my_handle = _my_handle_from_config()
    result = page.evaluate(
        _JS_EXTRACT_COMMENTS,
        {"listSel": SEL_COMMENT_LIST_CONTAINER, "textSel": SEL_COMMENT_TEXT_NODES, "myHandle": my_handle},
    )

    if result.get("error"):
        logger.warning("⚠️ extractor reported %s on %s — dumping page for inspection", result["error"], post_url)
        _dump_debug(page, post_url, prefix="miss")
        return [], []

    records = result.get("records") or []
    if not records:
        logger.warning("⚠️ 0 comments extracted from %s — dumping page for inspection", post_url)
        _dump_debug(page, post_url, prefix="empty")

    comments_rows: list[dict] = []
    commenters_seen: dict[str, dict] = {}
    for rec in records:
        account_url = rec.get("account_url") or ""
        text = rec.get("text") or ""
        display_name = rec.get("display_name") or ""
        ck = rec.get("componentkey") or ""

        if not account_url and not text:
            continue

        comment_id = _extract_comment_id(
            ck, fallback_seed=f"{post_url}|{account_url}|{text[:80]}"
        )
        comments_rows.append(
            {
                "platform": "linkedin",
                "comment_id": comment_id,
                "post_url": post_url,
                "commenter_url": account_url,
                "display_name": display_name,
                "text": text,
                "posted_at": rec.get("posted_at_iso"),
                "my_reply_text": rec.get("my_reply_text"),
                "my_replied_at": rec.get("my_replied_at_iso"),
            }
        )
        if account_url:
            commenters_seen.setdefault(
                account_url,
                {
                    "platform": "linkedin",
                    "account_url": account_url,
                    "display_name": display_name,
                },
            )

    logger.info("✅ %s — extracted %d comments", post_url, len(comments_rows))
    # Stamp post_posted_at into each row's verdict_reasons-friendly side channel for the classifier.
    for r in comments_rows:
        r["_post_posted_at"] = post_posted_at  # consumed by classifier only; stripped before upsert
    return comments_rows, list(commenters_seen.values())


# ---------- Orchestrator ----------

def run(days: int, *, headless: bool = False, dry_run: bool = False, limit: Optional[int] = None) -> dict:
    li_cfg = load_linkedin_config()
    posts = fetch_recent_li_posts(days)
    if limit:
        posts = posts[:limit]
    if not posts:
        logger.warning("⚠️ no posts to scrape")
        return {"posts": 0, "comments": 0, "commenters": 0}

    all_comments: list[dict] = []
    all_commenters: dict[str, dict] = {}

    with LinkedInSession(li_cfg, headless=headless) as session:
        try:
            session.goto_with_login_check(li_cfg["feed_url"])
        except LoginRequiredError:
            logger.error("❌ LinkedIn session expired — run `python -m planning.linkedin.bootstrap_session`")
            raise

        for p in posts:
            try:
                comments, commenters = scrape_post_comments(session.page, p["post_url"], p["post_posted_at"])
                all_comments.extend(comments)
                for c in commenters:
                    all_commenters.setdefault(c["account_url"], c)
            except Exception as err:
                logger.exception("❌ failed scraping %s: %s", p["post_url"], err)
                session.screenshot_failure(f"scrape-fail-{p.get('day') or 'na'}")

    # Strip side-channel fields before persisting comments.
    persistable_comments = [{k: v for k, v in c.items() if not k.startswith("_")} for c in all_comments]

    if dry_run:
        out_path = REPO_ROOT / "results" / "engagement" / f"dryrun-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {"posts": [p["post_url"] for p in posts], "comments": all_comments, "commenters": list(all_commenters.values())},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("📝 dry-run output → %s", out_path)
    else:
        # Promote legacy fallback:<hash> comment_ids to the new URN so user-set
        # classifications and statuses carry over to the URN-keyed row that
        # the upsert is about to write. Idempotent — only runs where a new URN
        # exists AND a matching fallback row is present.
        migrate_fallback_ids_to_urn(platform="linkedin", new_comments=persistable_comments)
        upsert_commenters(list(all_commenters.values()))
        upsert_comments(persistable_comments)
        # Auto-mark "I already replied" rows as ignored so the triage inbox
        # only contains comments that actually need my attention.
        flipped = mark_replied_as_ignored(platform="linkedin")
        if flipped:
            logger.info("✓ auto-marked %d already-replied row(s) as ignored", flipped)
        # Chain: apply the rule classifier so new comments from whitelist/
        # blacklist commenters get cascaded immediately (no manual click).
        try:
            from engagement.classify.rules import classify_pending  # late import
            res = classify_pending(platform="linkedin")
            logger.info("🧮 post-scrape classify: %s", res)
        except Exception as err:  # never let classify failure mask scrape success
            logger.warning("post-scrape classify failed: %s", err)

    return {"posts": len(posts), "comments": len(persistable_comments), "commenters": len(all_commenters)}


def main() -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Scrape LinkedIn comments on my own posts.")
    parser.add_argument("--days", type=int, default=None, help="lookback window; default from config.engagement.default_days")
    parser.add_argument("--limit", type=int, default=None, help="cap on number of posts to scrape (debug)")
    parser.add_argument("--headless", action="store_true", help="run Chrome headless (default headful)")
    parser.add_argument("--dry-run", action="store_true", help="don't upsert; write a json dump to results/engagement/")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    force_utf8_stdio()
    # Configure the package-root logger so siblings (engagement.db,
    # engagement.classify.*, engagement.reputation) propagate into the same log
    # file. Naming a leaf here leaves them with no configured ancestor, so they
    # fall through to logging.lastResort — stderr-only, WARNING+ (issue #160).
    setup_logger("engagement", level=logging.DEBUG if args.debug else logging.INFO, file_logging=True)
    logging.getLogger().setLevel(logging.DEBUG if args.debug else logging.INFO)

    cfg = load_config().get("engagement", {})
    days = args.days if args.days is not None else cfg.get("default_days", 5)
    result = run(days=days, headless=args.headless, dry_run=args.dry_run, limit=args.limit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
