# 📐 Agents: General-Purpose Structure Guide

Purpose: universal principles to analyze, reorganize, and validate project structure. Keep repos consistent and maintainable. Cross‑reference: see [AGENTS.md](AGENTS.md#planning-workflow) for the main project standards.

## 🧭 When to use this
- Repo feels cluttered or mixed-concern.
- New CLI or entry point needs clear placement.
- You need a consistent domain layout across modules.

## ✅ Reorg Checklist {#reorg-checklist}
- Identify domains (e.g., `process/`, `api_clients/`, `config/`, `cli/`, `docs/`).
- Move misplaced files to their domain; rename to descriptive `snake_case`.
- Create a single entry point (e.g., `cli/main.py` or `launch.py`).
- Update imports to reflect new paths; remove sys.path hacks.
- Add/refresh folder READMEs.
- Keep compatibility notes or thin shims if needed.

## ↔️ Relocation Rules
```
init.py           → process/<descriptive_module>.py
main.py (CLI)     → cli/main.py
config.py         → config/config.py
orchestrator.py   → process/pipeline.py
```

Naming: prefer explicit names reflecting behavior; avoid generic `main`, `utils2`, etc.

## 🔎 Minimal Discovery Pattern
```python
from pathlib import Path
import importlib.util

def load_module_from(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

## ✅ Validation Rules
- No circular imports; limit cross-domain imports.
- Entry points are minimal and delegate to domain modules.
- Config is read from `config/` only; no hardcoded paths.
- Tests/imports pass after moves; update tooling paths.

## 🧰 Optional CLI Hook
Provide a `structure` command in your CLI to analyze/validate structure. Keep it minimal; link to this doc for rules.

## 🔗 See Also
- [AGENTS.md](AGENTS.md#tldr)
- [AGENTS_CLI.md](AGENTS_CLI.md#cli-architecture)

