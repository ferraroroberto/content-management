r"""The pre-ship gate's dead-code stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.dead_code_ratchet_gate

The vendored `scripts/dead_code_ratchet.py` is never edited here. Its exits: 0 on
a pass (or when the table is not enabled); 1 when a function, class or method
nothing refers to appears outside the baseline, or the baseline file is absent;
2 on a bad table, baseline or arguments; 3 when git is unusable or a tracked
Python file cannot be read or parsed. Only 0 passes: 3 means a file that may
hold the only reference to a definition went unread, so it fails as its own
NOT CONFIRMED state and never reads as a pass or a skip. A test pins this
mapping, and the README's Verification section runs this wrapper.

Every outcome other than a clean pass exits 1 with its reason on the last line.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.static_ratchet_gate import run_script  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "dead_code_ratchet.py"

EXIT_PASS, EXIT_DEAD, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# One `git ls-files` plus an `ast` parse of every tracked Python file; this only
# has to outlast a hung git so the hang reads as NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_DEAD: "a new definition is never referenced, or the baseline is missing",
    EXIT_CONFIG: "bad [dead_code_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- git or a Python file could not be read or parsed, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no definition is unreferenced beyond the baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """The dead-code stage: `script` through the static wrapper's runner."""
    return run_script(script, name="dead code ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit)


if __name__ == "__main__":
    sys.exit(run())
