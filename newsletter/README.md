# newsletter — weekly newsletter pipeline

End-to-end orchestration for the weekly newsletter: archive the article
tabs in your real Chrome into Notion, normalise article titles to
sentence case, strip tracking params from URLs, then build the
ready-to-paste HTML for a specific newsletter number.

Tracks issues: ferraroroberto/content-management#17 (archive step) and
ferraroroberto/content-management#18 (full pipeline + monorepo migration).

## Workflow

```mermaid
flowchart LR
    subgraph S0[0. Schedule]
        Z[schedule_editions.py<br/>read every edition row<br/>-> max number + max date<br/>-> create the missing<br/>future rows, weekly cadence]
    end
    subgraph S1[1. Bootstrap]
        A[bootstrap_chrome.py<br/>targeted: reuse :9222 if up,<br/>else kill only the newsletter-profile<br/>Chrome and relaunch on :9222]
    end
    subgraph S2[2. Archive]
        B[connect_over_cdp] --> C[list tabs<br/>skip gmail/notion/...]
        C --> D[per tab: readability extract]
        D --> E[local-llm-hub<br/>classify + summarize]
        E --> F[cache lookup<br/>author + URL dedupe]
        F --> G[pick newsletter<br/>rollup < 8]
        G --> H[create Notion article<br/>+ close tab]
    end
    subgraph S3[3. Normalize]
        I[normalize_names<br/>sentence case + whitelist + spaCy] --> J[normalize_url<br/>strip tracking params]
    end
    subgraph S4[4. Build]
        K[input newsletter #] --> L[group by topic<br/>sort by star → niche → title]
        L --> M[render HTML<br/>results/newsletter/N{NNN}.html]
        M --> N[prompt must-read<br/>copy line to clipboard]
    end
    subgraph S5[5. Substack draft — optional]
        O[same grouped articles] --> P[native HTTP API<br/>cookie auth]
        P --> Q[private draft edition<br/>never published]
    end
    S0 --> S1 --> S2 --> S3 --> S4
    S4 -.-> S5
```

## One-time setup

1. Install dependencies (adds `readability-lxml`, `lxml_html_clean`,
   `rapidfuzz`, `spacy` if not already installed):
   ```powershell
   & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   & .\.venv\Scripts\python.exe -m spacy download en_core_web_sm
   ```
2. Make sure the local-llm-hub is running and reachable on
   `http://127.0.0.1:8000` with the `gemini_lite` route active.
3. Confirm the `newsletter_archive` block exists in `config/config.json`
   (template lives in `config/config_example.json`). Required keys:
   articles + connections + newsletter DB ids, LLM hub url + model,
   fuzzy threshold, category cap, topic→rollup map,
   `author_fallback_name` (e.g. `"(not classified)"`),
   `url_preserve_domains` (youtube / vimeo / twitter / x).
4. Make sure a connection named exactly **`(not classified)`** exists in
   the connections DB. It's the fallback used when the author can't be
   identified — required so the pipeline never invents people.
5. **Sign into Gmail in the dedicated newsletter Chrome profile**
   (one-time):
   ```powershell
   & .\.venv\Scripts\python.exe -m newsletter.bootstrap_session
   ```
   Opens Chrome against `newsletter/chrome_user_data/` (gitignored, same
   pattern as `planning/linkedin/chrome_user_data/`). Sign in, press
   Enter to save. Future runs reuse the session.

## Weekly run — the orchestrator

```cmd
launch_newsletter.bat
```

That launcher runs `newsletter_pipeline.py all` — the full interactive console
sequence:

1. **Bootstrap Chrome** — launches the dedicated newsletter Chrome on `:9222`
   *without touching your everyday browser.* If `:9222` is already up it reuses
   it (your tabs stay); otherwise it kills only the Chrome bound to
   `newsletter\chrome_user_data` (if any) and relaunches with
   `--remote-debugging-port=9222`.
