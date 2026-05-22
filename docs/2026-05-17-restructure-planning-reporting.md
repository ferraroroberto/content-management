# 2026-05-17 — Restructure: `planning/` + `reporting/`, `launch_planning` orchestrator

Issue: [#14](https://github.com/ferraroroberto/reporting/issues/14).

## What changed

### Layout

Five platform packages moved under `planning/`, three data packages moved
under `reporting/`:

```
BEFORE                              AFTER
linkedin/                           planning/linkedin/
instagram/                          planning/instagram/
twitter/                            planning/twitter/
threads/                            planning/threads/
substack/                           planning/substack/
notion/                             reporting/notion/
social_client/                      reporting/social_client/
process/                            reporting/process/
init.py                             reporting_pipeline.py
launcher.bat                        launch_reporting.bat
(new)                               planning_pipeline.py
(new)                               launch_planning.bat
```

`config/`, `results/`, `logs/`, `docs/`, `tmp/` stayed at root — shared by
both pipelines.

### New orchestrator: `planning_pipeline.py` + `launch_planning.bat`

Runs **LinkedIn → Instagram → Twitter → Threads** in fixed order on
whatever rows have `Work in Progress <P>` ticked in the Notion editorial
DB (no date filter — supports multi-week planning runs). Each scheduler's
`main()` returns `(exit_code, list[dict])` with per-day status; the
orchestrator aggregates these into a markdown summary at
`results/planning/YYYY-MM-DD-HHMMSS-summary.md` (+ stdout).

**Continue-on-error**: a per-platform failure does not stop the next
platform. The summary's verdict line distinguishes idempotent no-op vs
successful runs vs failures.

### Scheduler changes (additive)

All four schedulers gained:
- `--all-wip` flag (mutex with `--week-start` / `--date`) — fetches every
  WIP-<P> row in the editorial DB via a single Notion query with no title
  filter.
- `main()` returns `tuple[int, list[dict]]` instead of `int`. The
  `if __name__ == "__main__": raise SystemExit(main()[0])` guard takes
  the exit code; the orchestrator consumes the result list.

### Renames + import rewrites

- `init.py` → `reporting_pipeline.py`
- `launcher.bat` → `launch_reporting.bat`
- All `from instagram.…` etc → `from planning.instagram.…` (and same for
  the four other planning packages).
- All `from notion.…` / `from social_client.…` / `from process.…` → the
  `reporting.…` namespace.
- Each inner script's `sys.path.append(Path(__file__).parent.parent)`
  bumped to `parent.parent.parent` (need repo root on path, not the
  bucket dir).
- `*_session.py` `CONFIG_PATH` bumped one level deeper for the same
  reason.

### Cleanup

- Deleted 43 zero-byte dev `.log` files from this week's selector
  iteration in `logs/`.
- Removed stray `__pycache__/` at repo root.
- Re-pointed `.gitignore` entries to the new locations
  (`planning/<P>/chrome_user_data/`, `reporting/process/.env`).

### Config

`config/config.json` — five `user_data_dir` paths re-pointed to
`planning/<P>/chrome_user_data`. Nothing else moved.

## Validation

```powershell
# 1. py_compile across the entire moved tree
$files = Get-ChildItem -Path planning, reporting -Recurse -Include *.py
$files += Get-ChildItem -Path planning_pipeline.py, reporting_pipeline.py
& .\.venv\Scripts\python.exe -m py_compile ($files | %% { $_.FullName })
# → ALL 48 FILES OK

# 2. Single-platform smoke from the new path
& .\.venv\Scripts\python.exe -m planning.twitter.schedule_twitter_posts --all-wip --dry-run --debug
# → "🎯 All-WIP mode" + "⚠️ No WIP-TW rows in target range. Nothing to do."

# 3. Idempotency dual-run (acceptance gate)
.\launch_reporting.bat auto        # daily numbers — upserts no-op for already-collected day
.\launch_planning.bat              # dry-run; all four platforms find 0 WIP rows → ✅ Idempotent run
```

## Stream Deck / scheduled-task migration

Anything outside the repo that called `launcher.bat` needs to be
re-pointed at `launch_reporting.bat`. The planning workflow gets a new
shortcut to `launch_planning.bat live auto`.

## What did NOT change

- `reporting/` internals (data_processor, supabase_*, notion_supabase_sync)
  — move-only.
- Database schema, Notion editorial column names, illustrations folder
  path.
- The Notion editorial-clone step still lives at
  `planning/instagram/clone_to_other_platforms.py` — it's logically part
  of the IG workflow.
- `process/.env`, `config/config.json`, `config/mapping.json` — content
  unchanged.

## Future work hooks (not built — layout reserves room)

- `planning/_common/` for shared session-manager / scheduler base classes
  if the duplication earns the abstraction.
- `planning/engagement/` for in-work self-engagement (e.g. liking my own
  posts). External-interaction automation is explicitly out of scope.
