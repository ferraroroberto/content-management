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
    subgraph S1[1. Bootstrap]
        A[bootstrap_chrome.bat<br/>kill+relaunch Chrome on :9222<br/>--user-data-dir=newsletter/chrome_user_data]
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
    S1 --> S2 --> S3 --> S4
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

That launcher runs `newsletter_pipeline.py`, which walks you through:

1. **Bootstrap Chrome** — kills all `chrome.exe`, relaunches with
   `--remote-debugging-port=9222 --user-data-dir=newsletter\chrome_user_data`.
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
launch_newsletter.bat --newsletter 057     # pre-fill the number
launch_newsletter.bat --skip-bootstrap     # reuse an already-up Chrome
launch_newsletter.bat --debug              # verbose everywhere
launch_newsletter.bat --days 7             # tighter normalise window
```

## Running steps in isolation

| Command | What it does |
|---|---|
| `python -m newsletter.bootstrap_session` | One-time Gmail / source login into the dedicated Chrome profile. |
| `python -m newsletter.dry_run --first-non-gmail-tab --no-write` | Archive ONE tab, log only, no Notion writes. |
| `python -m newsletter.dry_run --single-url <url>` | Pick a specific tab by URL substring. Add `--no-write` for read-only. |
| `python -m newsletter.pipeline` | Archive **every** eligible tab (dry-run). |
| `python -m newsletter.pipeline --live` | Archive every eligible tab, write + close. |
| `python -m newsletter.normalize_names --days 14 [--dry-run]` | Rewrite article titles to sentence case. |
| `python -m newsletter.normalize_url --days 14 [--dry-run] [--testing]` | Strip URL query params; `--testing` HEAD/GETs each cleaned URL. |
| `python -m newsletter.build_newsletter --newsletter 057` | Render HTML for newsletter 057 and copy the must-read line to clipboard. |

Add `--debug` to any of the above for verbose logs. All runs append to
`logs/newsletter_archive.log` (archive entry points) or stdout (the
others).

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
  session-stealing extensions). The bat always launches with
  `--user-data-dir=newsletter\chrome_user_data\` to work around this.
- The bat always kills every `chrome.exe` first (logging what it
  killed) because a single orphan from a parallel Playwright run will
  otherwise grab the binary and swallow the debug flag.
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

- `bootstrap_chrome.bat` — daily Chrome launcher (kill + relaunch on `:9222`).
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
- `dry_run.py` — single-tab entrypoint.
- `normalize_names.py` — article title sentence-case rewriter.
- `normalize_names_words.json` — sidecar: proper-name whitelist + special
  cases + common words.
- `normalize_url.py` — URL query-param stripper with preserve list.
- `build_newsletter.py` — HTML builder + must-read line copier.
