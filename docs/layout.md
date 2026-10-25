# Layout

This is a multi-tool `pipeline-cli` repo: each tool is one root package, and `.fleet.toml` declares every one in `extra_roots`. The structure ratchet (`scripts/structure_ratchet.py`, config in `.fleet.toml`) checks that the tracked root folders match `shape` + `extra_roots`, and that no file passes 1,000 lines, no function 100 lines and no directory 40 modules above `.structure-ratchet-baseline.json`. A new tool is a new root package, declared in `extra_roots` with a line here; new code inside a tool goes in its existing package, in a subpackage once a directory nears 40 modules.

## Root folders

The preset folders:

- `tests/` — the unittest + pytest-style suite. Flat and baselined; `tests/ratchets/` holds the gate-wiring tests, a `notion` subfolder the Notion helper tests, and any other new test goes in a subfolder too, since the flat `tests/` is at its baselined size.
- `scripts/` — the vendored ratchets (`*_ratchet.py`, never edited) and the gate wrappers that map their exit codes (`*_ratchet_gate.py`).
- `config/` — `config.json`, `mapping.json`, logging, Chrome launch and shared helpers; `config/doc_capture/` is the control-panel screenshot engine.
- `docs/` — durable topic reference; `docs/screenshots/` holds the capture manifest and generated per-tab images.

Declared tool roots (`extra_roots`):

- `reporting/` — daily numbers: APIs → Supabase → Notion. `scrape_client/` (HTTP/Playwright scrapers), `social_client/` (RapidAPI fetchers), `process/` (transform, upload, aggregate), `notion/` (editorial helpers and Notion sync).
- `planning/` — weekly publishing through each platform's native scheduler. One subpackage per platform: `linkedin/`, `instagram/`, `twitter/`, `threads/`, `substack/`, plus `videos/` for the weekly cross-platform video.
- `newsletter/` — the weekly newsletter (archive, normalize, build, Substack draft). `triage/` turns the Gmail label into an edition shortlist.
- `engagement/` — anti-AI comment triage. `linkedin/` (comment scraper), `classify/` (layered classifier), `reputation/` (commenter feedback loop), `db/` (Supabase schema).
- `check_ip/` — illustration copyright check: Google Lens → SQLite → review.
- `podcast/` — one episode folder → its reviewed episode package (transcribe, clips, covers, copy, render).
- `demo_video/` — demo videos: storyboard → Remotion template (`remotion/`) → MP4 per cut; `examples/` holds a sample storyboard.
- `recorder/` — the self-hosted two-side recorder; `static/` is its page and chunk-upload script.
- `app/` — the Streamlit control panel: one `tab_*.py` per tool, routed from `app.py`. UI code lives here and in `engagement/ui.py` and `engagement/review_app.py`.
- `gmail_readonly/` — portable read-only Gmail OAuth + search component, vendored; excluded from the ratchet.
- `google_oauth_common/` — shared installed-app OAuth bootstrap for the portable Google packages, vendored; excluded from the ratchet.

Hidden folders: `.streamlit/` (Streamlit config), `.claude/skills/` and `.agents/skills/` (the repo's skills, mirrored for each agent).

## Root files

Each pipeline has one orchestrator at the root next to its launcher: `reporting_pipeline.py`, `planning_pipeline.py`, `newsletter_pipeline.py`, `podcast_pipeline.py`, `demo_video_pipeline.py`, `recorder_server.py`, `run_app.py` and the `launch_*.bat` files.

## Baselined offenders

The baseline records every file, function and directory over its limit at today's size, nothing excluded to shrink it; they cannot grow, and `--update-baseline` lowers an entry once one is split. Splitting them is separate work.
