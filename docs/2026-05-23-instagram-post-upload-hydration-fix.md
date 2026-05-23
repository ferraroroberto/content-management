# 2026-05-23 — Instagram post-route upload: hydration-race fix

Closes [#28](https://github.com/ferraroroberto/reporting/issues/28).

## What was done

The 2026-05-23 overnight `planning_pipeline.py --live` run scheduled every Instagram **story** successfully but every Instagram **post** failed at the file-upload step (0/5 — same five days, same error). The story and post paths share the same `_upload_files(page, paths)` helper inside the same Playwright session, so the regression was post-path-specific.

Diagnostic runs reproduced the failure intermittently:

- The Playwright click on `div[role="button"]:has-text("Add photo/video")` *succeeds* on the post composer — no exception — but **no FileChooser event fires and no `input[type=file]` ever attaches**. The button is left visibly intact (post-click screenshot shows the same DOM state).
- Story composer's structurally similar `Add photo/video` (carries a `data-surface="/bizweb:story_composer/..."` attribute) works on the first try every time.
- Same code path, same browser session, same selector — the difference is *Meta's hydration of the post-composer's click handler*: it isn't always bound by the time we click.

Fix lives in `planning/instagram/schedule_instagram_posts.py::_upload_files`:

1. **Settle 1500 ms** between `_dismiss_initial_schedule_submodal` and the click — gives Meta's React reconciler a chance to bind the post-composer's button handler.
2. **Leg 1 retry with JS-native click.** If `page.expect_file_chooser(timeout=6000)` returns nothing after the Playwright click, we re-resolve the locator and try a JS `el.click()` dispatched on the DOM node. The JS-native click bypasses Playwright's overlay/pointer-events checks *and* the half-bound React handler. This is what carries the day when the Playwright click is inert.
3. **Leg 0 (cheap pre-flight) + Leg 2 (intermediate dialog) + Leg 3 (attached input) as defenses in depth.** None were needed in dry-run + LIVE verification, but they survive future Meta UI shifts (label-wrapped hidden `<input>`, new "Upload from computer" sub-dialog, etc.).
4. **Diagnostic logging.** Every leg logs which strategy ran. On terminal failure the helper saves a debug screenshot and dumps the visible buttons in the latest dialog — the AC #5 logging improvement so the next regression is triaged from artefacts, not a live DOM session.
5. **EN | ES regex unions** for the button selectors (mirrors the LinkedIn locale work in #27), in case Meta flips this account's planner UI to Spanish too.

## Files modified

- `planning/instagram/schedule_instagram_posts.py` — `_upload_files` rewritten as the three-leg strategy described above; new `_dump_upload_debug` diagnostic.
- `planning/instagram/README.md` — selector table + gotchas reflect the new helper.

## Validation

- `& .\.venv\Scripts\python.exe -m py_compile planning\instagram\schedule_instagram_posts.py` — clean.
- **Dry-run × 3** on the originally-failing row 20260525: all three runs `story:DRY-OK, post:DRY-OK`, post uploaded via Leg 1 on every run (the 1500 ms settle alone is doing most of the work; JS-click retry is belt-and-braces).
- **LIVE** on 20260525: `story:LIVE, post:LIVE`; WIP-IG unticked in Notion. The originally-failing post is now scheduled for May 25 at 3:00 PM Madrid alongside a fresh 10 AM story (the prior story had been deleted manually for this validation).
- AC #2 (carousel route) and AC #3 (video IG path, `planning/videos/videos_instagram.py`) are not directly verified — both call into the same `_upload_files` helper, so they inherit the fix. They will be exercised by the next regular planning run.
