r"""The pre-ship gate's static-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.static_ratchet_gate

`scripts/static_ratchet.py` (vendored, never edited here) exits 0 on a pass,
1 on a new finding, 2 on a bad table or baseline and 3 when a detector could
not run (no `npx`, no network on the first `npx`, ruff missing, a timeout).
Exit 3 means nothing was checked, so it must fail the gate as its own state and
never read as a pass or a skip. This wrapper is the one place that mapping
lives, so a test can pin it; the README's Verification section calls the
wrapper, never the script.

Exit 0 only when the script exited 0; every other outcome exits 1 with its
reason on the last line.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.no_window import NO_WINDOW  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "static_ratchet.py"

EXIT_PASS, EXIT_NEW, EXIT_CONFIG, EXIT_NOT_CONFIRMED = 0, 1, 2, 3

# The script bounds each of its two detectors at 600 s itself; this only has to
# outlast both so a hung child is reported by the script's own NOT CONFIRMED.
RUN_TIMEOUT_S = 1300

_FAIL_LABELS = {
    EXIT_NEW: "NEW finding or missing baseline",
    EXIT_CONFIG: "bad [static_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a detector could not run, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no new duplicate block or unused import/variable"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run_script(
    script: Path,
    *,
    name: str,
    timeout_s: int,
    classify: Callable[[int], tuple[bool, str]],
) -> int:
    """Run `script` under this interpreter (the venv python, which has ruff),
    echo its output, then the verdict `classify` gives its exit code. A timeout
    or a script that cannot start is NOT CONFIRMED. Returns the gate's exit
    code; shared with the docs-ratchet wrapper so the mapping lives once."""
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, creationflags=NO_WINDOW,
            timeout=timeout_s, check=False,
        )
        code, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, output = EXIT_NOT_CONFIRMED, f"{script.name} did not finish in {timeout_s}s\n"
    except OSError as exc:
        code, output = EXIT_NOT_CONFIRMED, f"{script.name} could not start: {exc}\n"
    if output:
        sys.stdout.write(output if output.endswith("\n") else output + "\n")
    ok, label = classify(code)
    print(f"{'OK' if ok else 'FAIL'}: {name}: {label}")
    return 0 if ok else 1


def run(script: Path = SCRIPT) -> int:
    """The static-ratchet stage: `script` through `run_script`."""
    return run_script(script, name="static ratchet", timeout_s=RUN_TIMEOUT_S, classify=classify_exit)


if __name__ == "__main__":
    sys.exit(run())