2. **Wait** — you open the newsletter article tabs in that Chrome window
   (clicking links in Gmail is fine — they'll open there). Press Enter
   when ready.
3. **Archive** — every non-skipped tab is processed and closed on
   success. End state: only utility tabs remain.
4. **Wait** — press Enter when ready to normalise.
5. **normalize_names** then **normalize_url** — both default to the
   last 14 days, both write straight to Notion.
6. **Build** — prompts for the newsletter number (`057` or `N057`),
   queries the related articles, groups by topic, sorts (star desc →
   niche asc → title asc), writes HTML to
   `results/newsletter/N{NNN}.html`, opens it in the browser, then
   prompts you to pick the must-read topic (1/2/3) and copies the
   composed line to the clipboard.

CLI flags pass through to the orchestrator:

```powershell
launch_newsletter.bat --newsletter 057      # pre-fill the number
launch_newsletter.bat --debug               # verbose everywhere
launch_newsletter.bat --days 7              # tighter normalise window
```

## Running steps in isolation

The pipeline is split into independent, non-interactive subcommands (issue #59)
— the same ones the Streamlit app drives, so nothing is app-only:

| Command | What it does |
|---|---|
| `newsletter_pipeline.py schedule [--count N] [--target 8] [--dry-run] [--debug]` | Create the missing **future** newsletter rows in Notion so `archive` always has somewhere to file an article. |
| `newsletter_pipeline.py bootstrap` | Ensure Chrome is up on `:9222` (targeted — never kills the everyday browser). |
| `newsletter_pipeline.py archive [--debug]` | Archive every eligible open tab → Notion, write + close. |
| `newsletter_pipeline.py normalize [--days 14] [--debug]` | normalize_names + normalize_url. |
| `newsletter_pipeline.py build --newsletter 057 [--no-open] [--must-read 1\|2\|3 \| --no-must-read]` | Render HTML + write the topics sidecar. |
| `newsletter_pipeline.py create --newsletter 057 [--days 14]` | archive → normalize → build (non-interactive). |
| `newsletter_pipeline.py all [--newsletter 057] [--days 14]` | Full interactive console sequence (default when no subcommand). |
| `newsletter_pipeline.py substack-draft --newsletter 057 [--title T] [--subtitle S] [--must-read 1\|2\|3] [--delete-after]` | Create a **private** Substack draft edition from the same Notion content. Never publishes. |

Lower-level module entry points:

| Command | What it does |
|---|---|
| `python -m newsletter.bootstrap_chrome` | The targeted bootstrap itself (what `bootstrap_chrome.bat` wraps). |
| `python -m newsletter.bootstrap_session` | One-time Gmail / source login into the dedicated Chrome profile. |
| `python -m newsletter.dry_run --first-non-gmail-tab --no-write` | Archive ONE tab, log only, no Notion writes. |
| `python -m newsletter.dry_run --single-url <url>` | Pick a specific tab by URL substring. Add `--no-write` for read-only. |
| `python -m newsletter.pipeline` | Archive **every** eligible tab (dry-run). |
| `python -m newsletter.pipeline --live` | Archive every eligible tab, write + close. |
| `python -m newsletter.normalize_names --days 14 [--dry-run]` | Rewrite article titles to sentence case. |
| `python -m newsletter.normalize_url --days 14 [--dry-run] [--testing]` | Strip URL query params; `--testing` HEAD/GETs each cleaned URL. |
| `python -m newsletter.build_newsletter --newsletter 057` | Render HTML for newsletter 057 and copy the must-read line to clipboard. |
| `python -m newsletter.substack_draft --newsletter 057` | Create the private Substack draft edition directly. |

Add `--debug` to any of the above for verbose logs. All runs append to
`logs/newsletter_archive.log` (archive entry points) or stdout (the
others).

## Scheduling future editions (issue #230)

`archive` files each article against the first **future** newsletter row that
still has room for its topic (`notion_io.pick_newsletter`, which filters
`Date >= today`). When the buffer of future rows runs dry it aborts outright:

```
❌ No future newsletter has room for topic '<topic>' — stopping
```

`schedule` tops that buffer back up. It reads every row of the newsletter DB
once, takes the highest `number` and the highest `Date` **independently**, and
creates the rows that follow:

```powershell
& .\.venv\Scripts\python.exe newsletter_pipeline.py schedule --dry-run   # plan only, writes nothing
& .\.venv\Scripts\python.exe newsletter_pipeline.py schedule             # top up to 8 future editions
& .\.venv\Scripts\python.exe newsletter_pipeline.py schedule --count 4   # create exactly 4
```

- **Numbering** continues from the highest edition anywhere in the table, zero-padded to three digits (`N234`) — `build_newsletter.normalize_newsletter_number` rejects anything else, so an unpadded number would only fail later, at ④ Build. Taking the max number separately from the max date is what makes a duplicate impossible even if the two ever disagree.
- **Dating** carries the weekly cadence off the latest row, so the Saturday alignment is inherited rather than recomputed.
- **Idempotent.** With the buffer already at target it creates nothing, says so, and exits 0.
- New rows carry `number` and `Date` only — every other column on that DB is a rollup over the related `articles` DB, so they fill themselves in as articles are archived.
- The maximum is re-read inside the step, at write time. The ⓪ block in the app's 📰 tab only *pre-fills* the count from a cached read, so a stale caption can never produce a duplicate number.

## Notion field map

| Article DB field | Source |
|---|---|
| `article` (title) | readability `short_title()` (fallback: `<title>`); normalised to sentence case by `normalize_names` |
| `link` (url) | tab URL, cleaned of tracking params by `normalize_url` |
| `summary` (rich_text) | LLM 3-line plain text |
| `topic` (select) | LLM classifier (`personal development` / `innovation` / `leadership and management`) |
| `type` (select) | always `article` |
| `author or source` (relation → connections) | see *Author resolution* below |
| `news` (relation → newsletter) | first future newsletter where the per-topic rollup is `< 8` |
| page body | extracted article text as paragraph blocks (no images) |

| Newsletter rollup (per topic) | Cap |
|---|---|
| `n persdev` | 8 |
| `n innov` | 8 |
| `n leader` | 8 |

## Author resolution

The resolver in `author_resolver.py` follows this order:

1. **Single clean byline** found in the page (meta tag / OG / byline
   div): fuzzy-match against the connections cache
   (`rapidfuzz.token_sort_ratio >= 88`). If a match exists, use it.
   If not, **create a new connection** with that name and the article's
   topic (the byline is trusted because it came from the page itself).
2. **Multiple authors, missing byline, or any ambiguity** (`and` /
   `,` / `&` / `with` in the string): call Gemini-Lite via the LLM hub
   and ask it to identify the primary author OR the publishing
   organisation (Google, Anthropic, McKinsey, Microsoft, …). Verify the
   LLM's answer against the cache. If matched, use it.
3. If the LLM returns `UNKNOWN` or its answer doesn't match any
   connection, fall back to the connection named exactly
   **`(not classified)`** (configurable via `author_fallback_name`).
   The article still gets saved, just with the fallback author.

We **never** create a connection from LLM output — only from a real
byline. The fallback exists so the pipeline never invents people.

## Gotchas

- **Chrome 136+** silently refuses to bind `--remote-debugging-port`
  against the default profile dir (security policy change to block
  session-stealing extensions). Bootstrap always launches with
  `--user-data-dir=newsletter\chrome_user_data\` to work around this.
- Bootstrap is **targeted and idempotent** (issue #59, supersedes #57): it
  reuses an existing `:9222` if one is up, and otherwise kills **only** the
  Chrome whose command line carries `--user-data-dir=<newsletter profile>` (via
  `config.chrome_profile_lock.pids_holding_profile`) — never `taskkill /IM
  chrome.exe`. Your everyday browser is never touched. If a *non-debug* Chrome
  is holding the newsletter profile, relaunching it drops that window's open
  tabs (logins persist).
- The pipeline does **not** close Chrome when it disconnects — only the
  tabs whose articles processed successfully are closed.
- New connections are created with `name` + `topic` only. LinkedIn URLs
  are left empty for manual fill — auto LinkedIn search is deliberately
  out of v1 scope.
- URL canonicaliser (for dedupe) strips `utm_*` / `mc_*` / `_hsenc` /
  `_hsmi` / `ref` / `gclid` / `fbclid` / trailing slashes before
  comparing.
- Notion API is flaky under load; `notion_io._retry` does 2/4/8/16 s
  exponential backoff on transient errors (the four we've actually
  seen).
- Newsletter # in `build_newsletter` accepts `057` or `N057` — both
  normalise to `N057`.

## Files

- `bootstrap_chrome.py` — targeted, idempotent Chrome launcher on `:9222` (reuse-or-relaunch; never kills the everyday browser).
- `bootstrap_chrome.bat` — thin wrapper that runs `python -m newsletter.bootstrap_chrome`.
- `bootstrap_session.py` — one-time Gmail-login flow into the dedicated profile.
- `chrome_tabs.py` — CDP attach, list, skip filter, tab close.
- `extractor.py` — Playwright + readability-lxml + meta-tag fallback.
- `llm.py` — local-llm-hub `/v1/messages` wrapper.
- `classifier.py` — topic classifier with validation + fallback.
- `summarizer.py` — 3-line summarizer.
- `author_resolver.py` — byline / LLM-pick-primary / `(not classified)` fallback.
- `cache.py` — in-memory caches + URL canonicaliser + fuzzy name match.
- `notion_io.py` — DB read/write helpers with retry-with-backoff.
- `pipeline.py` — archive batch orchestrator.
- `schedule_editions.py` — future-edition scheduler: buffer state + the pure number/date sequence generator.
- `dry_run.py` — single-tab entrypoint.
- `normalize_names.py` — article title sentence-case rewriter.
- `normalize_names_words.json` — sidecar: proper-name whitelist + special
  cases + common words.
- `normalize_url.py` — URL query-param stripper with preserve list.
- `build_newsletter.py` — HTML builder + must-read line; writes the `N{NNN}.topics.json` sidecar the app's must-read picker reads.
- `substack_draft.py` — pushes the same grouped article lists into a private Substack draft edition over the native HTTP API.
- `triage/gmail.py` — read-only Gmail label ingestion + link extraction / redirect decoding (adapter over the vendored `gmail_readonly/`).
- `triage/history.py` — 54-week ground-truth dataset (Notion positives ⋈ Gmail offered links) + `stats.md`.
- `results/newsletter/triage/criteria.json` — machine-readable selection criteria (twin of `docs/newsletter-triage-criteria.md`), rebuilt by `python -m newsletter.triage.criteria`. Gitignored — a build artifact merging the hand-written `RULES` in `triage/criteria.py` with data priors that carry real sender addresses.
- `results/newsletter/triage/overrides.json` — owner-maintained sender decisions (tier `never|rarely|usually|always|review`, e.g. paywalled publications); applied before the data priors, updated by the feedback loop. Gitignored for the same reason as `triage/lessons.json` — regenerated/appended locally, carries real sender addresses.

## Triage — Gmail history + criteria (issue #210, Step 1/4)

The weekly inbox review (label `newsletters`, ~80 emails/week) is being
automated in four steps (#210 history + criteria, #211 engine + report, #217
store + control-panel tab, #212 Chrome hand-off + watermark). Step 1 builds the
**ground truth** the later ranker is scored against, under `newsletter/triage/`:

- `gmail.py` — read-only Gmail adapter over the vendored `gmail_readonly/` +
  `google_oauth_common/` packages (byte-for-byte from whatsapp-radar — never
  edit them here; extend in this adapter). Raw MIME walk, `<a href>` extraction,
  noise filter (unsubscribe / share / social / app-store / …), tracking-redirect
  decoding: local first (ConvertKit/Kit/HBR base64 path, Substack `redirect/2/`
  JSON, McKinsey host rewrite, Substack `post_id` dedupe), then a bounded, cached
  HTTP hop (`results/newsletter/triage/redirects.json`) for opaque redirectors
  (Substack `redirect/`, beehiiv, SendGrid, Mailchimp, ActiveCampaign, …).
- `history.py` — pulls the label over N weeks (incremental, HTML cached gzipped
  under `results/newsletter/triage/history/raw/`), loads the Notion positives
  (every article with an edition relation: topic, author, star, must-read from
  the edition title), joins positives → source email (canonical URL → Substack
  slug → fuzzy anchor-vs-title), and writes `stats.json` + `stats.md` (per-sender
  hit-rates, per-edition caps observed, topic/star/must-read patterns, lag,
  unmatched list).
- `criteria.json` — the machine-readable criteria the Step-2 ranker consumes;
  the human-readable rationale lives in
  [`docs/newsletter-triage-criteria.md`](../docs/newsletter-triage-criteria.md).

| Command | What it does |
|---|---|
| `python -m newsletter.triage.history` | Full build with `newsletter_triage.history_weeks` (54) and `redirect_budget_history`. |
| `python -m newsletter.triage.history --no-gmail --reextract --budget 0` | Re-run extraction + join + stats from the cached HTML after a rule change (redirects re-applied from the cache, no new network). |
| `python -m newsletter.triage.history --limit 150 --budget 100` | Smoke test. |
| `python -m newsletter.triage.criteria` | Rebuild `criteria.json` from `stats.json` + `overrides.json`. |

### Weekly engine (Step 2/4, issue #211)

`run.py` turns one review window into one edition-sized shortlist — the owner's
cadence rule: the run covers last watermark → now (normally Saturday → Friday)
and fills **one** edition (8/8/8); a backlog of N weeks is split into N 7-day
windows, oldest first, one report each. Nothing is written to Notion, Gmail or
Chrome (that is Step 3).

Pipeline per window: emails → non-noise links (decoded / resolved) → drop
already-in-Notion → sender/domain priors (`criteria.json` + `overrides.json`) →
**stage A** batched metadata scoring via the hub (`llm_model`, default
`claude_haiku`) → fetch + **stage B** content scoring for the top-K
(`stage_b_top_k`, 90) with paywall detection (`fetch.py`) → vetoes (`tier: never`,
paywalled, promo) → caps (HBR ≤ 3, same author ≤ 2, same domain ≤ 3, one pick
per email except digests) → 8 per topic + ⭐/🏆 suggestions →
`results/newsletter/triage/triage-<start>_<end>.md`.

| Command | What it does |
|---|---|
| `python -m newsletter.triage.run` | Live: window from `state.json → reviewed_until` (else today−7) to today, one report per 7-day window. |
| `python -m newsletter.triage.run --since 2026-08-08 --until 2026-08-22` | Explicit range (split into windows). |
| `python -m newsletter.triage.run --backtest N224,N225,N226,N227` | Offline replay from the history cache: precision of the shortlist vs real picks, recall of the edition's picks sourced in the window. |
| `python -m newsletter.triage.run --no-llm` | Rule-only report (sender/domain priors) when the hub is down. |
| `python -m newsletter.triage.run --force` | Replace a run already stored for the same window (without it the engine refuses with exit 3 — the control panel's override toggle passes it). |
| `python -m newsletter.triage.feedback <report.md>` | Ingest a reviewed markdown report (tick = yes) for the window in its name → `triage_decisions` + sender tiers in `overrides.json`. The control panel's Apply is the same path. |

First dry run (2026-08-21, `claude_haiku`, 2-week windows ending 7 days before
each edition): N224 precision 54 % / recall 16 %, N225 54 % / 26 %, N226 62 % /
50 %, N227 42 % / 29 % (+runners-up 21–56 %). Precision = share of the 24
suggestions that were real picks in some edition; recall = share of the
edition's picks sourced in the window that made the shortlist. Misses are mostly
caps doing their job (HBR ≤ 3 where the edition had 4, author ≤ 2) and picks
ranked just below the fold — the feedback loop and wider stage-B depth are the
levers.

The report is the review surface: shortlist per topic with checkboxes (ticked =
suggested), runners-up, a "new senders — set a tier" list, then every email in
inbox order with every link and its verdict (`selected / runner-up / candidate /
low / vetoed / duplicate / unknown`) — an unfetchable or unscorable link is
`unknown`, never "skipped". Skill: `/newsletter-triage`
(`.claude/skills/newsletter-triage/SKILL.md`, mirrored in `.agents/skills/`).

### Store + control-panel tab (Step 3/4, issue #217)

Every run and every owner decision lives in Supabase — the repo's store, same
project DB as reporting + engagement — in `triage_*` tables
(`newsletter/triage/schema.sql`, applied **once** in the Supabase SQL editor,
idempotent, and carrying the RLS + `anon_*` policy stanza every public table needs
for the daily drift check; `python -m newsletter.triage.db ensure-schema` probes and
prints the recipe if missing). `db.py` owns every query (supabase-py from
`config.supabase`, key fallback service_role → key → anon like engagement).
Markdown reports and caches stay in gitignored `results/newsletter/triage/`.

| Table | Holds |
|---|---|
| `triage_runs` | one row per (window, kind live/backtest) — status, model, stats, report path; re-running a stored window **replaces** it (needs `--force`) |
| `triage_emails`, `triage_candidates` | the run's inbox + every link with scores, verdict, stage-A/B JSON and the engine's suggestion (pick / runner, rank, ⭐, 🏆) |
| `triage_decisions` | the owner's ticks, keyed by **window + canonical URL** — they survive a re-run and pre-fill the new table; single source for the sender-tier tally (`feedback.jsonl` retired); picks from earlier windows are a dedupe set for the next run (`already picked <window>`, before the archive step puts them in Notion) |
| `triage_reviews` | the per-window review comment ("why these choices") |
| `triage_lessons` | criteria notes proposed by the hub (`lessons_model`, `claude_sonnet`) from comment + disagreements; accepted ones are exported to the gitignored `newsletter/triage/lessons.json` (regenerated on every accept/save; the store is the source of truth) and appended to the scoring prompt brief |
| `triage_editions`, `triage_picks` | the 54-week knowledge base from the history files (`python -m newsletter.triage.db import-history`) |

Control panel → **🧭 triage**: pick a closed Saturday→Saturday week (or the
open week / custom dates) → ▶ run with the live log (slot `triage`; a stored
week only re-runs behind the *override* toggle) → choose a stored run → the
editable table (`review.py`): suggestions pre-ticked (weak fills unticked),
tick / untick / promote from runners-up or the whole scored long tail, ⭐ / 🏆,
per-row notes — **nothing is written until ✅ Apply**, which saves every row's
decision, updates sender tiers from the whole history, stores the comment and
advances `state.json → reviewed_until` (live runs only). 🧠 **Distil lessons**
asks the hub for ≤ 3 generalisable notes you accept one by one; new senders get
a manual tier (`overrides.json`, `source: manual`) from the same tab; the
markdown report renders in an expander (one source, no second copy).

| Command | What it does |
|---|---|
| `python -m newsletter.triage.db ensure-schema` / `stats` | Probe the tables / row counts. |
| `python -m newsletter.triage.db import-history` | Load `triage_editions` + `triage_picks` from `results/newsletter/triage/history/`. |
| `python -m newsletter.triage.run --backtest N224,N225,N226,N227 --force` | Store the four backtests with the real picks as decisions (the first knowledge base). |
| `python -m newsletter.triage.lessons --run <id>` / `--accept 1,2` / `--list` | Distil / accept / list lessons from the CLI. |
| `python -m newsletter.triage.handoff --run <id>` | Hand-off preview (issue #212): the ticked URLs + the `until <newest e-mail> > included` watermark line — no writes. |
| `python -m newsletter.triage.handoff --run <id> --open` | `ensure_chrome()` then one tab per ticked URL in the `:9222` Chrome (already-open tabs skipped) → run ② Archive as usual. |
| `python -m newsletter.triage.handoff --run <id> --mark-reviewed` | Exactly one watermark comment on the `newsletters processing` task page (`notion_task_page_id`) + `state.json → reviewed_until`. |

The tab's **🌐 Open ticked in Chrome** / **📝 Mark reviewed in Notion** /
**👀 Preview hand-off** buttons run the same commands in the `triage` log
panel (enabled once a review is applied; mark-reviewed only for live runs).

### Gmail one-time setup

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt   # google-api-python-client, google-auth-*
```

Copy `credentials.json` + `token.json` from the sibling repo's `auth/gmail/`
into this repo's gitignored `auth/gmail/` (same OAuth client, own token file
refreshed independently — the pattern grocery-shopping-automation uses). If the
token is missing or revoked, re-consent (opens a browser, requests
`gmail.readonly` only):

```powershell
& .\.venv\Scripts\python.exe -m gmail_readonly.oauth --credentials auth\gmail\credentials.json --token auth\gmail\token.json
```

Gotchas: an OAuth app left in *Testing* issues refresh tokens that expire after
7 days — the client is in production. Refresh tokens also die on grant
revocation, a Google password change, or 6 months unused; recovery = remove the
grant at `myaccount.google.com/connections`, delete only `auth/gmail/token.json`,
re-run the command above. Vendored-package drift check:
`git diff --no-index -- E:\automation\whatsapp-radar\gmail_readonly gmail_readonly`
(and the same for `google_oauth_common`) — empty output means byte-identical.
The portable contract test `tests/test_gmail_readonly.py` is pytest-style
(`& .\.venv\Scripts\python.exe -m pytest tests\test_gmail_readonly.py`).

## Substack draft edition

The last step of the weekly run used to be manual: open
`results/newsletter/N{NNN}.html`, copy it, paste into the Substack editor.
`substack-draft` does that over Substack's native HTTP API instead — no browser,
no DOM selectors.

```powershell
& .\.venv\Scripts\python.exe newsletter_pipeline.py substack-draft --newsletter 057 --must-read 1
```

It reads exactly what `build` reads (the Notion articles + newsletter DBs) and
reuses the same grouping/sorting, so the draft and the HTML can't drift. Each
topic becomes an `<h2>`-equivalent heading followed by a bullet list of linked
article titles; `--must-read N` prepends the composed must-read line as the
opening paragraph.

- **It never publishes.** The draft is private and emails no one — publishing
  stays a deliberate action in the Substack editor. There is no `--confirm`
  flag here by design (`planning/substack/api_create.py` is the manual path that
  can publish).
- **Not part of `create` / `all`.** It writes to an external platform, so it
  stays an explicit, separately re-runnable step.
- `--delete-after` creates and immediately deletes the draft — the smoke test
  for "is my cookie still good and does the body build cleanly?".
- Needs the harvested API session (`planning/substack/api_session.json`, ~89-day
  cookie). On expiry the step exits `2` with a single line telling you to re-run
  `python -m planning.substack.extract_session`.

Article titles are inserted as **literal text**, never parsed as markdown —
titles scraped from arbitrary sites routinely contain `*`, `[` or backticks that
markdown parsing would mangle.
