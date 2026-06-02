# Playwright social-media scraping — durable reference

This is the reference doc for the `reporting/scrape_client/` package — the
Playwright-based alternative to the paid RapidAPI fetcher
(`reporting/social_client/social_api_client.py`). It captures the
per-platform DOM tricks that took the longest to find so a future reader
(or LLM) re-fixing a broken scraper has somewhere to start.

For the *user-facing* "how do I switch" docs see the
"[Choosing the data source](../README.md#choosing-the-data-source--rapidapi-vs-playwright)"
section in the main README. This document is the *implementation* memory.

## Architecture in one paragraph

Every endpoint key in `config/config.json`
(`linkedin_profile`, `linkedin_posts`, …, `substack_posts`) carries a
`"source": "rapidapi" | "playwright"` field. `social_api_client.get_api_data`
inspects it and either makes the HTTP call (RapidAPI) or imports
`reporting.scrape_client.<platform>` and calls `fetch_<profile|posts>(date)`.
The scraper returns just the `data` payload dict — the outer envelope
(`{date, platform, data_type, data}`) is added by the existing
`save_results`. Downstream, `data_processor` reads the JSON file and
applies the matching mapping in `config/mapping.json`; if the RapidAPI
shape mapping fails, it auto-falls-back to `<key>_playwright` via the
pre-existing `get_alternative_mapping_keys` helper. **No
`data_processor` change was needed** for the dual path to work.

## Browser launch — never re-inline stealth

Every scraper module imports the platform's existing session class
(`planning/<platform>/<platform>_session.py`) which itself uses
`config/chrome_launch.py::stealth_launch_kwargs()` + `STEALTH_INIT_SCRIPT`.
That is the single source of truth for stealth Chrome flags and re-
inlining them anywhere else is explicitly rejected — the docstring in
`chrome_launch.py` calls it out, and the global "browser automation must
not look like a bot" rule in `~/.claude/CLAUDE.md` reinforces it.

## Per-platform DOM cheat sheet

### LinkedIn

* **Follower count:** scraped from `https://www.linkedin.com/in/<handle>/recent-activity/all/`, **not** from `https://www.linkedin.com/in/<handle>/`. The own-profile view replaces the public bio's "111,518 followers · 500+ connections" line with an analytics widget that *omits* the count. The recent-activity left sidebar renders "Followers   111,516" cleanly. Regex `r"\bFollowers\b\s*\n?\s*([\d][\d,. ]*)"` against the `<main>` inner text.
* **Regex on the bio line is dangerous:** `r"followers"` (no anchor) ALSO matches hidden `<code id="bpr-guid-…">{"…"follower_count":111518…}</code>` JSON-LD blobs. Always require a leading digit + whitespace: `r"\d[\d,. ]*\s+followers?\b"`.
* **Posts:** `[data-urn^='urn:li:activity:']` containers on `/recent-activity/all/`. The activity ID encodes the timestamp: `(int(activity_id) >> 22) // 1000` is the Unix epoch second (LinkedIn uses the Unix epoch, not a Twitter-style 2010 epoch).
* **Engagement:** post inner-text contains `"You and 118 others"` (likes = N+1), `"8 comments"`, `"3 reposts"`. Regex `\band\s+(\d[\d,. ]*)\s+others?\b` covers both `"You and N others"` and `"Alex Smith and N others"`.
* **Video detection:** presence of a `<video>` element inside the post container. The user-facing "Edit captions" menu item on the three-dots only appears for video posts but requires a menu click — `<video>` is non-invasive.

### Twitter / X

