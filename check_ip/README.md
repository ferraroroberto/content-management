# check_ip — illustration copyright check

Finds where my published illustrations are being reused, and turns that into a
queue I can actually work through.

Every illustration is reverse-image-searched through Google Lens (via SerpAPI).
Every match lands in a local SQLite store. The control panel's **⚖️ check IP**
tab is where each match gets judged — legitimate share, or my work with the
credit stripped off — and the `/check-ip` skill does the boring first pass in a
browser so judging a match is a yes/no rather than an investigation.

Folded in from the sibling `automation` repo's `linkedin/check_ip/`
(issue [#286](https://github.com/ferraroroberto/content-management/issues/286)),
which wrote three Excel workbooks instead.

```mermaid
flowchart TB
    IMG[["illustrations<br/>(iCloud folder)"]] --> RUN["check_ip.run"]
    RUN -->|"upload once"| IMGUR[("Imgur")]
    IMGUR -->|"public URL"| LENS["check_ip.lens<br/>Google Lens via SerpAPI"]
    LENS --> STORE[("results/check_ip/check_ip.db<br/>images · results · api_history")]
    LENS -->|"full payload"| RAW[["results/check_ip/api_raw/<br/>one file per call"]]
    STORE --> SCREEN["check_ip.screen<br/>ranked queue"]
    SCREEN --> SKILL["/check-ip skill<br/>browser pass"]
    SKILL -->|"proposed verdict only"| STORE
    STORE --> REVIEW["check_ip.review"]
    REVIEW --> TAB["app/tab_check_ip.py<br/>⚖️ check IP"]
    TAB -->|"Apply — the owner's decision"| STORE
```

## The one rule

The store has two sets of decision columns, and they never mix:

| columns | written by | meaning |
|---|---|---|
| `ok` `person` `chat` `report` `fixed` | **the owner only**, via the tab | the verdict and the follow-up trail |
| `screen_verdict` `screen_reason` `screened_at` `screen_source` `poster_url` | the `/check-ip` skill | a *proposal*, nothing more |

`ok = 0` means "this is an infringement, act on it". `ok = 1` means "reviewed,
acceptable use". The skill cannot write either — `check_ip.screen.record_verdict`
has no code path that touches them, and `tests/test_check_ip_store.py` fails if
one is ever added. That separation is what makes the skill safe to let loose on
a queue of 13,000 links: the worst it can do is propose something wrong.

## Commands

```powershell
# import the legacy Excel store (idempotent — run it twice, nothing changes)
& .\.venv\Scripts\python.exe -m check_ip.migrate --report    # reconcile, write nothing
& .\.venv\Scripts\python.exe -m check_ip.migrate

# reverse-image search — COSTS MONEY, one SerpAPI call per image
& .\.venv\Scripts\python.exe -m check_ip.run --dry-run       # what it would search
& .\.venv\Scripts\python.exe -m check_ip.run --limit 10      # a small live run
& .\.venv\Scripts\python.exe -m check_ip.run                 # everything that is due
& .\.venv\Scripts\python.exe -m check_ip.run --force         # ignore re-search windows

# the screening queue
& .\.venv\Scripts\python.exe -m check_ip.screen stats
& .\.venv\Scripts\python.exe -m check_ip.screen next --limit 20
& .\.venv\Scripts\python.exe -m check_ip.screen verdict --id 123 --verdict infringement --reason "…"
```

## Modules

| module | does |
|---|---|
| `db.py` | the store — connection, schema, duplicate marking, per-image tallies |
| `schema.sql` | three tables plus a `meta` bookkeeping row |
| `migrate.py` | one-shot Excel → SQLite import + payload extraction |
| `process.py` | pure helpers: post-date extraction, platform identification, row building |
| `imgur.py` | upload each illustration once, with backoff |
| `lens.py` | one Google Lens search per call, logged to the store |
| `run.py` | the search run |
| `screen.py` | the ranked queue and the verdict writer |
| `review.py` | everything the tab reads and writes |

## Configuration

A `check_ip` block in `config/config.json` (gitignored — see
`config_example.json` for the shape):

| key | meaning |
|---|---|
| `images_folder` | where the illustrations live |
| `legacy_metadata_folder` | the old Excel workbooks, read by `migrate.py` |
| `store_folder` | defaults to `results/check_ip` |
| `api_keys` | `serpapi_key`, `imgur_client_id`, `imgur_access_token` — a `${VAR}` placeholder falls back to that environment variable |
| `processing_thresholds` | re-search windows (see below) |
| `search_settings` | `do_exact_search` / `do_similar_search` |
| `screen_queue` | default platform and batch size for the skill |

**Credentials live in the repo-root `.env`**, which is gitignored (as is any
`.env` anywhere in the tree):

```
SERPAPI_KEY=…
IMGUR_CLIENT_ID=…
IMGUR_ACCESS_TOKEN=…
```

`config.json` keeps `${SERPAPI_KEY}`-style placeholders, which mean "read this
from the environment". Pasting a real value into `config.json` also works — it
is gitignored too — but `.env` keeps secrets out of the file that carries
ordinary settings.

## Re-search windows

An illustration already showing up on LinkedIn a lot is where new reuse keeps
appearing, so it is re-searched every **6 days** once it passes 30 LinkedIn
hits. Everything else waits **180 days**. `--force` ignores both.

## Gotchas

- **The store must stay on local disk.** `results/check_ip/` is repo-local and
  gitignored. Never move it to iCloud or another synced folder: SQLite's `-wal`
  and `-shm` files sync independently of the database and corrupt it.
- **A live run costs real money** — roughly one SerpAPI call per image, and all
  1,033 illustrations fall due together after a long gap. Always `--dry-run`
  first; it prints the exact call count.
- **The legacy payload archive is ~47% truncated.** Excel capped a cell at
  32,767 characters, so 4,397 of the 9,370 imported API responses are cut
  mid-JSON. Those are stored as `.json.truncated` and flagged
  `raw_truncated = 1` — don't parse them. Payloads written from now on are whole.
- **One URL can legitimately appear several times**: the exact-match and
  visual-match searches both return it, and a later run re-finds it. The
  identity of a row is `(local_image, found_link, match_type, search_date)`.
  Eleven such groups carry *conflicting* owner verdicts from different dates —
  collapsing them would destroy real decisions.
- **Only canonical rows are served and shown.** A URL matching several
  illustrations is judged once (`duplicate` 0 or 2), not once per illustration.
