# Planning row outcome

Every planning scheduler ends each Notion row in one of six outcomes. `planning/row_outcome.py` is the only module that names them, derives a row's outcome from its legs and answers "did this row fail?" and "did something get scheduled?".

## Why it is declared

Without a declaration the outcome is six bare uppercase strings spread over the orchestrator, five platform drivers, the failure classifier and the tests, and each reader re-derives "did this row fail?" and "did something get scheduled?" on its own. Readers drift apart: one counts a `PARTIAL` row under `OTHER`, another treats it as a failure, a third does not count it as scheduled, and a status outside the set is neither failure nor success. One declared type and one derive function make a new reader call the module instead of guessing.

## The states

The string values are the ones the result JSON and the markdown summary carry; the planning tab and `/schedule-autoheal` read them, so they never change.

| `RowStatus` | Value | Meaning | `is_failure` | `has_scheduled` |
|---|---|---|---|---|
| `LIVE` | `LIVE` | Scheduled for real | no | yes |
| `DRY` | `DRY` | A dry run walked the flow to the final action without submitting | no | no |
| `SKIP` | `SKIP` | Nothing to do: out of scope, already scheduled or switched off | no | no |
| `FAIL` | `FAIL` | No leg completed and at least one broke | yes | no |
| `PARTIAL` | `PARTIAL` | At least one leg completed and at least one broke | yes | yes |
| `LOGIN_REQUIRED` | `LOGIN-REQUIRED` | The platform session is logged out | yes | no |

`parse` maps a string to a `RowStatus`, or to `None` when it names none of them. An unrecognised status is its own answer: `is_failure` reports it as a failure and `has_scheduled` as not scheduled, and the summary table counts it under `OTHER`. It is never folded into a clean row.

## Deriving a row from its legs

`derive_row_status(legs, dry_run)` takes the per-leg outcomes of one row: one leg per platform for the Videos row, one per surface (story and post) for the Instagram row. A single-post platform (LinkedIn, Twitter, Threads) has one leg, so its driver sets the row status directly.

1. Any `LOGIN_REQUIRED` leg makes the row `LOGIN_REQUIRED`.
2. All legs `SKIP` (or no legs) make it `SKIP`.
3. Only `SKIP` and `FAIL` legs, with at least one `FAIL`, make it `FAIL`.
4. A dry run with no failed leg is `DRY`.
5. Otherwise the legs that completed (`DRY` in a dry run, `LIVE` in a live run) decide: all completed and none failed is `LIVE`, some completed and some failed is `PARTIAL`, none completed is `FAIL`.

A failed leg therefore never reads as a clean `DRY` or `LIVE`. A `PARTIAL` row in a dry run means one leg walked dry-clean and another broke.

## Who reads it

| Reader | Where | Calls |
|---|---|---|
| Summary table buckets | `PlatformResult.status_counts` in `planning_pipeline.py` | `parse` |
| Failure flag, exit code, JSON verdict | `_platform_failed` in `planning_pipeline.py` | `is_failure` |
| Markdown verdict ("Mixed result") | `_build_summary_md` in `planning_pipeline.py` | `has_scheduled`, `is_failure` |
| Failure kind in the result JSON | `classify` in `planning/_failure.py` | `parse` |
| Per-platform exit code | each driver's `failed = [...]` list | `is_failure` |
| Videos row status | `_aggregate_row_status` in `planning/videos/schedule_videos_posts.py` | `derive_row_status` |
| Instagram row status | `main` in `planning/instagram/schedule_instagram_posts.py` | `derive_row_status` |

A new reader calls the module. It does not compare a status string, build its own list of failing statuses or substring-test a leg's free text; a fact the derive does not carry joins its inputs.

## Tests

`tests/scheduling/test_row_outcome.py` holds the table of leg combinations (including the dry-run `PARTIAL` and `FAIL` cases and an unrecognised status), the failure and scheduled flags per status, the proof tests that a failed or unreadable leg never yields a clean row, a regression test for the readers agreeing on a `PARTIAL` row, and a golden test that the result JSON keeps its status strings and shape.
