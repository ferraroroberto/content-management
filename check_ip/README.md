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
| `screen_verdict` `screen_reason` `screened_at` `screen_source` `poster_url` `screen_promotional` `screen_altered` | the `/check-ip` skill | a *proposal*, nothing more |

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

# retire the stored 'Similar Match' rows (issue #292) — flags them, deletes nothing
& .\.venv\Scripts\python.exe -m check_ip.migrate --retire-similar

# the screening queue
& .\.venv\Scripts\python.exe -m check_ip.screen stats
& .\.venv\Scripts\python.exe -m check_ip.screen next --limit 10
& .\.venv\Scripts\python.exe -m check_ip.screen verdict --id 123 --verdict infringement --reason "…"
& .\.venv\Scripts\python.exe -m check_ip.screen verdict --id 123 --verdict infringement --promotional --altered --reason "…"
```

## Only exact matches count

Google Lens returns two kinds of hit. An **exact match** is the same image file
— that is evidence of reuse. A **similar match** is Lens saying "this looks
like your picture", which for minimalist business illustration means another
artist drawing buckets, staircases and book piles in the same idiom. It is not
reuse and it never was: of 1,277 manual decisions over ten months, **zero** are
on a similar match, and every one the skill ever screened came back `unclear`.

Worse, the category is actively dangerous. Handed ten lookalikes from one
illustrator, the screener built a theory of a serial infringer and re-read its
own earlier verdicts to fit it — nine severity-3 accusations against a peer,
all reverted. Removing the category removes the exposure
([#292](https://github.com/ferraroroberto/content-management/issues/292)).

So: `MATCH_TYPES` no longer maps the `visual_matches` section, a live run skips
that search rather than paying for it, `next_batch` filters on
`match_type = 'Exact Match'`, and the 139,874 stored similar matches are
**retired, not deleted** — `results.retired = 1`, set by
`migrate --retire-similar`, reversible with one `update`. Each of those rows
cost a SerpAPI call and could not be re-derived without paying again.

A retired row is out of the queue, out of the tab and out of every `screen
stats` total except `retired`, which prints its reason next to it. A row
carrying an owner annotation is **never** retired, so no decision can vanish
from the tab — there are none today, and the guard is what keeps it that way.

## What counts as credit

**The post has to mention me** — my name, a tag, or a link to the original.
That is the whole test, and it is easy to get backwards: a visible
`ROBERTOFERRARO.ART` watermark is **not** credit on its own. An intact
watermark with no mention anywhere is still an infringement; the watermark's
*absence* is what counts against a post, not its presence in its favour.

Two flags record what makes an infringement worse, and `db.severity()` ranks
them so the tab and the queue agree on what "worst" means:

| severity | meaning |
|---|---|
| 0 | `acceptable`, or `unclear` — nothing to act on |
| 1 | no mention anywhere |
| 2 | …and either a self-promotional CTA (`screen_promotional`) or the image edited (`screen_altered`) |
| 3 | no mention, self-promotion **and** the watermark removed — plain stealing |

## How the queue ranks

Posters holding many pending rows come first: one conversation settles several
findings, while the large majority of posters appear exactly once. `poster_key`
is derived from `found_link` so this works *before* anything is screened.

`screen_queue.exclude_posters` drops accounts that are my own. Without it my
own handle tops the ranking by a wide margin — 491 pending rows against 78 for
the largest genuine reuser — and the queue serves my own posts first.
`screen stats` prints the exclusion so a checkout missing it is visible rather
than silently wrong (`config.json` is gitignored).

## Modules

| module | does |
|---|---|
| `db.py` | the store — connection, schema, duplicate marking, per-image tallies, retirement |
| `schema.sql` | three tables plus a `meta` bookkeeping row |
| `migrate.py` | one-shot Excel → SQLite import + payload extraction; also the store's bulk-update lane (`--retire-similar`) |
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
| `search_settings` | `do_exact_search` / `do_similar_search` — leave the latter `false`; a run warns and skips it rather than paying for results it would discard (#292) |
| `screen_queue` | default platform, batch size, and `exclude_posters` (accounts that are my own) |

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
  visual-match searches both returned it, and a later run re-finds it. The
  identity of a row is `(local_image, found_link, match_type, search_date)`.
  Eleven such groups carry *conflicting* owner verdicts from different dates —
  collapsing them would destroy real decisions. `match_type` stays in that
  key even though only one kind is ingested now: 139k stored rows depend on it
  to round-trip.
- **Retired is not deleted.** `results.retired = 1` hides a row from the queue
  and the tab; the row, its verdict and its history all stay. `update results
  set retired = 0 where match_type = 'Similar Match'` brings them all back.
- **Only canonical rows are served and shown.** A URL matching several
  illustrations is judged once (`duplicate` 0 or 2), not once per illustration.
