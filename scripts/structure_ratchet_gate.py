r"""The pre-ship gate's structure-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.structure_ratchet_gate

The vendored `scripts/structure_ratchet.py` is never edited here. Its exits:
0 pass; 1 a new offender, a baselined one that grew, a root folder off the
declared shape or a missing baseline; 2 a bad table, baseline or arguments;
3 something could not be measured (git unusable, an unparsable file, an unknown
`shape`). Exit 3 means nothing was checked, so it fails the gate as its own
state and never reads as a pass or a skip. The mapping lives here so a test can
pin it; the README's Verification section calls this wrapper, never the script.

Exit 0 only when the script exited 0; every other outcome exits 1 with its
reason on the last line.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.static_ratchet_gate import run_script  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "structure_ratchet.py"

EXIT_PASS, EXIT_NEW, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# One pass over the tracked tree plus an AST parse per Python file; this only
# has to outlast a hung git so the hang reads as NOT CONFIRMED, not a stuck gate.
RUN_TIMEOUT_S = 300

_FAIL_LABELS = {
    EXIT_NEW: "a new offender, a baselined one that grew, a root folder off the declared shape, or a missing baseline",
    EXIT_CONFIG: "bad [structure_ratchet] table, baseline, shape or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- something could not be measured, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no file, function or directory above its baselined size; layout matches the shape"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """The structure-ratchet stage: `script` through the static wrapper's runner."""
    return run_script(script, name="structure ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit)


if __name__ == "__main__":
    sys.exit(run())
