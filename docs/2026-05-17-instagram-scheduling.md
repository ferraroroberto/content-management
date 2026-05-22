# 2026-05-17 — Instagram (Meta planner) scheduling automation

Issue: [#13](https://github.com/ferraroroberto/reporting/issues/13).

Adds the third per-platform scheduler to the repo, mirroring `linkedin/` (#9) and `substack/` shapes. Same Notion editorial DB, same illustration folder (different sub-folder for IG-vertical format), same `config/chrome_launch.py` stealth.

## What was done

1. **Notion clone step** (`instagram/clone_to_other_platforms.py`) — Notion-only module that mirrors the IG side of the editorial DB to Threads/Twitter/Substack:
   - For non-thread days: straight copy of `illustration IG` / `text IG` / `repost IG` onto the matching `<P>` columns.
   - For Sunday-thread days: derives the first illustration from `post IG → illustration[0]`, then resolves the canonical first-publication caption for that illustration via `publishIG → earliest editorial row's text IG` (same rule the LinkedIn scheduler uses for every day). The IG row itself is back-filled when blank: `illustration IG ← derived first thread image`, `text IG ← instagram.sunday_template_text`.
   - TW and TH get `Work in Progress <P> = true` after clone; SB does not (Substack is posted day-by-day by `substack/daily_pipeline.py`).
   - Idempotency: skips target rows that already have illustration or caption set unless `--force`.

2. **Meta planner driver** (`instagram/schedule_instagram_posts.py`) — Playwright driver against `business.facebook.com/latest/content_calendar`:
   - Per WIP-IG day, schedules a Story at 10:00 (FB + IG, both default-checked, 1 image) and a Feed Post at 15:00 (1 image on regular days, 10-image carousel on Sunday-thread days).
   - Hover-then-click pattern for the per-day `Schedule ▾` dropdown (it's invisible until the cell is hovered).
   - Multi-image upload via `set_input_files([...])` in a single call.
   - Untick `Work in Progress IG` only when BOTH story and post succeeded; partial failures keep WIP set so the next run retries the day.

3. **Bootstrap + session manager** (`instagram/bootstrap_session.py`, `instagram/instagram_session.py`) — direct port of the LinkedIn equivalents. Dedicated `instagram/chrome_user_data/` (gitignored), Meta-specific login-redirect markers.

4. **README** (`instagram/README.md`) — mermaid workflow, CLI table, Notion field table, selectors table, gotchas, mirroring `linkedin/README.md`.

## Files modified / created

- **New**: `instagram/{__init__.py,bootstrap_session.py,instagram_session.py,clone_to_other_platforms.py,schedule_instagram_posts.py,README.md}`
- **Modified**: `config/config.json` — added top-level `instagram` block (URL, profile dir, illustration folder, editorial/illustration/posts column maps, Sunday template, default story/post times, dry-run default) and a `clone_ig_to_others` block (per-target field maps for TW/TH/SB).
- **Modified**: `.gitignore` — already had `instagram/chrome_user_data/`, no change needed.

## Validation

```powershell
# Phase 1 — config + sessions compile
& .\.venv\Scripts\python.exe -m py_compile instagram\bootstrap_session.py instagram\instagram_session.py

# Phase 2 — Notion clone dry-run for the pre-staged week 2026-05-18 → 2026-05-24
& .\.venv\Scripts\python.exe -m instagram.clone_to_other_platforms --week-start 2026-05-18 --dry-run --debug
#   → 7 in-scope rows; 6 non-thread + 1 Sunday-thread.
#   → All 7 generate writes to {illustration <P>, text <P>, repost <P>, Work in Progress <P>} for TW & TH and to {illustration SB, text SB, repost SB} for SB.
#   → Sunday: canonical caption resolved from publishIG row 20240131 (45 chars, "Fill up your own cup first 🌱"); IG row back-fill queued for illustration IG + text IG (template).

# Phase 3 — Meta scheduler dry-run, single day and Sunday-thread
& .\.venv\Scripts\python.exe -m instagram.schedule_instagram_posts --date 20260518 --dry-run --debug
#   → 1 row in-scope; story=1 image, post=1 image, caption=86 chars; fails at session-open with the expected FileNotFoundError until bootstrap is run.

& .\.venv\Scripts\python.exe -m instagram.schedule_instagram_posts --date 20260524 --dry-run --debug
#   → 1 row in-scope; story=1 image, post=10 images (all resolved from disk under archived_IGformat/); caption=0 chars (clone hasn't run live yet to back-fill the template).
```

End-to-end live verification (executed after bootstrap):

```powershell
& .\.venv\Scripts\python.exe -m instagram.bootstrap_session                                    # manual Meta login
& .\.venv\Scripts\python.exe -m instagram.clone_to_other_platforms --week-start 2026-05-18 --live
& .\.venv\Scripts\python.exe -m instagram.schedule_instagram_posts   --week-start 2026-05-18 --live
```

Outcome (2026-05-17 morning): clone written cleanly for all 7 days; planner scheduled 7 stories + 7 posts (Mon 18 – Sun 24) including the Sunday 10-image carousel; all 7 days had WIP-IG auto-unticked. Mon 18 ended up with a duplicate story due to a mid-iteration post failure on the first try (post path was fixed and the day was re-run, which re-scheduled the story too). User must manually delete the duplicate Mon 18 story in the Meta planner.

Failure artifacts land in `results/instagram/` (same convention as `results/linkedin/`).

## Selectors / approaches that DIDN'T work — and what does

The initial implementation guessed at the Meta planner UI based on the user's screenshots. Five iterations against the live DOM landed on:

1. **`Get Meta Verified` upsell blocks every click on first load.** Added `dismiss_meta_verified_modal()` after navigation and between days. The modal carries `data-surface=GeoIllustrationModal` and intercepts pointer events on the whole planner — without dismissing it, every hover/click silently times out.

2. **Day-column discovery: nested `div:has(> *:text-is("Mon 18"))` doesn't resolve.** Replaced with a JS DOM walker (`_FIND_COLUMN_JS`): find the literal text node "Mon 18", climb until an ancestor contains a Schedule button; that ancestor IS the column. Hover + click happen via that handle's `query_selector_all`.

3. **Week navigation buttons are labelled `"Left"` / `"Right"`, not `"Next week"` / `"Previous week"`.** The chevron glyph is the button's visible text and the aria-label is empty. `navigate_to_week()` clicks `Right` until the target day's column appears.

4. **Each `Schedule story` / `Schedule post` menu click opens a SUB-modal with default date+time.** The sub-modal sits ON TOP of the main composer; the main composer also has its own Cancel button. A page-wide `get_by_role("button", "Cancel").first` clicks the wrong one. `_dismiss_initial_schedule_submodal()` now does a JS climb from the sub-modal's heading to find its OWN Cancel button.

5. **Meta does NOT pre-mount an addressable `input[type=file]`.** The `Add photo/video` click opens the OS file picker. `set_input_files` on an unmounted input times out. `_upload_files()` wraps the click in `page.expect_file_chooser()` and feeds files via `file_chooser.set_files([...])` — works for both single image and 10-image carousels.

6. **Date is `<input placeholder="mm/dd/yyyy">`; time is THREE inputs** (`aria-label=hours/minutes/meridiem`), not button-styled divs as initially assumed. `_set_all_visible_date_time()` enumerates the inputs by their stable attributes and fills each via `Control+A → Delete → type` (Meta's React onChange ignores `.fill()`'s synthetic event in some builds).

7. **The post composer's date/time row is hidden until the `Set date and time` toggle is on.** The toggle is `<input type="checkbox" aria-label="Set date and time">` whose `value` reads `"false"` until clicked. `cb.click(force=True)` then read-back via `input_value()` to confirm.

8. **The composer opens at a separate URL** (`/composer/?asset_id=...`). After each day the script `goto`s `feed_url` to return to the planner — without that, the next iteration's column-finder reports "Mon 18 not present in current week".

All of this is captured in `instagram/README.md` § Selectors / Gotchas for the next platform port.
