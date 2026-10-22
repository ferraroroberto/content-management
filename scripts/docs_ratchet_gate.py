r"""The pre-ship gate's docs-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.docs_ratchet_gate

`scripts/docs_ratchet.py` (vendored, never edited here) exits 0 on a pass, 1 on
a README or history-marker count above the baseline, 2 on a bad table or
baseline and 3 when a file could not be read (not UTF-8, an unclosed fence or
comment, git unusable). Exit 3 means nothing was checked, so it must fail the
gate as its own state and never read as a pass or a skip. This wrapper is the
one place that mapping lives, so a test can pin it; the README's Verification
section calls the wrapper, never the script.

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

SCRIPT = REPO_ROOT / "scripts" / "docs_ratchet.py"

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# A Markdown line scan over a handful of files; this only has to outlast a
# hung git so the hang reads as NOT CONFIRMED rather than a stuck gate.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_RISE: "README size or a history marker rose above the baseline",
    EXIT_CONFIG: "bad [docs_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a doc could not be read, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "README within budget and no new history marker"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """The docs-ratchet stage: `script` through the static wrapper's runner."""
    return run_script(script, name="docs ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit)


if __name__ == "__main__":
    sys.exit(run())
