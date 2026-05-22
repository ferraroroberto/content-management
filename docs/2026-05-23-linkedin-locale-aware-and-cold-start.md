# 2026-05-23 — LinkedIn drivers: locale-aware selectors + cold-start tolerance

Closes [#27](https://github.com/ferraroroberto/reporting/issues/27).

## What was done

The 2026-05-23 overnight `planning_pipeline.py --live` run dropped the first LinkedIn row of every Playwright session — one in the photo flow (20260525, share-box `Photo` click timeout) and one in the videos flow (20260526, share-box `Video` click timeout). Subsequent rows in the same session always succeeded, so the failure mode was first-action-of-session only. Investigation surfaced two compounding causes:

1. **Cold-start race.** LinkedIn redirects `/feed/` to `/` for logged-in users and re-mounts the feed share box client-side; the existing 10 s `.click()` timeout occasionally lost the race.
2. **Spanish UI.** The connected LinkedIn account renders in Spanish, and LinkedIn's per-account UI language setting overrides any `Accept-Language` / `--lang=en-US` hint from the browser. Every English-named accessible-name regex (`/^photo$/i`, `/^schedule post$/i`, `:has-text("Next")`, …) silently missed once LI flipped to Spanish mid-session.

This change addresses both:

- New module **`planning/linkedin/linkedin_labels.py`** centralizes every user-facing LinkedIn button label as an EN | ES regex union, plus localized helpers for the calendar day aria-label (`calendar_day_aria_re`), month header (`calendar_header_candidates`), and time-picker entry (`time_picker_candidates`). Extending to a third language means editing one alternation in one file; no call-site changes.
- `planning/linkedin/schedule_linkedin_posts.py` and `planning/videos/videos_linkedin.py` replace every inline `re.compile(...)` and `:has-text("Next")` with imports from the new module. The Photo / Video / "Start a post" feed-entry helpers now share `linkedin_composer.FEED_ENTRY_CLICK_TIMEOUT_MS` (30 s) — the cold-start tolerance is in one constant, applied symmetrically across both modules.
- `config/chrome_launch.py` gains `locale="en-US"` + `--lang=en-US` as a best-effort browser-locale hint. This nudges sites that *do* honor `Accept-Language` (Instagram, Twitter, Threads, Substack) into English — but it is explicitly **not** the LI fix, because LI's account-level language setting wins.
- One in-file cleanup: the duplicated post-set date/time verification block at the tail of `_set_schedule_datetime` is collapsed to a single check, now against the new locale candidate tuple.

## Files modified

- `config/chrome_launch.py` — `locale="en-US"`, `--lang=en-US`.
- `planning/linkedin/linkedin_composer.py` — `FEED_ENTRY_CLICK_TIMEOUT_MS = 30000`.
- `planning/linkedin/linkedin_labels.py` — **new**. Central EN | ES label registry + calendar / time-picker locale helpers.
- `planning/linkedin/schedule_linkedin_posts.py` — every inline `re.compile` and `:has-text("Next")` replaced; locale-aware date / time pickers; duplicate verification block removed.
- `planning/videos/videos_linkedin.py` — `_click_video_button` uses `VIDEO_BTN_RE`; both `Next` `:has-text` calls accept English + Spanish; `FEED_ENTRY_CLICK_TIMEOUT_MS` from the shared constant; unused `import re` removed.
- `planning/linkedin/README.md` — selector tables point at the named constants in `linkedin_labels.py`; new gotchas for the LI account-language override and the cold-start click timeout.

## Validation

- `& .\.venv\Scripts\python.exe -m py_compile config\chrome_launch.py planning\linkedin\linkedin_composer.py planning\linkedin\linkedin_labels.py planning\linkedin\schedule_linkedin_posts.py planning\videos\videos_linkedin.py` — clean.
- **Dry-run** on the originally-failing row: `python -m planning.linkedin.schedule_linkedin_posts --all-wip --dry-run` walked the photo flow end-to-end on 20260525 (ILL route) — feed → Photo → upload → ALT (174 chars) → Next → composer → schedule dialog → date/time set, screenshot captured. ~22 s.
- **LIVE** on the same row: `python -m planning.linkedin.schedule_linkedin_posts --all-wip --live` — scheduled successfully in 27 s, WIP-LI unticked in Notion. Mid-session LinkedIn flipped the UI back from English to Spanish (visible in the after-screenshot's "Mis publicaciones programadas" footer + green confirmation banner); the Spanish-aware selectors carried the rest of the flow through.