* **Follower count:** exact value is in a tooltip that appears when you hover the `verified_followers` (or `followers`) link. Match the link by `role + text` (`get_by_role("link").filter(has_text=re.compile(r"\bFollowers\b"))`) — **never** by literal href, because X's DOM uses the canonical-case `/FerraroRoberto/...` regardless of the lowercase username in config. Hover, wait ~1.5s, read `[role='tooltip']`. Falls back to `parse_short_int("17.5K")` from the link text.
* **Like-button testid swap:** when the logged-in user has already liked their own tweet, X swaps `data-testid="like"` to `data-testid="unlike"` (toggle semantics). Same for `retweet` → `unretweet`. Always match both: `[data-testid='like'], [data-testid='unlike']`. Aria-label format is `"15 Likes. Liked"` (count first, then state).
* **Posts:** `<article>` elements. Filter to own tweets by requiring the in-article status link's path to start with `/<handle>/` (case-insensitive) — otherwise replies / quoted-tweets-of-others get included. Status ID is a Twitter snowflake (epoch 2010-11-04, `id >> 22 + 1288834974657 = ms_since_unix_epoch`).
* **Reply count:** `data-testid="reply"` button (no `unreply` variant).
* **Video detection:** `<video>` element inside the article.

### Instagram

* **Follower count:** exact value is in `<span title="132,368">132K</span>` on the profile header — same `title`-attribute trick Threads uses. **No hover needed.** Hover fallback exists for layout A/B tests.
* **Handle-prefixed tile hrefs:** IG started using `/<handle>/p/<code>/` for grid tile anchors (not the bare `/p/<code>/` the public permalink uses). Selector must be `a[href*='/p/'], a[href*='/reel/']` — `^='/p/'` misses the new form. Regex extraction must use `.search()` not `.match()` on the href.
* **Lazy-load gotcha:** anchors are *attached* to the DOM before the tile images become visible. `wait_for_selector` must use `state="attached"` (default `"visible"` times out even when the grid is fully rendered).
* **Engagement:** hover each tile in the grid to reveal a likes+comments overlay; the overlay's inner_text contains two numeric tokens in order (likes, comments). IG has no organic-reshare counter — `num_reshares` is intentionally omitted from the payload (matches the legacy `instagram_posts` mapping).
* **Posted_at + video detection:** not visible on the grid — visit each permalink, read `<time datetime>` for the date and check `<video>` element presence. Costs ~1.5s per tile but reliable.

### Threads

* **Follower count:** exact value lives in `<span title="31,080">31K</span>` *inside* the "31K followers" container. Find the parent text via `get_by_text(re.compile(r"^\d+[KMk]?\s+followers?$"))`, then `descendant span[title]` and read the title attribute. **No hover needed** (this is the same pattern Instagram uses for its own counter — discovered after several rounds of trying to make `hover()` work). Hover-based tooltip detection was a dead end: `Locator.hover()` does add ~6 DOM nodes (the tooltip) but the count text never surfaces in any `[role='tooltip']` / popover selector.
* **Posts — permalink walk, not feed parse:** Threads' feed view renders every post's engagement bar in the same scroll container, and the engagement counts use unlabeled `<div role="button">` slots (no `aria-label="Like"`) — robustly scoping each anchor to its own bar is brittle. **Solution:** scrape post codes from the feed, then `goto()` each permalink. On the permalink there is exactly one engagement bar at the bottom of the only post.
* **DOM virtualization:** Threads aggressively unmounts off-screen posts as you scroll past them. If you scroll N times *then* collect codes, the newest posts are gone. **Harvest codes between scrolls**, accumulating into a `seen` set.
* **Permalink engagement structure:** four `div[role="button"]` siblings in a horizontal row near the bottom of the main post. Each button's `textContent` concatenates the aria-label (e.g. `"Like"` / `"Unlike"` / `"Reply"` / `"Repost"` / `"Share"`) with the count (e.g. `"Unlike69"`, `"Reply1"`, `"Repost3"`, `"Share"`). Parse with `re.search(r"\d[\d,. ]*", textContent)`. The top-navigation bar `["Back", "Notification setting", "More"]` is *also* a row of 3 role=button SVG-bearing divs, so the row picker must filter by an engagement-word signature (`/Like|Unlike|Reply|Repost/`) to avoid grabbing the wrong row.
* **Posted_at:** `<time datetime="2026-05-25T...">` on the permalink page.
* **Video detection:** `<video>` element on the permalink page.

