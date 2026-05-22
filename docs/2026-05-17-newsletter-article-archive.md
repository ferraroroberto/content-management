# Newsletter / article archive automation

**Issue:** #17

## What was done
New `archive/newsletter/` package that closes the last manual step in the
weekly content workflow. Walks the open article tabs in a CDP-attached real
Chrome, archives each one to Notion, and closes the tab on success. End state
of a run: only Gmail (and other skipped utility tabs) remain.

Per-tab pipeline (`pipeline.process_url`):
1. Skip URLs that contain `mail.google.com`, `notion.so`, `chrome://`,
   `about:`, `localhost`.
2. Extract title + body text + author byline with Playwright +
   `readability-lxml`. OpenGraph / standard meta tags are the byline
   fallback.
3. Call the local-llm-hub (`gemini_lite`, `/v1/messages`) for the 3-line
   summary and the topic (`personal development` / `innovation` /
   `leadership and management`). Classifier validates the response with one
   retry and falls back to `personal development` if it stays off-spec.
4. Resolve the author against an in-memory cache of all Notion connections:
   1. **Single clean byline** → exact then `rapidfuzz.token_sort_ratio ≥ 88`.
      Hit → use it. Miss → CREATE the connection (real byline = trusted).
   2. **Multi-author / missing byline / ambiguity** → ask the LLM to pick
      the primary author (person or publishing org). Verify the LLM's
      answer against the cache. Match → use it. Miss → fall back to the
      `(not classified)` connection.
   3. We never create connections from LLM output, only from a real byline
      — the fallback exists so the pipeline cannot invent people.
5. Pick the newsletter: query newsletter DB filtered to `Date >= today`
   sorted ascending, take the first row where the per-topic rollup
   (`n persdev` / `n innov` / `n leader`) is `< 8`. Re-queried before
   every article so the rollups stay fresh after the previous insert.
6. Skip duplicates: canonicalize the URL (drop `www.`, fragment,
   `utm_*`/`mc_*`/`_hsenc`/`_hsmi`/`ref`/`gclid`/`fbclid`) and check
   against the cached article-URL set.
7. Create the Notion article page with title, link, summary, topic,
   author relation, newsletter relation, `type = article`, and the body
   text as paragraph blocks (chunked to respect Notion's 2000-char rich
   text and 100-block limits).
8. Close the tab on success. Leave open on failure.

## Files
- `archive/__init__.py`
- `archive/newsletter/__init__.py`
- `archive/newsletter/README.md` — workflow, setup, CLI, gotchas
- `archive/newsletter/bootstrap_chrome.bat` — kill-all-chrome + relaunch on
  `:9222` with the dedicated `--user-data-dir`, polled until responsive
- `archive/newsletter/bootstrap_session.py` — one-time Gmail login flow,
  mirrors `planning/linkedin/bootstrap_session.py`
- `archive/newsletter/chrome_tabs.py` — CDP attach / list / skip filter /
  tab close
- `archive/newsletter/extractor.py` — Playwright + readability-lxml + meta
  tag author fallback + byline-div heuristic
- `archive/newsletter/llm.py` — `requests` wrapper for the hub's
  `/v1/messages` (Anthropic shape)
- `archive/newsletter/classifier.py` — 3-topic classifier, one retry,
  configurable fallback
- `archive/newsletter/summarizer.py` — 3-line plain-text summary
- `archive/newsletter/author_resolver.py` — byline / LLM-pick-primary /
  fallback resolver
- `archive/newsletter/cache.py` — in-memory cache, URL canonicaliser,
  `rapidfuzz` name match
- `archive/newsletter/notion_io.py` — paginated DB iterator with
  exponential-backoff retry on transient Notion errors, newsletter picker,
  connection + article writers
- `archive/newsletter/pipeline.py` — batch orchestrator over every
  non-skipped tab
- `archive/newsletter/dry_run.py` — single-tab entrypoint
  (`--single-url` / `--first-non-gmail-tab` / `--no-write` /
  `--keep-tab`)

## Configuration
New section in `config/config.json` (and `config_example.json`):
```jsonc
"newsletter_archive": {
  "articles_db_id":     "67fbcee66711465c852ebf97303787a3",
  "connections_db_id":  "33a1990d72f949f0983acd55ccd15724",
  "newsletter_db_id":   "71fed31953b84183a9e77c48493bf9f4",
  "chrome_debug_port":  9222,
  "skip_url_substrings": ["mail.google.com", "notion.so", "chrome://", "about:", "localhost"],
  "llm_hub_base_url":   "http://127.0.0.1:8000",
  "llm_model":          "gemini_lite",
  "fuzzy_author_threshold": 88,
  "newsletter_category_cap": 8,
  "topic_to_rollup": {
    "personal development":      "n persdev",
    "innovation":                "n innov",
    "leadership and management": "n leader"
  },
  "author_fallback_name": "(not classified)"
}
```

## Dependencies added
`requirements.txt`: `readability-lxml`, `lxml_html_clean`, `rapidfuzz`.

## Validation
- `python -m py_compile` on all new modules — clean.
- Smoke-tested URL canonicaliser (strips `utm_*` + `triedRedirect` +
  `www.` + trailing slash; preserves meaningful params), name normaliser
  (lowercase + accent strip + whitespace collapse).
- Dry-run (`--no-write`) on `https://elenaverna.com/p/ic-work-is-the-new-career-flex`
  ran clean three times: extracted title + body + byline, classified
  topic, generated 3-line summary, identified target newsletter `N215`,
  flagged Elena Verna as a new connection.
- Live run (`--single-url ... ` without `--no-write`) created the article
  page + the Elena Verna connection in Notion and closed the tab — both
  rows verified via direct Notion API query.

## Gotchas captured
- **Chrome 136+** silently refuses to bind `--remote-debugging-port`
  against the default profile dir. We launch with an explicit
  `--user-data-dir=archive\newsletter\chrome_user_data\` to work around
  this. The bat also kills every `chrome.exe` first (logging what it
  killed) because a single orphan from a parallel Playwright run will
  otherwise grab the binary and swallow the debug flag.
- Notion's public API throws transient `temporarily unavailable` /
  `timeout` / `internal_server_error` errors under load. The retry
  wrapper in `notion_io._retry` applies 2/4/8/16 s exponential backoff
  and gives up after the fourth attempt.
- Windows console default `cp1252` crashes on emoji-bearing log
  messages. The two entrypoints reconfigure stdio to UTF-8 before
  configuring logging.
- Child-module loggers (`newsletter_archive.notion_io`,
  `.author_resolver`, …) propagate to the package-root `newsletter_archive`
  logger, so both entrypoints call
  `setup_logger("newsletter_archive", ...)` to get a single shared file
  handler.

## Deferred / future
- LinkedIn auto-search for newly-created authors.
- Auto-creating a new newsletter row when every visible future newsletter
  is full (today we warn and stop).
- Pinning LLM `temperature = 0` for reproducible topic classification
  (today the same article can be classified differently across runs).
