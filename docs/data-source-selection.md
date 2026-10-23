# Choosing the reporting data source — RapidAPI vs Playwright

Each `<platform>_<data_type>` block in `config/config.json` carries a
`"source"` key. The dispatcher in
`reporting/social_client/social_api_client.py::get_api_data` reads it and
either makes the HTTP call (`"rapidapi"`), delegates to the matching
`reporting.scrape_client.<platform>.fetch_<data_type>` (`"playwright"`), or
delegates to `reporting.scrape_client.<platform>_native.fetch_<data_type>`
(`"native"` — the platform's own HTTP API with cookie auth, no browser).

**Substack native API:** both Substack blocks can run natively —
`substack_profile` (follower count, one authenticated GET) and `substack_posts`
(note engagement, a couple of GETs instead of a feed scroll plus one page load
per note). The note path was verified record-for-record against the Playwright
scrape on the live account — 10/10 identical, 0.9 s vs 37.0 s. Both need the
one-time cookie harvest (`python -m planning.substack.extract_session`, re-run
~quarterly when the cookie expires); flip either back to `"playwright"` to use
the browser scrape. See [`substack-native-api.md`](substack-native-api.md)
and [`../planning/substack/README.md`](../planning/substack/README.md). `"native"` is
Substack-only for now; other platforms use rapidapi/playwright.

Note *posting* has its own flag, `substack.note_source` (`"playwright"` default
/ `"native"`), because it is a **write** to a public platform rather than a
read. The native backend publishes the daily Note over the same HTTP API and
records the permalink of the note it just created, instead of re-reading the
profile and taking whatever is topmost. Video Notes are covered too — the same
`substack.note_source` flag selects `post_substack_video_note.py`'s native
branch, with Playwright as the one-key rollback.

The downstream pipeline is source-agnostic — `data_processor` reads the
JSON envelope from `reporting/results/raw/<platform>_<data_type>_<YYYY-MM-DD>.json`
and uses the matching `<key>` mapping in `config/mapping.json`, falling
back to `<key>_playwright` automatically (via
`get_alternative_mapping_keys`). So you can flip one platform at a time
with no other change.

## Per-source trade-offs

| | RapidAPI (`"source": "rapidapi"`) | Playwright (`"source": "playwright"`) |
|---|---|---|
| **Cost** | Monthly subscription per provider | Free — drives the existing logged-in Chrome profile |
| **Speed** | ~2s per endpoint | ~5–30s per endpoint (browser launch + scroll + read; one open per endpoint) |
| **Session** | API key in config | Persistent profile at `planning/<platform>/chrome_user_data/`; re-login via `python -m planning.<p>.bootstrap_session` if Chrome forgets the session |
| **Breakage mode** | Provider may rate-limit, change pricing, deprecate endpoints | Selectors break when the platform's DOM changes — patch the per-platform module under `reporting/scrape_client/` |
| **Site coverage** | 1:1 with provider's API | Whatever's visible in the logged-in browser; see [`playwright-social-scraping.md`](playwright-social-scraping.md) for per-platform quirks |

## Flipping a platform

Edit `config/config.json` and set `source` on the two endpoints for that
platform (the `_profile` and `_posts` block):

```jsonc
"linkedin_profile": {
  "source": "playwright",           // ← was "rapidapi"
  "api_url": "...",                 // RapidAPI keys left in place for instant rollback
  ...
},
"linkedin_posts": {
  "source": "playwright",
  ...
}
```

Then run just that platform to validate:

```powershell
& .\.venv\Scripts\python.exe -m reporting.social_client.social_api_client `
    --platform linkedin_profile --no-skip --debug
& .\.venv\Scripts\python.exe -m reporting.social_client.social_api_client `
    --platform linkedin_posts --no-skip --debug
```

The new envelope lands in `reporting/results/raw/linkedin_*_<today>.json`.
Re-run `reporting_pipeline.py` (or just `data_processor`) and confirm the
Notion row matches what you expect.

## Rolling back to RapidAPI

If a platform's Playwright scraper breaks the day before you need a clean
report — flip its `"source"` back to `"rapidapi"` in `config/config.json`,
delete the broken file from `reporting/results/raw/`, and re-run the
pipeline. The RapidAPI configuration (api_url / api_key / api_host / etc.)
is intentionally left untouched in the config — flipping the key is the
only change. Keep the RapidAPI subscription active for at least one full
reporting cycle after the Playwright cutover to retain this rollback
escape hatch.

## Cancelling the RapidAPI subscription

Only after a sustained period (≥ 1 week) of clean Playwright runs:

1. Confirm every endpoint's `"source"` has been `"playwright"` continuously.
2. Cancel the provider subscription(s) on RapidAPI.
3. Open a follow-up issue to *retire* the now-dead RapidAPI integration —
   strip the `api_url` / `api_key` / `api_host` / `querystring` fields from
   `config.json` and remove the HTTP path from `social_api_client.py`. Do
   NOT do this in the same PR as the Playwright cutover — the dual-path
   architecture is the whole point of the rollback safety net.
