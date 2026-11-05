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

Six ratchets gate the pre-ship run, in this order: structure, instruction, doc-references, dead-code, conventions, runtime write. Each is `scripts/<name>_ratchet_gate.py` running the vendored `scripts/<name>_ratchet.py` (byte-for-byte from project-scaffolding; re-vendor with `/propagate-vendored <name>_ratchet`, never edit), with a baseline `.<name>-ratchet-baseline.json` (hyphenated name) and a `[<name>_ratchet]` table in `.fleet.toml`. Burn a baseline down with `--update-baseline`; only `--allow-additions` adds. Exit 3 (could not measure, read or parse) is NOT CONFIRMED and fails.

- **Structure:** fails when a file exceeds 1,000 lines, a function 100 lines or a directory 40 modules and is new or above its baseline entry, or when the root folders do not match `shape = "pipeline-cli"` plus `extra_roots` in `.fleet.toml`. The baselined giants (the oversized files, the long functions) are recorded at their real size, never excluded: they cannot grow, so new code goes in an existing tool package, and a new test in its domain's subfolder of `tests/` (see `docs/layout.md`). A new tool folder is declared in `extra_roots` with a line in `docs/layout.md`.
- **Instruction:** runs over `CLAUDE.md`, `AGENTS.md` and every `.claude/skills/*/SKILL.md` with the docs it links to; fails when a file's count for a prompt-audit rule rises above its baseline or a file passes its size cap. Reword a hit away rather than baselining it.
- **Doc references:** runs over `README.md`, `CLAUDE.md` and `docs/**/*.md`; fails when one names a repo path, a `python -m` module or a relative link that does not exist and is not in the baseline. Fix the reference or drop it rather than baselining it; a path named on purpose goes in `allow_refs`.
- **Dead code:** fails when a new function, class or method has no reference anywhere in the repo and is not in the baseline. Delete the definition or call it rather than baselining it; a real entry point the rules miss goes in `entry_decorators` or `allow`.
- **Conventions:** re-vendor with `/propagate-vendored conventions_ratchet,conventions_readers`. Fails when a file's hits for `no-window`, `logging-not-print`, `no-drive-path` or `subprocess-timeout` (a blocking `subprocess.run`/`call`/`check_*` with no `timeout=`) rise above the baseline, which is empty. Follow the convention, or keep a reasoned `# <rule>-exempt: <reason>` marker on the call. A new `print` in library code goes through `logging`.
- **Runtime write:** re-vendor with `/propagate-vendored runtime_write_ratchet,runtime_write_resolve` (the script has a sibling `scripts/runtime_write_resolve.py`). Fails when a file's count of write targets resolving under a code folder rises above the baseline; its `--snapshot`/`--probe` pair brackets the test runs and fails on any file they leave under a code folder. Runtime output goes under `data/` or the runtime root. Exit 3 also covers a new target it cannot resolve.

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is a hand-authored Mermaid diagram of this repo's own internal structure (the pipelines — reporting/planning/newsletter/engagement/check_ip/podcast/demo_video — the shared `config/` layer, the Streamlit control panel, and the external dependencies each talks to). Update it in the same PR as any material structural change (a pipeline split, a module moved, a new external dependency) — same anti-staleness contract as this repo's own `.fleet.toml` `description` field. It is not auto-generated and not covered by any test suite.
