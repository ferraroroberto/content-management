# Project Instructions

Canonical instructions for every AI coding agent here: Claude Code reads this file as project memory; other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## Streamlit conventions
*Apply only if this project uses Streamlit.*

- Call `st.set_page_config(layout="wide", page_title="...")` first, before any other Streamlit call. Guidance only — no lint, hook or test in this repo checks it.
- Use `width="stretch"` (and `width="content"` where appropriate) in new and modified code. **Never** introduce new `use_container_width=True` — it is deprecated; migrate it wherever you touch existing code that uses it.
- All mutable state in `st.session_state`. No module-level globals.
- `@st.cache_data` for DataFrames/files; `@st.cache_resource` for DB clients/models.
- Every widget needs a stable, explicit `key=`.
- UI code only in the UI modules (`app/`, `engagement/ui.py`, `engagement/review_app.py`; `run_app.py` imports `streamlit.web.cli` only to launch); data logic and zero `streamlit` imports in the non-UI packages. Guidance only — no lint rule enforces the boundary.
- User feedback via `st.error()` / `st.warning()` / `st.success()`, not `st.write()`.
- **App layout:** main file (e.g. `app.py`) handles only page config, shared state, sidebar, and tab/radio routing. Each tab/mode lives in its own file exposing a `main(...)` (or `render_*`) function. Default to `st.tabs()`; use a sidebar radio only when asked.

## This repository
Python automation suite: collects social media metrics across platforms, stores them in Postgres/Supabase, syncs to Notion for reporting. See `README.md` for setup, layout, and usage.

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) — hand-authored Mermaid of this repo's own internal structure (the four pipelines — reporting/planning/newsletter/engagement — the shared `config/` layer, the Streamlit control panel, and the external dependencies each talks to). Not auto-generated, not covered by any test suite: update it in the same PR as any material structural change (a pipeline split, a module moved, a new external dependency) — same anti-staleness contract as this repo's own `.fleet.toml` `description` field.
