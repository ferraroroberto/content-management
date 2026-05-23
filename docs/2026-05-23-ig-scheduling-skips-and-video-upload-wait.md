# 2026-05-23 — Instagram scheduling: drop stale `article LI` skip, tolerate missing carousel image, wait for video upload before Schedule

Closes [#35](https://github.com/ferraroroberto/reporting/issues/35).

## What was done

Three IG-side scheduling regressions surfaced on the 2026-05-23 overnight run; all silently dropped rows that should have gone through.

### 1. Stale `article LI` skip — IG, TW, TH planners

Each platform planner carried an identical 4-line guard:

```python
article_rels = props.get(article_col, {}).get("relation", []) or []
if article_rels:
    logger.info("⏭️  %s: has article LI — Phase 2 scope (LinkedIn), skipping IG too.", title)
    continue
```

The rationale dated to before issue #16 (the LinkedIn POST + CAROUSEL routes) shipped — at the time, LinkedIn-article days were not yet handled, and the IG/TW/TH planners were defensively skipping them. #16 is closed and merged; LinkedIn now dispatches article days off the relation pattern directly (ILL / POST / CAROUSEL — `planning/linkedin/schedule_linkedin_posts.py:6-15`). The IG/TW/TH coupling was leftover scaffolding that silently dropped 20260526 and 20260528 from the overnight run even though their `Work in Progress IG = true` rows had independent illustration + caption ready to schedule.

The three guards and their `article_col = ed_cols["article_rel"]` locals are removed. `config/config.json` `editorial_columns.article_rel` entries stay in place — LinkedIn still uses them.

### 2. Sunday carousel: fail-one no longer = fail-all

`resolve_post_payload`'s `thread_ig` branch built the 10-image carousel with no `try` around `_resolve_image_path`, so a single missing PNG aborted the whole day with `payload resolution failed: Illustration not found: …`. On 2026-05-23 that killed 20260531 because one of the ten files was missing from `archived_IGformat/`.

The loop now logs a per-illustration warning, continues on `FileNotFoundError`, and at the end logs a summary line:

```
🖼️ 20260531: carousel built with 9/10 images (1 missing illustration(s) skipped).
```

If fewer than 2 illustrations survive (IG's hard floor for carousel posts), the row fails honestly rather than silently downgrading to a single-image post that the caller would treat as the non-thread route.

### 3. Videos-IG: wait for upload to finish, don't hard-sleep

`planning/videos/videos_instagram.py` uploaded the .mp4 and then hard-slept 6 seconds before clicking Schedule. On 2026-05-23 that wasn't enough — Meta's composer kept the Schedule action `aria-disabled="true"` for the full 10 s `_click_action_button` timeout window, and 20260526 IG-video failed after the locator iterated 23 retries on a stuck-disabled button.

A new shared helper `wait_action_button_enabled(page, name, *, timeout_ms=90000)` lives next to `_click_action_button` in the IG planner module. The videos-IG driver now keeps a small 2 s head-start (to let the upload mount + re-render the Schedule button) and then polls `aria-disabled` for up to 90 s before clicking. On ceiling-hit, it raises `Action button 'Schedule' stayed aria-disabled after 90000 ms — media upload likely did not finish.` rather than a generic Playwright click-timeout traceback.

LinkedIn's video driver already uses a poll-based readiness check (`_wait_for_video_ready` + `_wait_for_upload_complete`); TW and TH still hard-sleep 2.5 s after upload, but no failures have been observed there in practice — generalising the poll across all four drivers is parked as a follow-up (out of scope per the issue).

## Files modified

- `planning/instagram/schedule_instagram_posts.py` — removed `article LI` skip block + `article_col` local in `fetch_wip_ig_rows`; added `try / except FileNotFoundError` + summary logging in `resolve_post_payload`'s thread branch; added `wait_action_button_enabled` helper next to `_click_action_button`.
- `planning/twitter/schedule_twitter_posts.py` — removed `article LI` skip block + `article_col` local in `fetch_wip_tw_rows`.
- `planning/threads/schedule_threads_posts.py` — removed `article LI` skip block + `article_col` local in `fetch_wip_th_rows`.
- `planning/videos/videos_instagram.py` — import `wait_action_button_enabled`; replaced the 6 s fixed sleep with a 2 s head-start + `wait_action_button_enabled(page, "Schedule", timeout_ms=90000)` before the Schedule click.
- `planning/instagram/README.md` — Mermaid filter node no longer mentions `article LI`; scope sentence rewritten; column table no longer lists `article_rel` (now LI-only); carousel partial-tolerance behaviour documented.
- `planning/twitter/README.md`, `planning/threads/README.md` — removed the `article LI` "skip-marker (Phase-2 LinkedIn scope)" row from the column tables.
- `planning/videos/README.md` — IG video bullet rewritten to describe the upload-ready poll instead of the old 6 s sleep.

## Validation

- `& .\.venv\Scripts\python.exe -m py_compile planning\instagram\schedule_instagram_posts.py planning\twitter\schedule_twitter_posts.py planning\threads\schedule_threads_posts.py planning\videos\videos_instagram.py` — clean.
- A LIVE re-run of the failed 20260526 IG-video and 20260531 IG-carousel cases is left for the user — both require actual Meta scheduling on the live editorial DB, which the issue's acceptance criteria explicitly expect during follow-up planning runs.
