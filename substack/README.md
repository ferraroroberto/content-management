# Substack automation

Browser-driven automation that replaces two manual daily steps:

1. Publish a Substack **Note** from the day's row in the Notion editorial database.
2. Scrape the **total followers** count from the Substack stats page and write it back to the same row.

Both steps use Playwright to drive **real Chrome** (`channel="chrome"`) against
a **dedicated, project-local Chrome profile directory**. The two scripts share
that profile so they only require one manual login per cookie lifetime.

### Why real Chrome, and what about my normal Chrome profile?

Playwright's bundled Chromium gets flagged by Substack's reCAPTCHA at sign-in
because it advertises automation. To work around that **without** installing
captcha-solver services or anti-detect plugins, this package launches the
user's installed Chrome binary against a separate on-disk profile created
specifically for this automation.

**Your regular Chrome profile is never opened, read, or written.** The
dedicated profile lives at the path configured under `substack.user_data_dir`
(default: `substack/chrome_user_data/`, gitignored). `SubstackSession` also
refuses to start if `user_data_dir` resolves to a path that looks like a real
Chrome profile location (`Google/Chrome/User Data`, `Library/Application
Support/Google/Chrome`, etc.).

## Module layout

```
substack/
├── __init__.py
├── README.md                       — this file
├── substack_session.py             — Playwright context + storage_state lifecycle
├── bootstrap_session.py            — one-time headed login; writes storage_state.json
├── notion_editorial.py             — read/write editorial rows (role→column map)
├── post_substack_note.py           — step 1 (publish Note)
├── update_substack_followers.py    — step 2 (scrape total followers)
└── daily_pipeline.py               — orchestrator; CLI entry
```

## Prerequisites

1. Install Python deps:
   ```powershell
   & .\.venv\Scripts\pip.exe install -r requirements.txt
   ```
   No `playwright install chromium` step needed — we drive the real Chrome
   already installed on the machine.
2. Configure the `substack` block in `config/config.json` (see `config_example.json`).
3. Run the one-time session bootstrap (a Chrome window opens against the
   dedicated profile — log in manually, then press Enter in the terminal):
   ```powershell
   & .\.venv\Scripts\python.exe -m substack.bootstrap_session
   ```
   This creates `substack/chrome_user_data/` (gitignored) holding the
   dedicated profile.

## Config keys (under `substack`)

| Key | Purpose |
| --- | --- |
| `handle` | Substack handle without leading `@`. |
| `publish_url` | Publication URL (e.g. `https://you.substack.com/publish/home`). |
| `profile_url` | Public profile URL (where notes are visible). |
| `stats_audience_url` | Audience-stats URL (where the followers count is rendered). |
| `user_data_dir` | Dedicated Chrome profile directory (gitignored; defaults to `substack/chrome_user_data`). Must NOT point at your real Chrome profile — the session refuses to start if it does. |
| `illustrations_folder` | Absolute folder containing the daily image. Joined with `image_filename`. |
| `editorial_db_id` | Notion editorial database id. |
| `notion_columns` | Role-to-column map. Roles: `title_day`, `text_body`, `image_filename`, `post_url`, `follower_count`. |
| `headless` | Optional bool (default `false`). |
| `dry_run_default` | Optional bool (default `false`). When `true`, step 1 always runs as a dry-run unless `--force` is passed. |

The `image_filename` role is expected to resolve to a value like `mypic.png`.
If your column is a formula that joins multiple filenames with `", "`, the
first filename is used.

## CLI

### Step 1 — publish a Note
```powershell
& .\.venv\Scripts\python.exe -m substack.post_substack_note [--date YYYYMMDD] [--dry-run] [--force] [--debug]
```
- Default date is today (local).
- Idempotent: if the editorial row's `post_url` is already populated, the script exits 0 unless `--force` is supplied.
- `--dry-run` composes the Note (text + image) but **does not** click Post. A screenshot is saved under `results/substack/<date>-dryrun.png`.

### Step 2 — scrape followers
```powershell
& .\.venv\Scripts\python.exe -m substack.update_substack_followers [--date YYYYMMDD] [--debug]
```
Always overwrites the `follower_count` column.

### Combined pipeline
```powershell
& .\.venv\Scripts\python.exe -m substack.daily_pipeline [--date YYYYMMDD] [--dry-run] [--skip-post] [--skip-followers] [--force] [--debug]
```
Both steps share a single browser launch. A failure in step 1 does not block step 2.

## Failure handling

- A redirect to `sign-in` raises `LoginRequiredError`; the script exits non-zero and asks you to re-run `bootstrap_session`.
- Selector-level failures save a screenshot to `results/substack/` so you can inspect what changed.
- The dedicated Chrome profile auto-persists cookies on every close — nothing to manage by hand.

## Known risks

- The Substack DOM may change. Selectors are anchored on ARIA roles + accessible-name regexes, but breakages are still possible.
- The cookie eventually expires; re-run `bootstrap_session` when that happens.
- Step 1 publishes content to a public platform. Use `--dry-run` first when in doubt.
