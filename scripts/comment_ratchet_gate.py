r"""The pre-ship gate's comment-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.comment_ratchet_gate

`scripts/comment_ratchet.py` (vendored, never edited here) exits 0 on a pass,
1 when a file's count of `#NNN` issue references inside comments rises above
the baseline (or the baseline is missing), 2 on a bad table or baseline and 3
when a file could not be scanned (unterminated string/comment/template, a
Python file `tokenize` rejects, undecodable bytes, git unusable). Exit 3 means
nothing was checked, so it must fail the gate as its own state and never read
as a pass or a skip. This wrapper is the one place that mapping lives, so a
test can pin it; the README's Verification section calls the wrapper, never
the script.

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

SCRIPT = REPO_ROOT / "scripts" / "comment_ratchet.py"

EXIT_PASS, EXIT_RISE, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# A tokenizer/scanner sweep over the tracked tree; this only has to outlast a
# hung git or a pathological file so the hang reads as NOT CONFIRMED rather
# than a stuck gate.
RUN_TIMEOUT_S = 300

_FAIL_LABELS = {
    EXIT_RISE: "a file's #NNN comment-reference count rose above the baseline, or the baseline is missing",
    EXIT_CONFIG: "bad [comment_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a file could not be scanned, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no file above its baselined #NNN comment-reference count"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """The comment-ratchet stage: `script` through the static wrapper's runner."""
    return run_script(script, name="comment ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit)


if __name__ == "__main__":
    sys.exit(run())
