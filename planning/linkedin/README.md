# LinkedIn Planning Automation

Plays back the weekly LinkedIn-scheduling routine via Playwright + real Chrome. Reads upcoming days from the Notion editorial database, drives LinkedIn's native "Schedule for later" flow, and posts nothing automatically — it only places already-written content into LinkedIn's own scheduler.

**This is a planner, not a bot.** No likes, comments, follows, or DMs are automated. The script only schedules the user's own pre-written, already-illustrated posts.

Three routes are dispatched off each WIP-LI row purely on the editorial relation pattern (no read of the linked post page's `type` property):

| illustration LI | article LI | post LI | newsletter | Route |
|---|---|---|---|---|
| set | empty | any | any | **ILL** — photo + IG-derived caption (original flow) |
| set | set | set | any | **POST** — photo + caption from posts-DB body, with `@mention` resolution |
| empty | empty | set | empty | **CAROUSEL** — Add a document (PDF) + caption from posts-DB body |
| empty | empty | set | set | skip — newsletter is a separate manual process |
| else | | | | skip with log |

---

## Workflow

```mermaid
flowchart TD
    Mark([weekly setup in Notion:<br/>user ticks <b>Work in Progress LI</b><br/>on the editorial rows<br/>they want scheduled])
    Run[<b>run:</b><br/>python -m planning.linkedin.schedule_linkedin_posts<br/>--all-wip --live]
    Classify{Classify route<br/>from relation pattern}
    SkipNL[skip<br/>newsletter rows<br/>or unmatched patterns]

    subgraph ILL ["ILL — photo + IG caption"]
        I1[Follow <b>illustration LI</b> → <b>publishIG</b> →<br/>earliest editorial row's <b>text IG</b>]
        I2[Feed → Photo → upload → ALT → Next →<br/>type caption → Schedule]
    end
    subgraph POST ["POST — photo + post body"]
        P1[Read <b>post LI</b> page body<br/>(code block) →<br/>cache to <b>textLI</b>]
        P2[Photo + ALT same as ILL,<br/>caption typed with<br/><b>@mention typeahead resolution</b>]
        P3{Caption ≤ 3000 chars?}
    end
    subgraph CAR ["CAROUSEL — PDF document"]
        C1[Read <b>post LI</b> body + title]
        C2[Fuzzy-match folder under<br/>thread/books + thread/monographic →<br/>locate single PDF]
        C3[Feed → Start a post → More →<br/>Add a document → upload PDF →<br/>title → Done → caption → Schedule]
        C4[Wait for background<br/>PDF upload to settle]
    end

    Verify{Composer dialog<br/>closed within 20s?}
    Untick[Set <b>Work in Progress LI = false</b><br/>on the editorial row]
    Fail[Save failure screenshot to<br/>results/linkedin/]
    Done([✅ scheduled, Europe/Madrid])

    Mark --> Run --> Classify
    Classify -->|none| SkipNL
    Classify -->|ILL| I1 --> I2
    Classify -->|POST| P3 -->|yes| P1 --> P2
    P3 -->|no| Fail
    Classify -->|CAROUSEL| C1 --> C2 --> C3 --> C4
    I2 --> Verify
    P2 --> Verify
    C4 --> Verify
    Verify -->|no| Fail
    Verify -->|yes| Untick --> Done
```

---

## Setup (one-time)

```powershell
& .\.venv\Scripts\python.exe -m planning.linkedin.bootstrap_session
```

A real-Chrome window opens at LinkedIn's login page. Log in manually, then return to the terminal and press **Enter**. The session is persisted under `linkedin/chrome_user_data/` (gitignored; **separate** from your normal Chrome profile).

The bootstrap uses the same stealth flags as every other run (see `config/chrome_launch.py`) so the login Chrome looks identical to a hand-driven session — no automation infobar, `navigator.webdriver` is undefined.

---

## Daily / weekly use

```powershell
# default = dry-run, default week = next Monday
& .\.venv\Scripts\python.exe -m planning.linkedin.schedule_linkedin_posts

# explicit dry-run for a chosen week
& .\.venv\Scripts\python.exe -m planning.linkedin.schedule_linkedin_posts --week-start 2026-05-25 --dry-run

# go live for the whole week
& .\.venv\Scripts\python.exe -m planning.linkedin.schedule_linkedin_posts --week-start 2026-05-25 --live

# single-day mode (testing)
& .\.venv\Scripts\python.exe -m planning.linkedin.schedule_linkedin_posts --date 20260518 --live

# override the idempotency guard
& .\.venv\Scripts\python.exe -m planning.linkedin.schedule_linkedin_posts --date 20260518 --live --force
```

| flag | meaning |
|------|---------|
| `--week-start YYYY-MM-DD` | Monday of the target week. Default: next Monday from today. |
| `--date YYYYMMDD` (or `YYYY-MM-DD`) | Single-day mode; overrides `--week-start`. |
| `--dry-run` / `--live` | `--dry-run` walks the flow up to the Schedule dialog, screenshots, then cancels. `--live` actually schedules. Default is `--dry-run` (set via `linkedin.dry_run_default` in `config.json`). |
| `--force` | Schedule even if the editorial row's `link LI` column is already populated. |
| `--debug` | Verbose logging. |

Screenshots, success/failure artifacts, and per-row dry-run images land in `results/linkedin/`.

---

## What gets scheduled, what doesn't

A row is **in scope** when `Work in Progress LI = true` and it classifies into one of the three routes (ILL / POST / CAROUSEL — see the table at the top of this README). Rows that don't match any route — for example "article LI only" rows or rows with no relations at all — are silently skipped with a log line.

In-scope rows are scheduled in local time (Europe/Madrid) on the row's own date:

- **Mon-Fri** → 06:30 (`schedule_hour_local` / `schedule_minute_local`)
- **Sat-Sun** → 08:00 (`schedule_weekend_hour_local` / `schedule_weekend_minute_local`)

Days with nothing checked are silently skipped.

### Idempotency — running twice is safe

The script will not double-schedule. Two layers of protection:

1. The Notion query filter is `Work in Progress LI = true` — only ticked rows are even considered.
2. After a successful **live** schedule, the script writes `Work in Progress LI = false` on that editorial row, so a subsequent run no longer matches it in the filter.

In practice: run `--live` twice for the same week and the second run will report "Nothing to do" for every row that was scheduled by the first run. Failed rows keep their tick and will be retried on the next run.

The `link LI` column is also checked (rows where it's populated are skipped unless `--force`), but that URL is harvested by the existing notion_update pipeline only **after** LinkedIn publishes the post — so the WIP-LI untick is the primary idempotency guard at scheduling time.

---

## Notion fields used

All field names come from `config/config.json` `linkedin.editorial_columns`, `linkedin.illustration_columns`, and `linkedin.posts_columns` — never hardcoded in the script.

**Editorial DB (`ee23dec3...`):**

| role | column | type | purpose |
|------|--------|------|---------|
| `title_day` | `day` | title | Row's day in `YYYYMMDD`. |
| `wip_checkbox` | `Work in Progress LI` | checkbox | The scope marker; auto-unticked after live schedule. |
| `illustration_rel` | `illustration LI` | relation | Points to the source illustration row. Used by ILL + POST routes. |
| `article_rel` | `article LI` | relation | Presence promotes ILL → POST route. |
| `post_rel` | `post LI` | relation | Points to the posts-DB page holding the body for POST + CAROUSEL routes. |
| `newsletter_rel` | `newsletter` | relation | Presence disables the CAROUSEL route (newsletter rows are skipped). |
| `post_url` | `link LI` | url | Idempotency check; not written by this script. |
| `caption_text` | `text IG` | rich_text | Per-day IG caption — used as the ILL route's caption source (via the illustration's earliest `publishIG` row). |

**Posts DB (`960d4044...`)** — POST + CAROUSEL routes:

| role | column | type | purpose |
|------|--------|------|---------|
| (page body) | n/a | heading + `code` block sections | A sequence of heading + `code` block pairs. Only the **first `code` block on the page** is used — see "Page body convention" below. |
| `caption_li` | `textLI` | rich_text | Cache of the first-code-block text. Written through on first read (chunked into ≤2000-char segments since Notion's per-segment limit is 2000). Auto-invalidated and re-read if the cached value exceeds LI's 3000-char post limit (i.e. left over from a pre-Phase 2 reader). |

### Page body convention (posts DB)

A LI posts-DB page is organised as a stack of heading + `code` block pairs:

| heading | `code` block contents | scheduler picks it up? |
|---------|-----------------------|:---:|
| `text` | the canonical LI caption you want scheduled | ✅ (first `code` block) |
| `text (old)` | a previous draft kept for reference | ❌ |
| `source` | citation / podcast link / attribution | ❌ |
| `article` etc. | any other helper section | ❌ |

`planning/linkedin/linkedin_posts_body.py::first_code_block_text` walks `blocks.children.list` and returns the text of the **first** `code` block it sees. Anything after that is ignored. This is why the canonical caption must live under the `text` heading at the top of the page — re-ordering the sections changes what gets scheduled.

The shared `reporting.notion.editorial.get_page_body_text` is **not** used here — it concatenates every text-bearing block on the page, which on a posts page yields `text` + `text (old)` + `source` glued together (2-3× the intended caption, easily over LI's 3000-char hard limit). The clips DB uses `get_page_body_text` because clip pages have only one `code` block; posts pages have several, so they need the first-code-block reader.

**Illustrations DB (`f700...`):**

| role | column | type | purpose |
|------|--------|------|---------|
| `image_filename` | `illustration` | title | Image filename (no extension); `.png` is appended. |
| `alt_text` | `ALT text` | rich_text | LinkedIn ALT text for the image. |
| `publish_relation` | `publishIG` | relation | All editorial rows where this illustration has been published. |
| `caption_fallback` | `text IG to copy` | formula | Only used as fallback when `publishIG` is empty (illustration never published before). |

### Why the caption goes through `publishIG`, not `text IG to copy`

The illustration's `text IG to copy` formula concatenates **every** caption ever written for that image across all reuses. For an image published 3 times this yields a multi-version, comma-joined mess. The script instead follows `publishIG` → sorts by day ascending → reads the earliest editorial row's `text IG` rich_text. That's the single canonical first-publication caption.

If an illustration has never been published before (empty `publishIG`), the script falls back to `text IG to copy` — which in that case has nothing to concatenate, so it's safe.

---

## How the browser session is hardened against bot detection

Single source of truth: `config/chrome_launch.py`. Every per-platform module (`substack/`, `linkedin/`, future `twitter/` / `threads/` / `instagram/`) imports `stealth_launch_kwargs` and `STEALTH_INIT_SCRIPT` from there. No automation tells are inlined elsewhere.

What it does:

- **Real Chrome** (`channel="chrome"`), not bundled Chromium. Chromium is fingerprinted instantly by reCAPTCHA / LinkedIn-style anti-bot.
- **Persistent profile** under `linkedin/chrome_user_data/` (separate from your real Chrome). Login cookies survive across runs.
- **`ignore_default_args`**: strips `--enable-automation` (kills the "Chrome is being controlled by automated test software" yellow infobar), `--enable-blink-features=IdleDetection` (another fingerprintable tell), and `--no-sandbox` (kills its own complaint banner).
- **`--disable-blink-features=AutomationControlled`**: lower-level handling of `navigator.webdriver`.
- **`STEALTH_INIT_SCRIPT` via `add_init_script`**: belt-and-braces — explicitly redefines `navigator.webdriver` to `undefined` before any page script runs.
- **`--disable-features=Translate`, `--no-default-browser-check`, `--no-first-run`**: removes the other ambient popups that look like an unattended Chrome.

---

## Files in this package

| file | what it does |
|------|--------------|
| `bootstrap_session.py` | One-time interactive login. Opens real Chrome at LinkedIn's sign-in page; after you log in, pressing Enter saves the session to the persistent profile. |
| `linkedin_session.py` | `LinkedInSession` context manager: launches the persistent-profile real-Chrome session and exposes `page` + a `screenshot_failure()` helper. |
| `linkedin_composer.py` | Shared composer helpers (`fill_caption_with_mentions`, `wait_for_upload_complete`) used by photo + post + carousel + video flows. Lives here (not in `videos/`) because it's an LI-composer concern, not video-only. |
| `linkedin_posts_body.py` | Loads the LI long caption off a posts-DB page (preferring the `textLI` cache, falling back to the page body, write-through chunked into ≤2000-char rich_text segments). Also pre-flights LI's 3000-char post limit. |
| `linkedin_carousel_pdf.py` | Pure fuzzy folder matcher: maps a post title like `LI - failure and success 04` to a PDF under `thread/books` + `thread/monographic` via `SequenceMatcher`. `fuzzy_min_ratio` (0.6) is a confidence threshold, not a gate — the best-scoring folder is **always** used; a below-threshold match is taken anyway with a `WARNING` naming the folder + candidates. Only no-candidates / no-PDF hard-fail. |
| `schedule_linkedin_posts.py` | The scheduler. Queries Notion, classifies each row into ILL / POST / CAROUSEL, drives the appropriate UI, writes back the WIP-LI untick. |
| `chrome_user_data/` | (auto-created, gitignored) The dedicated Chrome profile. |
| `README.md` | This file. |

Cross-module dependencies:

- `reporting/notion/editorial.py` — shared Notion helper (`query_rows_by_filter`, `get_field`, `set_field`, `retrieve_page`, `get_page_body_text` — the last includes `code` blocks).
- `config/chrome_launch.py` — shared stealth launch flags.
- `config/logger_config.py` — shared logger setup.
- `planning/videos/videos_linkedin.py` — re-exports `linkedin_composer` helpers for backward compatibility; otherwise no dependency in either direction.

---

## Selectors used in the LinkedIn UI

LinkedIn's class names are obfuscated (`_6e37ba57`, `_876da3c4`, …) and rotate — never anchor on classes. These role/text-based selectors are the validated ones; full details in the `reference_linkedin_composer_selectors` memory entry.

> **Locale-aware:** every user-facing accessible name below is defined as an EN | ES regex union in [`linkedin_labels.py`](linkedin_labels.py) — `PHOTO_BTN_RE`, `VIDEO_BTN_RE`, `START_POST_RE`, `ALT_TEXT_BTN_RE`, `ADD_BTN_RE`, `MORE_BTN_RE`, `ADD_DOCUMENT_BTN_RE`, `CHOOSE_FILE_BTN_RE`, `DONE_BTN_RE`, `SCHEDULE_POST_BTN_RE`, `NEXT_MONTH_BTN_RE`, `NEXT_BTN_RE`, `FINAL_SCHEDULE_BTN_RE`, `DISCARD_BTN_RE`, plus the calendar / time-picker candidate helpers. When LinkedIn ships a new wording or adds a third language, extend the union in that file — do **not** re-inline a `re.compile` at the call site. The English example shown below is the EN branch only.

| step | selector |
|------|----------|
| Open post + file picker (ILL / POST) | `page.get_by_role("button", name=PHOTO_BTN_RE)` on the feed. The button's accessible name is its `aria-label` (`"Add a photo"`), not its visible text `"Photo"`, so `PHOTO_BTN_RE` anchors on the trailing noun (`(?:^|\s)(?:photo\|foto)$`) to match `Photo` / `Foto` / `Add a photo` / `Añadir … foto` alike |
| Upload image | `input[type="file"]` (first; appears in DOM after "Photo" click) |
| Open ALT dialog | `[role="dialog"] >> get_by_role("button", name=ALT_TEXT_BTN_RE)` |
| ALT textarea | `textarea[placeholder*="describe this image" i], textarea[placeholder*="imagen" i], [role='dialog'] textarea` (first hit wins) |
| Close ALT dialog (Add) | `[role="dialog"] >> get_by_role("button", name=ADD_BTN_RE).last` |
| Editor → composer (Next) | `[role="dialog"] button:has-text("Next"), [role="dialog"] button:has-text("Siguiente")` |
| Caption editor | `div[role="textbox"][contenteditable="true"]` (then `page.keyboard.type(...)`) |
| Open Schedule dialog | `get_by_role("button", name=SCHEDULE_POST_BTN_RE)` |
| Set date | Click `input[name="artdeco-date"]` → advance to the target month by reading the header `h1.artdeco-calendar__month` (e.g. `May 2026` / `mayo de 2026`) and clicking `aria-label="Next month"` until it matches → click the calendar day by `calendar_day_aria_re(target)` (EN `Monday, May 18, 2026.` / ES `lunes, 18 de mayo de 2026.`). The calendar popover is portaled **outside** `[role="dialog"]`, so the header + day cells are matched document-wide, not scoped to the dialog |
| Set time | Click `input[name="timepicker"]` → wait for `.artdeco-typeahead__results-list:not([data-count="0"])` → click `li:has-text(<cand>)` for `<cand>` in `time_picker_candidates(hour, minute)` (EN `6:30 AM` / ES 24h `06:30` / ES 12h `6:30 a. m.`) |
| Schedule sub-dialog → composer | `[role="dialog"] button:has-text("Next"), [role="dialog"] button:has-text("Siguiente")` |
| Final Schedule button | `[role="dialog"] >> get_by_role("button", name=FINAL_SCHEDULE_BTN_RE)` (anchored on `^schedule$` / `^programar$` so it never re-opens the clock-icon's schedule sub-dialog) |
| Success signal | The composer dialog disappears within ~20s |

**CAROUSEL-only — Add a document flow:**

| step | selector |
|------|----------|
| Open empty composer (no Photo) | `page.get_by_role("button", name=START_POST_RE)` (EN `start a post` / `create a post` / ES `empieza una publicación` / `crea una publicación`) |
| Expand secondary actions | `[role="dialog"] >> get_by_role("button", name=MORE_BTN_RE)` |
| Open Share-a-document dialog | `[role="dialog"] >> get_by_role("button", name=ADD_DOCUMENT_BTN_RE)` |
| Push PDF | fast path: `input[type="file"]`; fallback: `page.expect_file_chooser()` + click `CHOOSE_FILE_BTN_RE` |
| Wait for PDF processing | `DONE_BTN_RE` button stays `disabled`/`aria-disabled` while LI processes — poll until clickable |
| Fill document title | `[role="dialog"] input[name*="title" i]` / `[aria-label*="title" i]` / `[aria-label*="título" i]` / `[placeholder*="title" i]` / `[placeholder*="título" i]` / `input[type="text"]` (first hit wins) |
| Close document dialog (Done) | `[role="dialog"] >> get_by_role("button", name=DONE_BTN_RE)` |
| Background upload settle | `planning.linkedin.linkedin_composer.wait_for_upload_complete` — explicit signal fast path + 60s fallback (same as videos) |

---

## Gotchas (learned the hard way)

- **Don't press `Escape`** inside the composer or its sub-dialogs. It bubbles up to the composer and triggers a "Save this post as a draft?" prompt that then blocks every following click. Use direct element interactions (calendar click, typeahead click) instead.
- **`get_by_role("button", name="Schedule")` is dangerous without `exact=True`** — without it, the matcher also hits the small `aria-label="Schedule post"` clock icon, which just re-opens the schedule dialog.
- **The time picker is a typeahead combobox.** Typing "6:30 AM" silently selects whichever option was first highlighted (often 12:30 AM). Always click the matching `<li>` from the dropdown.
- **There is no standalone URL for the scheduled-posts list.** Every `/scheduled-posts/`-style URL 404s. The list is a modal sheet reachable only via the post composer's Schedule dialog → "View all scheduled posts" → Discard the in-progress draft.
- **Carousel "Next" buttons on the feed** behind the modal have `aria-label="Next"` but empty visible text. Scope to `[role="dialog"] button:has-text("Next"), [role="dialog"] button:has-text("Siguiente")` so they don't win the `.first` race.
- **Don't trust a fixed `wait_for_timeout` + screenshot as success confirmation.** The composer briefly stays open while LinkedIn renders the schedule, then unmounts. Wait for the dialog to actually disappear.
- **LinkedIn's account-level UI language wins over `Accept-Language`.** If the connected LI account is set to Spanish (Settings → Account preferences → Site Preferences → Language), every page renders in Spanish no matter what `locale="en-US"` and `--lang=en-US` are doing at the Chrome layer — even mid-session LI can flip back after an action. The selectors in [`linkedin_labels.py`](linkedin_labels.py) cover both languages; if you see a brand-new Spanish wording that doesn't match, extend the alternation there, not at the call site.
- **The schedule calendar popover renders OUTSIDE `[role="dialog"]`.** Its month header is an `<h1 class="artdeco-calendar__month">` (e.g. `May 2026`), not an `h2`/`div`, and it is not a descendant of the composer dialog. Scoping the month-arrival check to `[role="dialog"]` (or matching `h2`/`div`) silently never fires, so the "Next month" loop runs until LinkedIn's ~3-month scheduling limit and overshoots the target month. Read the header document-wide from `h1.artdeco-calendar__month` and click `aria-label="Next month"` until it matches.
- **First feed action of a fresh session needs a longer click timeout.** LinkedIn redirects `/feed/` → `/` for logged-in users and re-renders the share box client-side; the cold-start race used to time the original 10 s `.click()` out (issue #27). The shared constant `linkedin_composer.FEED_ENTRY_CLICK_TIMEOUT_MS` (30 s) is what every feed-entry helper (`_click_add_photo`, `_click_video_button`, `_click_start_a_post`) uses; warm calls still return in ~1 s because `.click()` returns the instant the button is actionable.

---

## Deleting / inspecting a scheduled post (manual recipe)

From a logged-in LinkedIn tab:

1. Click **Photo** in the feed share-box, upload anything (a placeholder image).
2. Click **Next** → type any text.
3. Click the **clock** icon (Schedule post) at the bottom of the composer.
4. In the Schedule dialog, click **View all scheduled posts** (top-left of the dialog).
5. LinkedIn asks "Save this post as a draft?" — click **Discard**.
6. The Scheduled posts sheet opens. Each row has a `...` (actions menu) on the right → Delete post → confirm.

---

## Reading post body text from Notion (the code-block trick)

LinkedIn's long-form captions don't fit into a Notion `rich_text` *property* — they're multi-paragraph, contain emoji, blank lines, and trailing URLs that must survive verbatim. The convention used across this repo's clips and (upcoming) posts databases is:

**The full caption lives inside the Notion page body as a single block of type `code` (language = "plain text").**

Why a code block and not a paragraph?

- Notion's paragraph renderer collapses consecutive whitespace and treats blank lines inconsistently.
- A code block preserves whitespace, emoji, and line breaks **exactly** as typed.
- The block's `rich_text` array always has one segment whose `plain_text` is the entire caption — no concatenation, no transforms.

### How to read it via the Notion API

`reporting.notion.editorial.get_page_body_text(notion, page_id) -> str` walks `blocks.children.list` with pagination and joins the `rich_text` of every block type that exposes one. The relevant set is:

```python
text_block_types = {
    "paragraph", "heading_1", "heading_2", "heading_3",
    "quote", "callout",
    "bulleted_list_item", "numbered_list_item", "to_do", "toggle",
    "code",   # <-- critical for clip / post body captions
}
```

If you forget `code`, the API call comes back with `0 chars` and looks like a Notion API limitation — it isn't. The body text is there; you just filtered it out.

### Caching pattern (clip page → `TextLI` property, posts page → `textLI` property)

The videos orchestrator and the LI posts/carousel routes both read body content on every row resolution. To avoid the per-run cost and to make the caption directly visible in the Notion DB UI, the resolved text is also written into a dedicated `TextLI` (clips DB) / `textLI` (posts DB) rich_text *property*. Logic:

1. Read the cache property — if non-empty and ≤ LI's 3000-char limit, use it (cache hit).
2. Otherwise read the source text and use that.
3. If the source was non-empty, write it back into the cache property so the next read is free.

The two packages use different source readers:
- **Clips DB** (videos): `get_page_body_text` — concatenates every text-bearing block (the clip page has only one `code` block).
- **Posts DB** (LI POST / CAROUSEL): `first_code_block_text` — returns only the first `code` block (the posts page has several; see "Page body convention" above).

The posts DB caption can run close to LI's 3000-char limit, above Notion's 2000-char-per-rich_text-segment limit. `planning/linkedin/linkedin_posts_body.py::_write_rich_text_multi_segment` chunks the body on newline boundaries when possible, then writes a multi-segment payload directly via `notion.pages.update` (bypassing the shared `set_field` which would build a single oversized segment).

**Stale-cache invalidation:** if the cached `textLI` value is over LI's 3000-char limit, it's treated as stale (only an earlier reader that didn't apply the first-code-block rule could have written it). The scheduler logs `🧹 Stale textLI cache ... — invalidating and re-reading body.`, re-reads the first `code` block, and overwrites the cache. No manual cache-clear required.

### Pre-flight: LinkedIn's 3000-char post limit

LinkedIn enforces a hard limit of **3000 characters** for a regular post's text body. The composer disables the final Schedule button when the typed text exceeds this — you'd otherwise discover the failure only after the scheduler has typed thousands of characters into a doomed composer.

`assert_caption_within_linkedin_limit(payload)` runs as the first action of the POST / CAROUSEL routes (after `load_post_payload`) and fails the row with a clear message before opening the LI UI. Resolution: trim the first `code` block in the Notion posts DB; the stale-cache invalidator handles the rest on the next run.

---

## Resolving @mentions in the composer

LinkedIn renders an `@Name` mention as a blue, clickable link to the person's profile. You can't get this by typing the literal `@Hannah Wilson` into the composer — it stays as plain text. You have to drive LinkedIn's mention typeahead: type `@`, type the name letter-by-letter slowly enough for the dropdown to populate (~80ms per char), then click the matching suggestion before continuing.

### Implementation (`planning/linkedin/linkedin_composer.py::fill_caption_with_mentions`)

1. Scan the caption with a Unicode-aware regex: `r"@([^\W\d_]+(?:[ \t]+[^\W\d_]+)*)"`. The letter class `[^\W\d_]` matches **any** Unicode letter, so accented names — `@Mercè Brey`, `@Begoña Núñez` — are captured whole. (The earlier ASCII-only `[A-Z][a-zA-Z]*` stopped at the first accented character, capturing only `Merc` and dumping the tail `è Brey` into the composer as stale literal text beside the resolved chip.) The inter-token separator is **horizontal** whitespace `[ \t]+`, not `\s+`: since `\s` also matches newlines, the old form let a capitalized word after a blank line continue the name (`@Andre Muller\n\nThanks` → `Andre Muller\n\nThanks`, typed verbatim into the composer and mis-resolved — issue #74). Restricting to `[ \t]` keeps a name on one line, so `@Mercè Brey\n\nGreat book` resolves the chip `Mercè Brey` and leaves `\n\nGreat book` as literal text. Because stdlib `re` has no Unicode-uppercase class, the regex matches a greedy run of letter tokens of any case and `_leading_capitalized_run` then trims it to the leading run of *capitalized* tokens — so a lowercase-initial `@` (an email's `@gmail`) is skipped, and `Thanks @John for help` resolves `John`, not `John for help`. Periods/apostrophes/hyphens inside names (`@O'Connor`, `@Jean-Paul`) are still NOT supported — extend the token class if needed.
2. For each match:
   - Type the literal text up to the `@`.
   - Type `@` with a 20ms delay.
   - Type the name with **80ms per character** (LinkedIn's typeahead is async — too fast and the dropdown never appears).
   - Wait for the dropdown via `_click_mention_suggestion(page, name)`, which polls these selector candidates in order:
     ```
     div.mentions-typeahead-content [role="option"]
     div[data-test-id="mentions-typeahead"] [role="option"]
     [aria-label*="mention" i] [role="option"]
     .artdeco-typeahead__results-list li
     [role="listbox"] [role="option"]
     ```
   - Click the first option whose visible text contains the typed name (case-insensitive). If no option in the top 6 matches, click the top-ranked. If no dropdown appears within 6s, log a warning and leave `@<name>` as literal text — **don't fail the row**.
3. Type the tail.

### Verification

Watch the LinkedIn Scheduled posts sheet after a LIVE run. A resolved mention appears in **blue** with a profile link; an unresolved mention shows as black `@Name` text. If you see unresolved mentions in production, either (a) LinkedIn rolled out a new typeahead class, or (b) the network was slow and the 6s wait timed out. The first fix is to add the new selector; the second is to bump the timeout in `_click_mention_suggestion`.

### Quirk: mention chip eats the following space

Committing a mention chip (clicking the typeahead suggestion) **absorbs the immediately-following keystroke when that keystroke is a SPACE**. The visible symptom is "@FirstName LastName" fused with the next word into a single blue chip: `Michelle Kemptonsays`, `Mireia Mujika Irustapodcast`.

`fill_caption_with_mentions` compensates: after a successful chip click it peeks at the next source character and, when it's whitespace (space/tab) or alphanumeric, types one space and advances past the source whitespace if any. Newlines and punctuation pass through untouched — observed against:

| source char after `@Name` | what the user sees in LI before fix | what `fill_caption_with_mentions` does now |
|---|---|---|
| `" "` (space) | `Kemptonsays` (space eaten, fused) | injects a space, advances past the source space |
| alphanumeric (defensive — source missed a space) | `Kemptonsays` | injects a space |
| `"\n"` (newline) | `Wilson\n\nShe shared` (works correctly) | no compensation needed |
| punctuation (`.`, `,`, …) | unverified — assume no fusion | no compensation (avoids `Name .`) |

This applies to the videos LI flow as well, since both flows use the same `fill_caption_with_mentions` in `planning/linkedin/linkedin_composer.py`. Video captions historically had newline-after-mention which is why this bug was invisible there; with the fix in place, a future video caption with a word right after the mention will render correctly.

---

## Waiting for the post-Schedule upload-complete signal

When you click the final Schedule button, LinkedIn **closes the composer immediately** but background media uploads (video uploads for the videos package, document/PDF uploads for the CAROUSEL route) keep running. If your Playwright context tears down before the upload finishes, the scheduled post is created with **no media** and clicking through to it in the Scheduled posts sheet shows *"Something went wrong, please try reloading the page"*. This is invisible to the caller — the `_wait_composer_clears` check passes, the driver returns `LIVE`, and you discover the failure only when you look at LinkedIn the next morning.

### Implementation (`planning/linkedin/linkedin_composer.py::wait_for_upload_complete`)

Order of attempts:

1. **Fast path** — if any of these texts is already visible, return immediately: `upload complete`, `video uploaded`, `successfully scheduled`, `post scheduled`, `your post will be published`.
2. **In-progress poll** — hunt for any of these in-progress indicators:
   ```
   div[aria-label*="upload" i]:not([aria-label*="complete" i])
   div[role="status"]:has-text("Uploading")
   div[role="alert"]:has-text("upload" i)
   div:has-text("don't close")
   div:has-text("Don't close")
   div:has-text("Do not close")
   div:has-text("video is uploading")
   div:has-text("Uploading your video")
   div.global-alert
   [data-test-global-alert-id]
   ```
   If at least one appears, poll until they all disappear (cap 7 minutes), then add a 3-second safety buffer.
3. **Fallback** — if no indicator ever appears (LinkedIn may have already finished or use a selector not on the list), hold the browser open for a fixed **60 seconds** before tearing down.

### Why two layers

Real LinkedIn behavior varies per rollout and per browser instance. Sometimes the toast shows for 30+ seconds; sometimes it never appears at all (small video on a fast uplink). Implementing only the explicit-signal path causes silent failures when the toast is missing; implementing only the fixed hold wastes time on small files. The two-layer approach handles both.

### Diagnosing a future failure

If a row that the driver reported `LI:LIVE` ends up with "Something went wrong" in the Scheduled posts sheet:

1. Open LinkedIn manually, click Photo → upload a small video → Schedule → watch the bottom-left corner.
2. Inspect the upload-progress toast / banner with DevTools. The class names will be obfuscated but `role`, `aria-label`, and the inner text should be stable enough to selectorize.
3. Add the new selector to `_UPLOAD_IN_PROGRESS_SELECTORS` and/or the success text to `_UPLOAD_COMPLETE_TEXT_RE` in `planning/videos/videos_linkedin.py`.
4. If the toast genuinely doesn't show on your account, bump the 60-second fallback to 90/120 seconds.

---

## Replication template for other platforms

`substack/` already exists. `twitter/`, `threads/`, `instagram/` need the same shape:

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
- Add a `<platform>` block to `config/config.json` with the platform-specific URLs and `editorial_columns` + `illustration_columns` role maps (the relevant `Work in Progress <XX>` checkbox, `link <XX>` URL, etc.).
- Add `<platform>/chrome_user_data/` to `.gitignore`.

The IG-first caption rule applies across the board: LinkedIn, Twitter, Threads and Instagram all reuse the canonical first-publication caption read via `publishIG`. Adjust per-platform only what differs (filter field, schedule UI flow).
