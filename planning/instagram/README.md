# Instagram (Meta planner) Scheduling Automation

Plays back the weekly Instagram + Facebook scheduling routine via Playwright + real Chrome. Reads upcoming days from the Notion editorial database, mirrors the IG plan onto Threads / Twitter / Substack columns of the same DB, then drives Meta's native content-calendar planner at `business.facebook.com/latest/content_calendar` to schedule one **Story** (10:00) and one **Feed Post** (15:00) per day. Sunday-thread days schedule a 10-image carousel post; everything else is a single image.

**This is a planner, not a bot.** No likes, comments, follows, or DMs are automated. The script only places the user's own pre-written, already-illustrated content into Meta's own scheduler.

---

## Workflow

```mermaid
flowchart TD
    Mark([weekly setup in Notion:<br/>user ticks <b>Work in Progress IG</b><br/>on each day, fills <b>illustration IG</b>/<b>text IG</b>;<br/>on Sundays user ticks <b>thread IG</b> + sets <b>post IG</b> relation])
    Clone[<b>run:</b><br/>python -m planning.instagram.clone_to_other_platforms<br/>--week-start YYYY-MM-DD --live]
    CloneDo[For each WIP-IG day:<br/>• non-thread: copy illustration IG + text IG + repost IG<br/>• thread: derive first illustration from <b>post IG</b>,<br/>  derive canonical caption via <b>publishIG</b><br/>• write to TW/TH/SB columns (no WIP for SB)<br/>• back-fill Sunday IG row: illustration IG + template text IG]
    Run[<b>run:</b><br/>python -m planning.instagram.schedule_instagram_posts<br/>--week-start YYYY-MM-DD --live]
    Filter{For each day,<br/>WIP IG = true?}
    Skip[skip<br/>days not in scope]
    Resolve[Resolve story = 1 image,<br/>post = 1 image OR 10 images<br/>caption = text IG]
    Browser[<b>Launch real Chrome</b> via Playwright<br/>persistent profile, no automation banners]
    Story[Hover day cell → <b>Schedule ▾</b> →<br/><b>Schedule story</b> → upload 1 image →<br/>FB+IG default-checked at 10:00 → Save]
    Post[Hover day cell → <b>Schedule ▾</b> →<br/><b>Schedule post</b> → upload N images →<br/>type caption → toggle Set date and time on →<br/>set date + 15:00 → Schedule]
    Both{Story AND post<br/>both succeeded?}
    Untick[Set <b>Work in Progress IG = false</b><br/>on the editorial row]
    Fail[Save failure screenshot to<br/>results/instagram/]
    Done([✅ scheduled on<br/>the row's own date,<br/>Europe/Madrid])

    Mark --> Clone
    Clone --> CloneDo
    CloneDo --> Run
    Run --> Filter
    Filter -->|no| Skip
    Filter -->|yes| Resolve
    Resolve --> Browser
    Browser --> Story
    Story --> Post
    Post --> Both
    Both -->|no| Fail
    Both -->|yes| Untick
    Untick --> Done
```

---

## Setup (one-time)

```powershell
& .\.venv\Scripts\python.exe -m planning.instagram.bootstrap_session
```

