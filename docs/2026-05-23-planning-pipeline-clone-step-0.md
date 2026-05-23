# 2026-05-23 — Planning pipeline: wire clone as step 0 + derive captions for repost days

Closes [#32](https://github.com/ferraroroberto/reporting/issues/32).

## What was done

While running `planning_pipeline.py` the orchestrator was skipping the IG → TW/TH/SB clone step entirely, leaving every Threads / Twitter / Substack row for the upcoming week with empty captions and illustrations because the platform schedulers downstream only read whatever was already on those columns. The clone module — `planning.instagram.clone_to_other_platforms.main` — exists and works fine when invoked directly; it was just never plugged into the orchestrator.

Wiring it in and running it live for the upcoming week surfaced a second bug: every "repost" day errored with `non-thread day but text IG is empty`. The repost flow is intentional — the canonical caption lives on the illustration's earliest `publishIG` row, reachable via `illustration → publishIG → earliest row's text IG`. The LinkedIn scheduler and the Sunday-thread branch of the clone already do this derivation; the non-thread branch of the clone was the lone outlier that errored instead of falling through.

Both halves ship together as one PR (you can't run the pipeline without the wiring, and you can't run the wiring without the repost fix). Commit order is **fix first, wiring second** so every intermediate commit is functional — landing the wiring before the fix would have published a `main` whose pipeline calls a clone known to error on repost days.

## Files modified

- `planning/instagram/clone_to_other_platforms.py` — `resolve_source` non-thread branch: when `row.text_ig` is empty, fall through to `_canonical_caption_from_publish_ig` (the existing helper) instead of raising. The derived caption is also returned via `CloneSource.ig_row_text` so `apply_to_targets` back-fills the IG row's own `text IG` field — a human reading the editorial DB after a clone run sees real text instead of an empty cell.
- `planning_pipeline.py` — new `--skip-clone` flag, `_build_clone_args(args)`, `_run_clone(args)`. The clone runs as step 0 before the LinkedIn / Instagram / Twitter / Threads / Videos loop. Clone failures (raises or `SystemExit`) are logged and reported in the final summary but do **not** abort the downstream schedulers — same continue-on-error contract the project already applies between platforms. The orchestrator threads the clone exit code into the same `PlatformResult` shape as every other step; the `_run_platform` helper is not used because clone's `main()` returns a bare `int`, not the platform schedulers' `(int, rows)` tuple.
- `planning/instagram/README.md` — "Daily / weekly use" header note that the clone now runs from the orchestrator; new gotcha entry for the repost-day `text IG` derivation.

## Validation

- `& .\.venv\Scripts\python.exe -m py_compile planning_pipeline.py planning\instagram\clone_to_other_platforms.py` — clean.
- `& .\.venv\Scripts\python.exe planning_pipeline.py --dry-run --skip-linkedin --skip-instagram --skip-twitter --skip-threads --skip-videos` — exercises the clone path through the orchestrator with every scheduler off. Output:
  - `━━━━━━━━━━ Clone (IG → TW/TH/SB) ━━━━━━━━━━` banner prints.
  - Clone runs in DRY-RUN, lists 6 in-scope WIP-IG rows for 2026-05-25 → 2026-05-31.
  - All TW/TH/SB targets correctly identified as "already has illustration/text — skipping" (idempotency preserved from the prior live clone run).
  - The Sunday-thread day (20260531) derives a canonical caption from `publishIG` row `20230211` — proving the publishIG chain still resolves through the wiring.
- The repost-day non-thread caption derivation itself was validated live in a prior parallel-session run: four days (20260525/26/27/29) had their `text IG` derived from canonical publishIG rows `20241125 / 20241116 / 20241106 / 20230613`, written to TW/TH/SB, and back-filled to the IG row's `text IG`. Already-populated rows correctly skipped.
