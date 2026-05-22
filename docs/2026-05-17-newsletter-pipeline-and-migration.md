# Newsletter pipeline + monorepo migration

**Issue:** #18 (follow-on to #17)

## What was done
Pulled the weekly newsletter workflow into a single orchestrated pipeline.
Two structural changes + three migrations + one orchestrator + one
launcher, then the monorepo originals get retired.

### B1 — Folder rename
- `archive/newsletter/` → `newsletter/`
- Dropped the redundant `archive/` parent (it never had any siblings).
- `chrome_user_data/` (gitignored) moved with the rest; Gmail session
  preserved.
- Updated imports across the package: `archive.newsletter.X` →
  `newsletter.X`.
- Fixed `REPO_ROOT = Path(__file__).resolve().parent.parent.parent` (4
  levels) → `parent.parent` (2 levels) in `pipeline.py`, `dry_run.py`,
  `bootstrap_session.py`.
- Updated `.gitignore`, both READMEs, and `bootstrap_chrome.bat`
  comments.

### B2 — Migrated 3 scripts from `E:\automation\automation\notion\`
- `newsletter/normalize_names.py` — title → sentence case with proper
  name preservation, ALL-CAPS acronym handling, optional spaCy PERSON
  entity detection.
- `newsletter/normalize_url.py` — strip query params + fragments except
  for domains in the preserve list.
- `newsletter/build_newsletter.py` — group articles by topic for one
  newsletter, sort, emit HTML, prompt for must-read line, copy to
  clipboard.
- `newsletter/normalize_names_words.json` — sidecar with
  `proper_name_whitelist` (299 entries), `special_cases`,
  `common_words`, `common_words_with_punct`. Only the word lists — API
  token and DB ids now come from `config/config.json`.

Each migrated script keeps its original CLI but now also exposes a
`run(...)` callable that the orchestrator drives directly (no
`subprocess`). Config loading switched from per-script JSON +
`NOTION_API_TOKEN` env var to a single read of `config/config.json`
(`notion.api_token` plus the existing `newsletter_archive` block).
HTML output landed under `results/newsletter/N{NNN}.html` instead of
next to the script.

### B3 — Orchestrator
- `newsletter_pipeline.py` (repo root) — sequences:
  1. `bootstrap_chrome.bat` (kill + relaunch + poll `:9222`)
  2. Press-Enter wait for the user to open article tabs
  3. `newsletter.pipeline.run_batch(write=True)`
  4. Press-Enter wait
  5. `newsletter.normalize_names.run(days=14)`
  6. `newsletter.normalize_url.run(days=14)`
  7. Prompt for newsletter number
  8. `newsletter.build_newsletter.run(number)` — HTML + must-read line
- `launch_newsletter.bat` (repo root) — visible launcher, mirrors
  `launch_planning.bat` / `launch_reporting.bat`.
- CLI flags: `--newsletter NNN`, `--skip-bootstrap`, `--days N`,
  `--debug`.

### B4 — Docs
- Global `README.md`: "Three pipelines" intro now mentions the full
  newsletter flow (not just archive). Project tree shows
  `newsletter_pipeline.py` + `launch_newsletter.bat`. Launchers table
  becomes 3 rows.
- `newsletter/README.md`: full rewrite covering bootstrap → archive →
  normalise → build, with mermaid for all 4 stages, full one-time setup
  list, CLI table for running steps in isolation.
- This changelog.

## Configuration changes
Added to the `newsletter_archive` block in `config/config.json` and
`config/config_example.json`:
```jsonc
"url_preserve_domains": [
  "youtube.com", "www.youtube.com", "youtu.be",
  "vimeo.com", "twitter.com", "x.com"
]
```
Used by `normalize_url`. The previous fields (DB ids, LLM hub, fuzzy
threshold, category cap, topic→rollup map, author_fallback_name) all
unchanged.

## Dependencies added
`requirements.txt`: `spacy`. Plus the model:
```
.venv\Scripts\python -m spacy download en_core_web_sm
```
Falls back to whitelist-only behaviour if spaCy or the model isn't
available — install is not strictly required.

## Files changed
| Type | Path |
|---|---|
| Renamed | `archive/newsletter/*` → `newsletter/*` (13 files) |
| Renamed | `archive/__init__.py` → `newsletter/__init__.py` |
| New | `newsletter/normalize_names.py` |
| New | `newsletter/normalize_names_words.json` |
| New | `newsletter/normalize_url.py` |
| New | `newsletter/build_newsletter.py` |
| New | `newsletter_pipeline.py` (repo root) |
| New | `launch_newsletter.bat` (repo root) |
| Modified | `README.md` (root) — newsletter section + launchers + tree |
| Modified | `newsletter/README.md` — full rewrite |
| Modified | `config/config.json`, `config/config_example.json` —
            `url_preserve_domains` added |
| Modified | `.gitignore` — `archive/newsletter/chrome_user_data/` →
            `newsletter/chrome_user_data/` |
| Modified | `requirements.txt` — `spacy` added |

## Validation
- `py_compile` on all 5 new modules + 11 renamed modules: clean.
- Each migrated class instantiates with config from `config/config.json`:
  - `NotionNameNormalizer` → 299-entry whitelist + spaCy loaded
  - `NotionURLNormalizer` → preserve domains = the 6 expected
  - `NotionNewsletterBuilder` → articles + newsletter DB ids resolved
- Orchestrator imports cleanly; all `step_*` functions present.

## Monorepo cleanup (separate commit in the monorepo)
After the orchestrator has run successfully end-to-end, delete from
`E:\automation\automation\notion\`:
- `normalize_names.{py,bat,json,md}`
- `normalize_url.{py,bat,json,md}`
- `build_newsletter.{py,bat,json,md,html}`

That commit lives in the `automation` repo, not this one.

## Deferred (Phase C)
Rename `reporting` → `content-management`: GitHub repo, local folder,
the parent-level `reporting-remote.bat` and `reporting.code-workspace`.
Will get its own issue once Phase B has been used in anger for a couple
of weekly cycles.
