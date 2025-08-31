# Automation Project Rules & Standards

This is the concise, general-purpose hub for coding standards and workflow. Use it as the single starting point; deeper topics are linked at the end.

## 🔎 TL;DR Core Rules {#tldr}
- **Name clearly**: files `snake_case.py`, functions/vars `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE_CASE`.
- **Imports order**: stdlib → third‑party → local.
- **Type hints**: annotate functions; use `Optional`, `Union`, `Dict[str, Any]`, `List[T]` when relevant.
- **Logging, not print**: module-level loggers; info/debug with emojis; no persistent file logging by default.
- **Error handling**: fail fast, clear messages, retry only when it helps.
- **Config first**: JSON config; no hardcoding; defaults + validation; secrets via env/.env.
- **SQL style**: descriptive aliases, `COALESCE`, UPPERCASE identifiers, doc complex processes.
- **Security**: never commit secrets; minimal dependencies; pin versions.
- **Plan before code**: write a brief implementation plan and wait for approval.
- **Scope discipline**: implement only what was requested.
- **.venv policy**: never activate; call the interpreter inside `.venv` directly.

## 🐍 Core Standards {#standards}

### Naming & Imports
```python
# Order imports: stdlib, third‑party, local
from pathlib import Path
import requests
from .utils import load_config
```

### Type Hints
- Annotate parameters and returns. Prefer explicit `Optional[T]` instead of bare `None`.

### Logging
```python
logger.debug("📂 Loading configuration file")
logger.info("✅ Configuration loaded successfully")
logger.error(f"❌ Config not found at {config_path}")
```

### Error Handling
- Handle expected errors with helpful messages and context; re‑raise unexpected ones.

### Configuration Rules
- JSON-based configs; no hardcoding of paths/URLs.
- Provide sane defaults; validate on load.
- Use env/.env for secrets; never commit.

## 🗄️ SQL & BigQuery Standards {#sql-standards}
```sql
-- Use descriptive CTE aliases
WITH source_with_flags AS (...)
SELECT COALESCE(a.field, b.field) AS field
FROM PROJECT.DATASET.TABLE AS T
```
- Document complex processes in a sibling `.md`.
- Partition/cluster appropriately for large tables.

## 📝 Documentation {#documentation}
- Use clear docstrings and minimal inline comments for non‑obvious logic.
- README template:
```markdown
# Module Name

## 🚀 Overview
## 📋 Usage
## 🔧 Configuration
## 📊 Output
```

## 📋 Planning Workflow {#planning-workflow}
Before any code changes, provide and get approval for a short plan:
```markdown
## 📋 Implementation Plan

### 🎯 Objective
[What will be accomplished]

### 📁 Files to Modify
- path/to/file1.py — [create/modify/delete] — [what changes]
- path/to/file2.py — [create/modify/delete] — [what changes]

### 🔗 Dependencies
[New packages/imports/external]

### 🧪 Testing Strategy
[How you will validate]

### ⚠️ Risks
[Potential impacts]
```

### Scope Limitation Rule
- No extra features or “nice‑to‑haves” unless explicitly approved.

## 🤖 Virtual Environment Policy

Always use the local `.venv` without activation. Call the interpreter directly.

**Linux/macOS:**
```bash
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pytest
```

**Windows (PowerShell):**
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
```

Use the same pattern for tools (black, isort, etc.): `./.venv/bin/python -m black .`

## 🧪 Testing & Virtual Environment {#testing-venv}

**Critical Rules:**
- **Never create** a virtual environment - use the existing one
- **Never install requirements** unless explicitly asked by the user
- **Always check** the `VENV_FOLDER` variable in the `.env` file for the venv path

**Testing Instructions:**
```powershell
# Read venv path from .env file
$venvPath = (Get-Content .env | Select-String "VENV_FOLDER" | ForEach-Object { $_.Line -split "=" | Select-Object -Last 1 }).Trim()

# Run tests using the venv interpreter
& "$venvPath\Scripts\python.exe" -m pytest

# Run specific test file
& "$venvPath\Scripts\python.exe" -m pytest tests/test_specific.py

# Run tests with coverage
& "$venvPath\Scripts\python.exe" -m pytest --cov=src --cov-report=html
```

**Example Usage:**
- If `VENV_FOLDER=E:\automation\automation\.venv`, use: `& "E:\automation\automation\.venv\Scripts\python.exe" -m pytest`

## 🖥️ Platform & Shell Assumptions {#platform-shell}
- Default environment: **Windows 10+ with PowerShell**.
- When executing commands with tools, **use PowerShell syntax**, not Unix/bash-only constructs.
- Prefer Windows path formats and quoting (e.g., `".\\venv\\Scripts\\python.exe"`).
- Use PowerShell-style file paths for Git and all CLI commands. Example: `git add .\\automation\\AGENTS.md` (not `git add automation/AGENTS.md`).
- Provide Windows examples first. Unix/macOS equivalents live in `AGENTS_PR.md` and are reference-only.
- Combine with the `.venv` policy above (no activation; call the interpreter directly).

## 🖥️ PowerShell Usage {#powershell}
- **Use PowerShell 7+** for `&&` operator and command chaining
- **Avoid PowerShell 5.1** - doesn't support `&&`, causes "stuck" commands
- **Reference**: `system/terminal_setup_howto.md`

### Command Patterns
```powershell
# PowerShell 7+ (Recommended)
cd "E:\automation\project" && git add . && git commit -m "Update"

# PowerShell 5.1 Fallback
cd "E:\automation\project"; if ($?) { git add . }; if ($?) { git commit -m "Update" }
```

### Multi-line Commands
```powershell
# Backtick continuation (PowerShell 7+)
git commit -m "Add feature" `
           -m "- Core functionality"

# Here-string
git commit -m @"
Add logging system
- JSON output
- Log rotation
"@
```

### Error Handling
```powershell
git add .; if ($?) { git commit -m "Success" } else { Write-Host "❌ Failed" }
```

### Common Commands
```powershell
# File operations
New-Item -ItemType File -Path ".\new_script.py" -Force
Copy-Item ".\source\*" ".\destination\" -Recurse
Remove-Item ".\temp\*" -Recurse -Force

# Git operations
git add .\modified_file.py; git commit -m "Fix bug"; git push origin main

# Python operations
python -c "import sys; print(sys.version)"
python -m pytest --cov=src
```

### Troubleshooting
```powershell
$PSVersionTable.PSVersion
Get-Command python -ErrorAction SilentlyContinue
Get-ChildItem Env: | Where-Object {$_.Name -like "*PYTHON*"}
```

## 📄 GIT commits and push rules
- Do not automatically create commits or push. Wait for explicit instruction from the user. On task completion, ask: "shall I create a commit message, stage, commit and push"?

## 🔒 Security & Dependencies
- Never commit secrets; use env/.env.
- Minimal dependencies; pin versions; keep security patches up to date.

## 🔗 See Also
- [AGENTS_CLI.md](AGENTS_CLI.md#cli-architecture)
- [AGENTS_STRUCTURE.md](AGENTS_STRUCTURE.md#reorg-checklist)
- [AGENTS_PR.md](AGENTS_PR.md#environment-setup)