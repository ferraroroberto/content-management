r"""The pre-ship gate's doc-refs-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.doc_refs_ratchet_gate

`scripts/doc_refs_ratchet.py` is vendored and never edited here. It exits 0 on a
pass; 1 when a doc names a missing path, module or link outside the baseline, or
the baseline file is absent; 2 on a bad table, baseline or arguments; 3 when git
or a doc could not be read (not UTF-8, a fence or HTML comment left open).
Only 0 passes: 3 is reported as its own NOT CONFIRMED state, so an unread doc
never looks checked. A test pins this mapping, and the README's Verification
section runs this wrapper rather than the script.

Every outcome other than a clean pass exits 1 with its reason on the last line.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.static_ratchet_gate import run_script  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "doc_refs_ratchet.py"

EXIT_PASS, EXIT_DANGLING, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# One `git ls-files` plus one batched `git check-ignore` over a few dozen docs;
# this only has to outlast a hung git so the hang reads as NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_DANGLING: "a doc names a path, module or link that does not exist, or the baseline is missing",
    EXIT_CONFIG: "bad [doc_refs_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a doc or git could not be read, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no doc names a missing path, module or link beyond the baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """The doc-refs-ratchet stage: `script` through the static wrapper's runner."""
    return run_script(script, name="doc refs ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit)


if __name__ == "__main__":
    sys.exit(run())