### Substack

* **Follower count = "Total followers", not "subscribers":** these are two distinct metrics on Substack. RapidAPI returned `subscriberCountNumber` (mailing list, ~9.8K). The Playwright scraper returns the "Total followers (N)" line from `stats_audience_url` (~14.6K) — that's the metric the legacy `planning/substack/update_substack_followers.py` always reported. The fold-in keeps the legacy semantic; if you ever need the subscriber-list count again, that's a separate field/mapping. The relevant config key is `substack.stats_audience_url` (per-publication URL — not the same as the public profile URL).
* **Posts — skip only a true teaser, not the first note (issue #84):** we *used* to drop the first unique `c-<id>` code unconditionally as "the newsletter teaser". That was wrong for the daily-Note workflow — the scrape (pipeline step 1) runs *before* the day's Note is published (step 6), so the top entry is *yesterday's* real note, which is exactly the row the posts consolidator needs (`posted_at = date - 1 day`). Dropping it left every `*_substack_no_video` column NULL → blank Notion fields. Now we keep all notes and drop a note **only** when it embeds a `/p/` newsletter post preview (`<a href*='/p/'>` inside the note container) — the signal a genuine announcement/restack note carries and ordinary daily notes never do.
* **Posts — permalink walk:** same reason as Threads. The feed view crams multiple notes' engagement bars into the page; scoping each anchor to its own toolbar via ancestor xpath is brittle. Visit each permalink for a clean read.
* **Permalink engagement structure — `text_content()` vs `inner_text()`:** each engagement slot is a `<button aria-label="Like|Comment|Restack|Share">` containing the icon SVG and the count nested in a child element. **Playwright's `Locator.inner_text()` returns empty** because Substack styles the count container in a way that excludes it from the CSS-visible-text accounting. `Locator.text_content()` returns the DOM text content (including the count) — use that. This was the single longest dead-end of the integration; the diagnostic that revealed it is at `tmp/debug_sb_btn.py` if you need to re-verify.
* **Feed view DOM is different from permalink view:** on the feed, each engagement icon's count is a *next-sibling* `<button>` (not nested inside). The current code uses the permalink-walk for posts, so the feed structure is no longer load-bearing — but if you ever switch back to feed parsing, remember the layouts diverge.

## Pre-existing bug worth knowing about

`reporting/social_client/social_api_client.py::save_results` originally
had a duplicate file-existence check at the top that silently dropped
freshly-fetched data when a file already existed on disk — even with
`--no-skip`. The `process_all_endpoints` loop's own skip-existing logic
already handles the day-already-collected case upstream, so the inner
check was redundant *and* broken. It was removed as part of the
Playwright integration but predates it; any forward debugging of "I
re-ran with `--no-skip` and the JSON didn't change" should not point at
the new code.

## Validation pattern

When iterating on a new scraper, the fastest loop is:

1. Edit the scraper.
2. Delete the old JSON: `Remove-Item reporting\results\raw\<key>_<date>.json`.
3. Re-run that one endpoint:
   `python -m reporting.social_client.social_api_client --no-skip --platform <key> --debug`
4. Inspect the saved JSON and compare to a known-good baseline (the
   RapidAPI shape mapping for the same date is fine for a diff).
5. When a selector misses, the scraper saves a screenshot to
   `results/<platform>/<timestamp>-<date>-<reason>.png`. Open that PNG;
   the failure is usually obvious (wrong page state, login expired,
   layout A/B test).

For full-pipeline validation, run `python -m reporting.process.data_processor`
standalone — it processes every JSON file in `results/raw/`, falls back
to `_playwright` mappings as needed, and emits per-DataFrame counts so
you can spot any endpoint that produced zero records.