A real-Chrome window opens at `business.facebook.com/`. Log in manually (both Facebook and the connected Instagram business account must be accessible), then return to the terminal and press **Enter**. The session is persisted under `instagram/chrome_user_data/` (gitignored; **separate** from your normal Chrome profile and every other platform's profile).

The bootstrap uses the same stealth flags as every other platform (see `config/chrome_launch.py`) — no automation infobar, `navigator.webdriver` is undefined.

---

## Daily / weekly use

The clone now runs automatically as **step 0** of `planning_pipeline.py` (see issue #32), so the per-script invocations below are only needed for ad-hoc / single-step runs. Pass `--skip-clone` to the orchestrator to opt out.

```powershell
# 1) Clone IG plan to TW/TH/SB columns (Notion-only; safe to run repeatedly).
#    Skip if you're running planning_pipeline.py — it does this automatically.
& .\.venv\Scripts\python.exe -m planning.instagram.clone_to_other_platforms --week-start 2026-05-25 --dry-run
& .\.venv\Scripts\python.exe -m planning.instagram.clone_to_other_platforms --week-start 2026-05-25 --live

# 2) Drive the Meta planner for IG (story + post per day)
& .\.venv\Scripts\python.exe -m planning.instagram.schedule_instagram_posts                        # default = dry-run, next Monday
& .\.venv\Scripts\python.exe -m planning.instagram.schedule_instagram_posts --week-start 2026-05-25 --dry-run
& .\.venv\Scripts\python.exe -m planning.instagram.schedule_instagram_posts --week-start 2026-05-25 --live
& .\.venv\Scripts\python.exe -m planning.instagram.schedule_instagram_posts --date 20260518 --live  # single-day testing
& .\.venv\Scripts\python.exe -m planning.instagram.schedule_instagram_posts --date 20260518 --live --force  # ignore link IG
```

| flag | meaning |
|------|---------|
| `--week-start YYYY-MM-DD` | Monday of the target week. Default: next Monday from today. |
| `--date YYYYMMDD` (or `YYYY-MM-DD`) | Single-day mode; overrides `--week-start`. |
| `--dry-run` / `--live` | `--dry-run` walks the flow up to Save/Schedule, screenshots, then cancels. `--live` actually submits. Default is `--dry-run` (set via `instagram.dry_run_default` in `config.json`). |
| `--force` | Schedule even if the editorial row's `link IG` column is already populated (post script) or the target platform row already has data (clone script). |
| `--debug` | Verbose logging. |

Screenshots, success/failure artifacts, and per-row dry-run images land in `results/instagram/`.

---

## What gets cloned, what gets scheduled

**Cloning to TW / TH / SB** runs for each day where `Work in Progress IG = true`:

- Non-thread days: the IG row already has everything; the script copies `illustration IG → illustration <P>`, `text IG → text <P>`, `repost IG → repost <P>`.
- Sunday thread days (`thread IG` = true): the script follows the `post IG` relation, reads its `illustration` multi-relation, takes the **first** illustration, and uses **its** canonical first-publication caption (`publishIG` → earliest editorial row's `text IG`; same rule LinkedIn uses) for TW/TH/SB. The IG row is also back-filled: `illustration IG ← first thread image`, `text IG ← instagram.sunday_template_text`.
- Threads + Twitter: also get `Work in Progress <P> = true`. Substack does **not** get its WIP set — Substack is posted day-by-day by `substack/daily_pipeline.py`, and a WIP tick here would confuse that flow.
- `thread <P>` is never replicated. Only IG has thread-posts.
- Idempotency: target platforms whose row already has illustration OR a non-empty caption are skipped unless `--force`.

**Scheduling on Meta** runs for each day where `Work in Progress IG = true`:

- **Story at 10:00** (Europe/Madrid): one image (the day's first illustration), Facebook + Instagram both default-checked.
- **Feed Post at 15:00**: one image on regular days, 10 images on Sunday-thread days. Caption = the row's `text IG`.

Days with nothing checked are silently skipped. The presence of `article LI` (a LinkedIn-article day) no longer suppresses IG — the IG and LI sides are scheduled independently off their own `Work in Progress <P>` flags.

If a Sunday carousel is missing one or more illustration files on disk, the missing entries are logged and skipped and the carousel goes up with the survivors (IG accepts 2 – 10 images per post). If fewer than 2 survive, the day fails honestly.

### Idempotency — running twice is safe

Two layers, mirroring LinkedIn:

1. The Notion query filter is `Work in Progress IG = true` — only ticked rows are even considered.
2. After **both** the story and the post succeed live for a day, the script writes `Work in Progress IG = false` on that editorial row. Partial failures (story OK / post FAIL or vice versa) leave the tick on so the next run retries the day.

The `link IG` column is also checked (rows where it's populated are skipped unless `--force`), but that URL is harvested by the existing notion_update pipeline only **after** Meta publishes the post — so the WIP-IG untick is the primary idempotency guard at scheduling time.

---

## Notion fields used

All field names come from `config/config.json` `instagram.editorial_columns` / `illustration_columns` / `posts_columns` and `clone_ig_to_others.targets` — never hardcoded in the script.

**Editorial DB (`ee23dec3...`) — IG side:**

| role | column | type | purpose |
|------|--------|------|---------|
| `title_day` | `day` | title | Row's day in `YYYYMMDD`. |
| `wip_checkbox` | `Work in Progress IG` | checkbox | The scope marker; auto-unticked after live schedule. |
| `illustration_rel` | `illustration IG` | relation | Source illustration (single). Back-filled on Sunday from `post IG`. |
| `post_url` | `link IG` | url | Idempotency check; not written by this script. |
| `caption_text` | `text IG` | rich_text | The per-day caption typed into the post composer. |
| `repost_checkbox` | `repost IG` | checkbox | Replicated to TW/TH/SB by the clone step. |
| `thread_checkbox` | `thread IG` | checkbox | True on Sunday-carousel days. |
| `post_rel` | `post IG` | relation | Sunday only — points to the posts-DB page holding the 10-image carousel. |

**Per-target maps (`clone_ig_to_others.targets`):**

| target | wip | illustration | text | repost | set_wip |
|--------|-----|--------------|------|--------|---------|
| TW | `Work in Progress TW` | `illustration TW` | `text TW` | `repost TW` | true |
| TH | `work in progress TH` | `illustration TH` | `text TH` | `repost TH` | true |
| SB | `work in progress SB` | `illustration SB` | `text SB` | `repost SB` | **false** |

**Illustrations DB (`f7008956…`):** same shape as LinkedIn — `illustration` (title) for the filename stem, `ALT text`, `publishIG` (the relation we follow to find the canonical first-publication caption), `text IG to copy` (formula fallback).

**Posts DB (`960d4044…`):** `illustration` is the multi-relation that lists every illustration in a thread post. The order Notion returns matches user insertion order, so `[0]` is the deterministic "first" image.

### Why the Sunday TW/TH/SB caption goes through `publishIG`, not the template

The IG-side Sunday caption is a static template ("Ten visuals on personal development, management and leadership. What is your favourite? 🎨.") because Instagram is showing a 10-image carousel and the per-image caption would be confusing. But on TW / TH / SB the same Sunday slot is a **single** image — the FIRST one from the thread — and that image deserves its own original caption. The clone step follows `publishIG` from that first illustration back to the earliest editorial row that ever posted it, and copies that row's `text IG`. Same rule LinkedIn uses for every day.

---

## How the browser session is hardened against bot detection

Single source of truth: `config/chrome_launch.py`. Every per-platform module (`substack/`, `linkedin/`, `instagram/`, future `twitter/` / `threads/`) imports `stealth_launch_kwargs` and `STEALTH_INIT_SCRIPT` from there. No automation tells are inlined elsewhere.

See `linkedin/README.md` § "How the browser session is hardened against bot detection" for the full rationale.

---

## Files in this package

| file | what it does |
|------|--------------|
| `bootstrap_session.py` | One-time interactive login. Opens real Chrome at Meta's sign-in page; after you log in, pressing Enter saves the session to the persistent profile. |
| `instagram_session.py` | `InstagramSession` context manager: launches the persistent-profile real-Chrome session and exposes `page` + a `screenshot_failure()` helper. Equivalent to `linkedin/linkedin_session.py`. |
| `clone_to_other_platforms.py` | Notion-only step: replicate IG plan to TW/TH/SB columns. Independent of Playwright. |
| `schedule_instagram_posts.py` | The Meta planner driver. Queries Notion, resolves images + captions, drives the calendar UI for story + post, writes back the WIP-IG untick. |
| `chrome_user_data/` | (auto-created, gitignored) The dedicated Chrome profile. |
| `README.md` | This file. |

Cross-module dependencies:

- `notion/editorial.py` — shared Notion helper used by every platform.
- `notion/notion_update.py` — for `prepare_notion_update` (handles relation/rich_text/checkbox payloads) and `format_database_id`.
- `config/chrome_launch.py` — shared stealth launch flags.
- `config/logger_config.py` — shared logger setup.

---

## Selectors used in the Meta planner UI

Meta's class names are obfuscated and rotate — never anchor on classes. These role/text/input selectors are the ones validated against the live UI during the first end-to-end run.

> **Centralised labels:** the user-facing accessible names and the locale `EN | ES` media-attach unions live in [`instagram_labels.py`](instagram_labels.py) — `NOT_NOW_BTN_RE`, `CLOSE_BTN_RE`, `DISCARD_LEAVE_BTN_RE`, `SET_DATE_TIME_TEXT_RE`, `SCHEDULE_TEXT_RE`, `NEXT_MONTH_BTN_RE`, `NEXT_WEEK_BTN_RE`, `PREV_WEEK_BTN_RE`, `ADD_MEDIA_BTN_SELECTOR`, `UPLOAD_FROM_COMPUTER_SELECTOR`, plus the `day_cell_label()` / `fmt_time_12h()` rendering helpers. When Meta relabels a control or an account flips locale, extend the alternation there — do **not** re-inline it at the call site. Argument-built name regexes (`Schedule post` / `Schedule story`, action buttons, time slots) and structural CSS/JS probes stay inline in the driver.

| step | selector |
|------|----------|
| Dismiss 'Get Meta Verified' upsell | `page.get_by_role("button", name=/^not now$/i)` — fires on first navigation only |
| Navigate to target week | `page.get_by_role("button", name=/^(next week\|right)$/i)` — Meta uses chevron-glyph text `Left`/`Right` (NOT aria-label) |
| Find day-cell column | JS walk: find the text node `"Mon 18"`, climb until an ancestor contains a button whose text starts with `"Schedule"` — that ancestor IS the column. (Static CSS selectors fail because Meta's column wrapper isn't a stable tag/role.) |
| Reveal Schedule dropdown | `column.hover()` then `column.query_selector_all('div[role="button"], button')` — pick the last whose innerText starts with "Schedule" |
| Pick menu item | `page.get_by_role("menuitem", name=/^Schedule story$/i)` (fallback: `get_by_text("Schedule story")`) |
| Dismiss initial Schedule sub-modal | JS: find heading `"Schedule story"`/`"Schedule post"`, climb to ancestor containing a `Cancel` button, click it. Page-wide `get_by_role("button", "Cancel").first` hits the wrong Cancel (main composer also has one). |
| Add photo/video | `_upload_files` — three-leg strategy in `schedule_instagram_posts.py`. Leg 0: pre-mounted `input[type=file]` (rare but cheap to probe). Leg 1: settle 1500 ms then `page.expect_file_chooser()` around a Playwright click on the EN+ES button regex; on miss, re-resolve the locator and retry once via a JS-native `el.click()` (sidesteps Meta's hydration race on the post composer — see issue #28). Leg 2: intermediate "Upload from computer" dialog if one appeared. Leg 3: any attached `input[type=file]` (8 s wait). Final failure dumps a debug screenshot + the visible buttons in the latest dialog. |
| Upload files | `file_chooser.set_files([path1, path2, …])` — supports multi-image carousel in one call |
| Caption (post composer) | `page.locator('textarea, [role="textbox"][contenteditable="true"], div[contenteditable="true"]')` — fill or keyboard.type |
| 'Set date and time' toggle | `page.locator('input[type="checkbox"][aria-label="Set date and time"]')` — `cb.click(force=True)` then verify `input_value() == "true"` |
| Date input | `page.locator('input[placeholder="mm/dd/yyyy"]')` — fill with `MM/DD/YYYY`. Story has 2 (FB + IG), post has 1 (after toggle). |
| Time inputs (split) | Three inputs per slot: `input[aria-label="hours"]` (1–12), `input[aria-label="minutes"]` (00–59), `input[aria-label="meridiem"]` (`AM`/`PM`). Type into each via select-all + delete + type. |
| Story primary action | `page.get_by_role("button", name=/^schedule$/i).last` |
| Post primary action | `page.get_by_role("button", name=/^schedule$/i).last` |
| Success signal | URL leaves `/composer/...` — `_wait_composer_closes` polls `page.url` until `/content_calendar` is back |

---

## Gotchas

- **The 'Get Meta Verified' upsell modal blocks every click on first navigation.** A `data-surface=GeoIllustrationModal` overlay intercepts pointer events. The script clicks `Not now` after page load (and again between days, just in case).
- **The calendar planner defaults to the current week.** To schedule for a day in a later week, click the `Right` button (chevron-glyph text, not aria-label "Next week") until the target day's column is rendered. Same for past weeks via `Left`.
- **Each `Schedule story` / `Schedule post` opens a SUB-modal first** with default date/time pre-set to "next active time" (typically not your target). Cancel it and use the main composer's date/time inputs instead, which are deterministic.
- **Both the sub-modal AND the main composer have a `Cancel` button.** A page-wide `get_by_role("button", "Cancel").first` may click the main composer's Cancel and discard everything. Always scope Cancel to within the sub-modal's heading ancestor.
- **Meta opens its file picker on click — there's no addressable `input[type=file]` in advance.** Use `page.expect_file_chooser()` around the `Add photo/video` click and feed the files via `file_chooser.set_files([...])`.
- **The post composer's `Add photo/video` button is sometimes inert immediately after the sub-modal closes** (issue #28). A Playwright `.click()` registers but no FileChooser fires and no input attaches — story composer's identical-looking button works fine in the same session. `_upload_files` settles 1500 ms after the sub-modal, then if leg 1 (Playwright click + `expect_file_chooser`) returns no chooser, re-resolves the locator and retries with a JS-native `el.click()`. The JS-click sidesteps the half-bound React handler. Don't drop the settle delay; don't drop the retry.
- **Date is a single `<input placeholder="mm/dd/yyyy">`. Time is THREE inputs**: `aria-label=hours`, `aria-label=minutes`, `aria-label=meridiem`. The story composer surfaces 2 date+time sets (FB + IG, both must be set); the post composer surfaces 1.
- **The post composer hides its date/time row until 'Set date and time' is toggled on.** The toggle is an `<input type="checkbox">` whose `value` reads `"false"` until clicked. Click via `force=True` (real click event fires React onChange).
- **Set-all-visible-date-time uses `_fill_input` (Control+A → Delete → type) rather than `.fill()`.** Meta's React onChange handler ignores values set via `.fill()`'s synthetic event in some builds.
- **Composer opens at a separate URL (`/composer/...`)** — between days the script navigates back to `feed_url` (the content_calendar) and re-dismisses the upsell modal.
- **WIP IG is unticked only when BOTH the story AND the post succeed for the day.** Partial failures keep WIP set so the next run retries the same day cleanly. If a day's story succeeded but the post failed mid-run, a re-run will re-schedule a duplicate story — delete the duplicate manually in Meta's planner before the rerun, or accept it.
- **Notion relation order is preserved by the API.** "First thread illustration" = `post IG → illustration → [0]`. If Meta or Notion ever shuffle relations, fall back to sorting by `created_time` or by `publishIG`-implied first publish date.
- **Repost days have empty `text IG` by design** (issue #32). The canonical caption lives on the illustration's earliest `publishIG` row's `text IG` field (with the `text IG to copy` formula as last-ditch fallback). The clone resolves this via `_canonical_caption_from_publish_ig` in `clone_to_other_platforms.py` — same helper the LinkedIn scheduler and the Sunday-thread branch use. The derived caption is also back-filled to the IG row's own `text IG` so a manual reader of the editorial DB sees real text instead of an empty cell. Never error on "non-thread day but text IG is empty" — fall through to the publishIG derivation first.
- **The video upload path racily double-attaches** (issue #37). Verified live: when uploading a .mp4 via Leg 1 (`page.expect_file_chooser` around the `Add photo/video` click), Meta's planner sometimes processes the file twice — same blob URL ends up bound to two adjacent tiles. The video-specific Leg 0 fast-path (`input[type=file][accept*="video"]`, `is_video=True`) reduces exposure when the input is mounted, but the composer often opens with **zero** `input[type=file]` elements (verified), so Leg 1 is unavoidable. Defense in depth: (1) after upload, `_count_composer_media(page)` counts visible `<div role="button">` whose `innerText` matches `^(remove\|delete\|eliminar\|quitar)\s+(video\|photo\|...)\b/i` — one per attached tile, verified accurate against the real planner (do NOT use `<video>` element count, the right-side Instagram Feed preview pane mounts a `<video>` of its own); (2) if count > expected, `_delete_extra_media_tiles(page, target=N)` clicks the trash button LIFO until count drops to target (no confirmation popover — clicks delete immediately); (3) `_assert_composer_media_count(page, expected=N)` then enforces a hard match; (4) `_check_meta_video_error_toast(page)` reads any `role=alert/status` carrying "more than 1 minute" / "only post one video" / "video is too long" and raises so the FAIL detail names the Meta-side cause.
- **`_upload_files` logs to the `instagram_schedule` logger, not the orchestrator's own.** During a `planning.videos` run, `schedule_videos_posts.main()` must include `"instagram_schedule"` in its `configure_logger` loop or the `📤 Leg N` DEBUG lines, media-count probe, and toast-detector output all vanish — leaving you blind precisely when an IG-side regression happens.

---

## Replication template for other platforms

`substack/` and `linkedin/` already exist; `instagram/` joins them. `twitter/` and `threads/` need the same shape:

```
<platform>/
├── __init__.py
├── README.md
├── bootstrap_session.py
├── <platform>_session.py
├── schedule_<platform>_posts.py
└── chrome_user_data/        (gitignored)
```

Each new platform must:

- Import `stealth_launch_kwargs` + `STEALTH_INIT_SCRIPT` from `config/chrome_launch.py`. Never inline launch args.
- Use `notion/editorial.py` for all Notion access.
- Add a `<platform>` block to `config/config.json` with the platform-specific URLs and `editorial_columns` + `illustration_columns` role maps.
- Add `<platform>/chrome_user_data/` to `.gitignore`.

The IG-first canonical-caption rule applies across the board: LinkedIn, Twitter, Threads, Substack and Instagram (single-image days) all reuse the canonical first-publication caption read via `publishIG`. Adjust per-platform only what differs (filter field, schedule UI flow).
