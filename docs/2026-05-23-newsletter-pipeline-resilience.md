# Newsletter pipeline — resilience against LLM-hub failures

**Issue:** #24

## What was done

Hardened `newsletter/pipeline.py`'s `run_batch` against the LLM hub being slow, wedged, or down. The trigger was the 2026-05-22 run: the local hub (model `gemini_lite`) wedged partway through, and from that point every article failed with a 120 s `ReadTimeout`. The batch kept marching tab by tab — roughly 24 remaining tabs × 120 s ≈ 48 minutes of dead waiting — left every failed tab open, created nothing, and printed no summary. Five changes now make the pipeline fail fast and informatively, and tolerate transient blips.

### 1 — Retry transient errors in `llm.call`

`llm.call` now wraps its `requests.post` in a retry loop: on `requests.ReadTimeout` / `requests.ConnectionError` it retries up to 2 times with exponential backoff (2 s, 4 s) before re-raising, so one slow blip no longer discards a fully-extracted article. Each attempt keeps the full 120 s timeout. Genuine HTTP error responses are not retried.

### 2 — Pre-flight hub health check

New `llm.health_check` makes one tiny generation call (`max_tokens=4`, 30 s timeout, no retry) and returns a bool. `run_batch` calls it immediately after loading config — before the slow Notion cache hydration (minutes) and the Chrome connection — so a dead or unresponsive hub aborts the run in seconds instead of after the first full-length article timeout.

### 3 — Skip empty-body articles

`process_url` now checks the extracted body length against `min_body_chars` (default 200). A binary PDF or other page that extracts to a near-empty body can't be classified or summarised, so it logs a warning, skips the LLM steps, leaves the tab open, and returns — counted as a skip, not a failure.

### 4 — Circuit-breaker + end-of-run summary

`run_batch` tracks `archived` / `skipped` / `failed` counts plus a consecutive-failure counter. After `consecutive_failure_limit` consecutive tab failures (default 3) it aborts the loop with an explicit message and returns a non-zero exit code; a success or a benign skip resets the counter. Every run now ends with a `📊 N archived, N skipped, N failed` line, and when there are failures it lists the failed URLs so they are easy to re-open in Chrome for the next run.

### Bundled fix — Notion rich-text chunking miscounts emoji

Surfaced once the run got far enough to write articles: Notion rejected pages with `rich_text...content.length should be ≤ 2000, instead was 2001`. `_chunk_rich_text` in `newsletter/notion_io.py` sliced the body by Python `len()` (code points), but Notion's 2000-char limit counts UTF-16 code units — an emoji or other astral character is one code point but two UTF-16 units, so a 2000-char chunk holding one emoji reached 2001 units server-side. The chunker now sizes segments by UTF-16 width, so emoji-bearing bodies and summaries split correctly.

## Files modified

- `newsletter/llm.py` — retry loop in `call`; new `health_check` helper.
- `newsletter/pipeline.py` — empty-body skip in `process_url`; pre-flight check, circuit-breaker, counters and summary in `run_batch`.
- `newsletter/notion_io.py` — `_chunk_rich_text` now chunks by UTF-16 code-unit width (bundled fix; see above).
- `config/config.json` — added `consecutive_failure_limit` (3) and `min_body_chars` (200) under `newsletter_archive`.
- `config/config_example.json` — mirrored the two new keys.

## Validation

- `py_compile` on `newsletter/llm.py` and `newsletter/pipeline.py` — clean.
- Both `config/config.json` and `config/config_example.json` re-parsed as valid JSON after the edits.
- `llm.health_check` exercised directly against the live hub (`claude_sonnet`) — returns `True` for the running hub and `False`, without raising, for a dead endpoint.
- `_chunk_rich_text` checked against plain, boundary, and emoji-dense inputs — no chunk exceeds 2000 UTF-16 units and every split rejoins losslessly, including the exact 1999-chars-plus-one-emoji case that previously produced a 2001-unit chunk.

There is no automated test suite under `newsletter/`; `newsletter.dry_run` runs a single tab through `process_url` directly and does not go through `run_batch`, so the circuit-breaker and end-of-run summary are covered by compile-check and review rather than an end-to-end run.
