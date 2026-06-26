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

## Native HTTP API path (cookie-auth) — preferred for the follower count

Alongside the Playwright automation above, this package also talks to Substack's
private HTTP API directly with the session cookie (no browser, no DOM selectors,
no reCAPTCHA, no profile lock). It is the **default source for the daily follower
count** and provides manual tools to pull an archive and create/publish editions.
The Playwright path is **kept** as an alternative source — nothing here removes it.
See `docs/substack-native-api.md` for the design, endpoint map, and fragility notes.

Modules:

```
api_client.py        — session loader + fetch_follower_count() + SubstackAPI (pull/create/publish)
extract_session.py   — harvest cookies+UA from the Chrome profile → api_session.json (gitignored)
api_pull.py          — manual CLI: dump a post archive to JSON
api_create.py        — manual CLI: create a draft edition (publish only with --confirm)
```

One-time / once-per-~89-days setup (the `substack.sid` cookie lives ~89 days):

```powershell
# After bootstrap_session has logged the dedicated profile in:
& .\.venv\Scripts\python.exe -m planning.substack.extract_session
```

This writes `planning/substack/api_session.json` (gitignored: live auth cookies +
the browser User-Agent that `cf_clearance` is bound to). When the cookie expires
the API returns 401/403 and the helpers raise `SessionExpiredError` telling you to
re-run `extract_session`.

### Daily follower count via the API

Set `substack_profile.source` to `"native"` in `config.json` (leave the other
keys — the endpoint loop still needs `api_url` present). The reporting pipeline
then routes the follower count through
`reporting/scrape_client/substack_native.py::fetch_profile`, which returns the
same `{"num_followers": N}` envelope as the Playwright scraper. Flip back to
`"playwright"` to use the browser scrape. (`substack_posts` / note engagement
stays on Playwright — the Notes endpoints aren't reverse-engineered yet.)

### Manual archive + create

```powershell
# Pull the latest published posts into results/substack/archive_<date>.json
& .\.venv\Scripts\python.exe -m planning.substack.api_pull [--limit N] [--with-body]

# Create a private DRAFT edition (does NOT email anyone); validate it; print the edit URL
& .\.venv\Scripts\python.exe -m planning.substack.api_create --title "..." --subtitle "..." --body "para" [--image p.png]
# Add --confirm ONLY when you intend to publish + email the whole subscriber list (irreversible).
```

`api_create` defaults to draft + pre-publish validation and never publishes
without `--confirm`; it is never wired into the daily cron.

## Module layout

```
substack/
├── __init__.py
├── README.md                       — this file
├── substack_session.py             — Playwright context + storage_state lifecycle
├── bootstrap_session.py            — one-time headed login; writes storage_state.json
├── notion_editorial.py             — read/write editorial rows (role→column map)
├── post_substack_note.py           — publish Note
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
   & .\.venv\Scripts\python.exe -m planning.substack.bootstrap_session
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
| `notion_columns` | Role-to-column map. Roles: `title_day`, `text_body`, `image_filename`, `post_url`. The `follow SB` follower column is **not** in this map — it is populated by the reporting pipeline (`reporting/scrape_client/substack.py::fetch_profile` → `data_processor` → `notion_update`, mapped from `profile.num_followers_substack`) like every other platform's follower count. |
| `headless` | Optional bool (default `false`). |
| `dry_run_default` | Optional bool (default `false`). When `true`, step 1 always runs as a dry-run unless `--force` is passed. |

The `image_filename` role is expected to resolve to a value like `mypic.png`.
If your column is a formula that joins multiple filenames with `", "`, the
first filename is used.

## CLI

### Step 1 — publish a Note
```powershell
& .\.venv\Scripts\python.exe -m planning.substack.post_substack_note [--date YYYYMMDD] [--dry-run] [--force] [--debug]
```
- Default date is today (local).
- Idempotent: if the editorial row's `post_url` is already populated, the script exits 0 unless `--force` is supplied.
- `--dry-run` composes the Note (text + image) but **does not** click Post. A screenshot is saved under `results/substack/<date>-dryrun.png`.

### Follower scrape (now in the reporting pipeline)
The Substack follower count is no longer scraped from here. See
`reporting/scrape_client/substack.py::fetch_profile`, dispatched by
`reporting/social_client/social_api_client.py` when the
`substack_profile` block in `config.json` carries `"source": "playwright"`.
The value flows through `data_processor` → `profile_aggregator` →
`notion_update` like every other platform's follower count.

### Combined pipeline
```powershell
& .\.venv\Scripts\python.exe -m planning.substack.daily_pipeline [--date YYYYMMDD] [--dry-run] [--skip-post] [--force] [--debug]
```

## Composer selector notes

A couple of non-obvious DOM facts the Note publisher relies on, kept here so a
future selector fix starts from the right mental model rather than from the ARIA
spec:

- **The note composer is not a `role="dialog"` and the editor is not a
  `role="textbox"`.** The composer is a custom popover and the editor is a
  ProseMirror `contenteditable` div. `post_substack_note.py` therefore finds the
  editor by `[contenteditable="true"]` (preferring one whose `data-placeholder`
  mentions "mind") and scopes the composer by walking up to the nearest ancestor
  that holds both the Cancel and Post buttons — so the image file-input search
  doesn't hit the page's avatar or cover-photo inputs.
- **Wait for the real upload preview, not any `<img>`.** A naive `img[src]`
  match returns instantly against the tiny avatar in the popover header while the
  Post button is still disabled. The publisher instead waits for an `<img>`
  inside the composer with `naturalWidth > 200 && naturalHeight > 200 &&
  complete` — the first such image is the upload preview, not an avatar.

## Failure handling

- A redirect to `sign-in` raises `LoginRequiredError`; the script exits non-zero and asks you to re-run `bootstrap_session`.
- Selector-level failures save a screenshot to `results/substack/` so you can inspect what changed.
- The dedicated Chrome profile auto-persists cookies on every close — nothing to manage by hand.

## Known risks

- The Substack DOM may change. Selectors are anchored on a mix of ARIA roles, accessible-name regexes, and structural fallbacks (see *Composer selector notes* above), but breakages are still possible.
- The cookie eventually expires; re-run `bootstrap_session` when that happens.
- Step 1 publishes content to a public platform. Use `--dry-run` first when in doubt.
