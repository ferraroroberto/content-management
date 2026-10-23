# Project Instructions

Canonical instructions for AI coding agents working in this repository. Claude Code reads this file directly as project memory. Other agents (Cursor, Codex, etc.) reach it via the one-line `AGENTS.md` pointer.

## Streamlit conventions
*Apply only if this project uses Streamlit.*

- Call `st.set_page_config(layout="wide", page_title="...")` first, before any other Streamlit call. Guidance, not a guarantee: no lint, hook or test in this repo checks it.
- Use `width="stretch"` (and `width="content"` where appropriate) in new and modified code. **Never** introduce new `use_container_width=True` — it is deprecated. When you touch existing code that uses `use_container_width`, migrate it.
- All mutable state in `st.session_state`. No module-level globals.
- `@st.cache_data` for DataFrames/files; `@st.cache_resource` for DB clients/models.
- Every widget needs a stable, explicit `key=`.
- UI code only in the UI modules (`app/`, `engagement/ui.py`, `engagement/review_app.py`; `run_app.py` imports `streamlit.web.cli` only to launch). Data logic stays in the non-UI packages. Keep `streamlit` imports out of non-UI code. Guidance, not a guarantee: no lint rule enforces the boundary.
- User feedback via `st.error()` / `st.warning()` / `st.success()`, not `st.write()`.
- **App layout:** main file (e.g. `app.py`) handles only page config, shared state, sidebar, and tab/radio routing. Each tab/mode lives in its own file exposing a `main(...)` (or `render_*`) function. Default to `st.tabs()`; use a sidebar radio only when asked.

## This repository
Python automation suite spanning social media reporting and content planning,
the weekly newsletter (with Gmail-history triage), comment-engagement
defense, illustration copyright checks (check IP), podcast production, demo
video capture, and a recorder + self-healing scheduler layer — metrics and
pipeline state live in Postgres/Supabase, synced to Notion where relevant.
See `README.md` for setup, layout, and usage.

- **A structure ratchet gates the pre-ship run.** `scripts/structure_ratchet_gate.py` runs the vendored `scripts/structure_ratchet.py` (byte-for-byte from project-scaffolding; re-vendor with `/propagate-vendored structure_ratchet`, never edit) and fails when a file passes 1,000 lines, a function 100 lines or a directory 40 modules and is new or above its entry in `.structure-ratchet-baseline.json`, or when the root folders do not match `shape = "pipeline-cli"` plus `extra_roots` in `.fleet.toml`. The baselined giants (the oversized files, the long functions, the flat `tests/`) are recorded at their real size, never excluded: they cannot grow, so a new test goes in a subfolder of `tests/` once one exists, and new code in an existing tool package. Burn the baseline down with `--update-baseline`; only `--allow-additions` adds. A new tool folder is declared in `extra_roots` with a line in `docs/layout.md`. Exit 3 (could not measure) is NOT CONFIRMED and fails. Config is the `[structure_ratchet]` table in `.fleet.toml`.

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is a hand-authored Mermaid diagram of this repo's own internal structure (the pipelines — reporting/planning/newsletter/engagement/check_ip/podcast/demo_video — the shared `config/` layer, the Streamlit control panel, and the external dependencies each talks to). Update it in the same PR as any material structural change (a pipeline split, a module moved, a new external dependency) — same anti-staleness contract as this repo's own `.fleet.toml` `description` field. It is not auto-generated and not covered by any test suite.
