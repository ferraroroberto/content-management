# 2026-05-17 — Twitter (X) + Threads scheduling automation

Issues: [#10](https://github.com/ferraroroberto/reporting/issues/10) (Twitter),
[#11](https://github.com/ferraroroberto/reporting/issues/11) (Threads),
[#12](https://github.com/ferraroroberto/reporting/issues/12) (Substack — no code).

Ships the 4th and 5th per-platform schedulers (after `linkedin/` #9,
`substack/` already in-tree, `instagram/` #13). Mirror of the IG module
shape. Both consume the IG-clone output written by
`instagram/clone_to_other_platforms.py`. No new Notion columns.

## What was done

1. **Twitter / X scheduler** (`twitter/schedule_twitter_posts.py`) —
   Playwright driver against `https://x.com/home`:
   - Per WIP-TW day, opens the modal composer via the side-rail
     `[data-testid="SideNav_NewTweet_Button"]`, types the caption,
     uploads one image via the pre-mounted
     `input[data-testid="fileInput"]`, clicks the Schedule toolbar icon,
     fills 6 native `<select>` (Month / Day / Year / Hour / Minute /
     AM-PM) by positional index, clicks Confirm, then the final Schedule
     action (`[data-testid="tweetButton"]`).
   - Untick `Work in Progress TW` on success.

2. **Threads scheduler** (`threads/schedule_threads_posts.py`) —
   Playwright driver against `https://www.threads.com/@ferraroroberto`:
   - Per WIP-TH day, opens the `New thread` modal from the inline `What's
     new?` row, types the caption, uploads one image via the pre-mounted
     `input[type=file]` inside the dialog, clicks the top-right 3-dots
     (positional JS — no aria-label), clicks `Schedule…`, navigates the
     calendar popup to the target month, clicks the day-cell via a JS
     walk from the digit's leaf `<span>` to its nearest
     `[role="gridcell"]` ancestor (the only element with the React click
     handler), fills `input[placeholder="hh"]` and
     `input[placeholder="mm"]` (24h, zero-padded), clicks Done, then the
     final Schedule action.
   - Untick `work in progress TH` on success.

3. **Bootstrap + session managers** for both platforms — direct ports of
   the IG equivalents. Login markers per platform (x.com `/i/flow/login`;
   threads.com `/login` or `instagram.com/accounts/login`).

4. **READMEs** (`twitter/README.md`, `threads/README.md`) — mermaid
   workflow, CLI table, validated selectors, gotchas, file layout.

5. **Substack #12 closed manually** with a comment pointing at the
   existing `substack/daily_pipeline.py` package and at #13 (Notion
   replica already shipped via the clone step). No new code.

## Files modified / created

- **New**: `twitter/{__init__.py, bootstrap_session.py, twitter_session.py, schedule_twitter_posts.py, README.md}`
- **New**: `threads/{__init__.py, bootstrap_session.py, threads_session.py, schedule_threads_posts.py, README.md}`
- **Modified**: `config/config.json` — added top-level `twitter` and
  `threads` blocks (URL, profile dir, illustration folder, editorial
  column maps, post time, dry-run default).
- **No change**: `.gitignore` already had both `chrome_user_data/`
  entries from the IG ship.

## Validation

```powershell
# Phase 1 — config + sessions compile
& .\.venv\Scripts\python.exe -m py_compile `
    twitter\bootstrap_session.py twitter\twitter_session.py twitter\__init__.py `
    threads\bootstrap_session.py threads\threads_session.py threads\__init__.py

# Phase 1 HANDOFF — user-run bootstraps (one Chrome window each)
& .\.venv\Scripts\python.exe -m twitter.bootstrap_session
& .\.venv\Scripts\python.exe -m threads.bootstrap_session

# Phase 2 — Twitter live for the full week 2026-05-18 → 2026-05-24
& .\.venv\Scripts\python.exe -m twitter.schedule_twitter_posts --date 20260518 --dry-run --debug
#   → composer + 6-select schedule modal verified ("Will send on Mon, May 18, 2026 at 3:00 PM").
& .\.venv\Scripts\python.exe -m twitter.schedule_twitter_posts --date 20260518 --live --debug
#   → LIVE; WIP-TW unticked.
& .\.venv\Scripts\python.exe -m twitter.schedule_twitter_posts --week-start 2026-05-18 --live --debug
#   → 6/6 remaining days LIVE in one pass; all WIP-TW unticked.

# Phase 3 — Threads live for the full week
& .\.venv\Scripts\python.exe -m threads.schedule_threads_posts --date 20260518 --dry-run --debug
#   → first dry-run: day-click selected today (17) — bug found, fixed.
#   → second dry-run: day-cell highlight visually moves to 18; time 15:00.
& .\.venv\Scripts\python.exe -m threads.schedule_threads_posts --week-start 2026-05-18 --live --debug
#   → 7/7 days LIVE; all WIP-TH unticked.
```

End-to-end outcome (2026-05-17 morning): Twitter clean sweep (7/7 days
LIVE, 0 retries needed). Threads ended with the same 7/7 result on the
second iteration — but the **first** Threads run silently scheduled all
seven posts for **today** (May 17) at 15:00 instead of for May 18-24,
because the day-click was hitting an inert ancestor and the calendar's
selected-day never updated (today was the default). User must delete the
7 mis-scheduled May 17 Threads posts manually. Mid-iteration the
`_click_calendar_day` function was rewritten to walk from the digit's
leaf `<span>` up to its `[role="gridcell"]` ancestor (the actual element
with the React click handler), and to pick the lowest-lightness span
color so that today (white text on black pill = lightness 255) is
excluded.

Failure artifacts land in `results/twitter/` and `results/threads/`.

## Selectors / approaches that DIDN'T work — and what does

### Twitter
1. **The inline `/home` composer carries stale draft text.** Don't try
   to type into `[data-testid="tweetTextarea_0"]` directly on the feed —
   always open the modal via the side-rail button
   `[data-testid="SideNav_NewTweet_Button"]` for a clean slate.

2. **Page-wide `get_by_role("button", name=/^close$/i)` clicks the
   side-rail "Close" nav** and breaks the composer flow. Scope every
   modal dismissal to `[role="dialog"]`.

3. **`select_option(label="May")` flakes on X's Month select.** Anchor
   the 6 Schedule selects by their FIXED ORDER inside the dialog
   (`[role="dialog"] select` positional) and `select_option(value="5")`
   with X's 1-indexed month value.

### Threads
1. **Clicking the day's `<span>` does NOTHING — calendar selection
   doesn't update.** Walk up to the nearest `[role="gridcell"]` ancestor
   and click that.

2. **"Brightest" cell does NOT mean "current month".** Today is white
   text on a dark pill → highest lightness; current-month days are black
   text on white → lowest lightness; prev/next-month days are light grey
   → intermediate. Pick the LOWEST.

3. **`aria-label*="options"` matches the bottom "Reply options" button**
   instead of the top-right 3-dots. The 3-dots has NO aria-label and NO
   test-id — anchor positionally in the dialog header band (y < 80 from
   dialog top, rightmost svg-only button).

4. **"New thread" text appears in the side-nav permanently.** Composer-
   close detection must check for `[role="dialog"]` count == 0, never
   for the text.

All of this is captured in `twitter/README.md` and `threads/README.md`
under § Gotchas for the next platform port.

## Issue closure

- **#10 (Twitter)** — closed via `Closes #10` in the merge commit.
- **#11 (Threads)** — closed via `Closes #11` in the merge commit.
- **#12 (Substack)** — closed manually with a comment pointing at the
  existing `substack/daily_pipeline.py` package and at #13 (Notion
  replica via clone). No code change.
