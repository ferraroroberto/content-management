r"""The pre-ship gate's request-budget-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.request_budget_ratchet_gate

The vendored `scripts/request_budget_ratchet.py` is never edited here; this wrapper only
translates its exit code for the gate.

- 0: no rule count rose above the baseline, or the `[request_budget_ratchet]` table is off.
- 1: a rule count rose, or `.request-budget-ratchet-baseline.json` is missing.
- 2: the table, the baseline or the arguments are malformed.
- 3: git failed, a source file was unreadable, a JS literal or call never closed, or a
  Python file did not parse. That file's poll or loop count is unknown, not zero, so the
  stage is NOT CONFIRMED and fails; it is never read as a pass or a skip.

A unit test pins the mapping and the README's Verification section runs this wrapper.
Every outcome other than a clean pass exits 1 with its reason on the last line.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.static_ratchet_gate import run_script  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "request_budget_ratchet.py"

EXIT_PASS, EXIT_RISEN, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# One `git ls-files` plus a read of the in-scope sources; this only has to outlast a
# hung git so the hang reads as NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_RISEN: "a poll, loop, scan, pool submit or the timing middleware has a new hit, or the baseline is missing",
    EXIT_CONFIG: "bad [request_budget_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- git or a source file could not be read, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no rule count rose above the request-budget baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """The request-budget stage: `script` through the static wrapper's runner."""
    return run_script(script, name="request budget ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit)


if __name__ == "__main__":
    sys.exit(run())
