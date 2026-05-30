"""Newsletter pipeline orchestrator (issue #18).

Walks the weekly newsletter workflow end-to-end:

1. Ensure Chrome is up on ``:9222`` against the dedicated newsletter profile.
   Bootstrap is **skipped by default** — bring Chrome up yourself by running
   ``newsletter/bootstrap_chrome.bat`` in a separate console (it kills every
   chrome.exe + relaunches with ``--user-data-dir``). Pass ``--no-skip-bootstrap``
   to let the pipeline run that bootstrap for you.
2. Wait for you to open your article tabs in that Chrome window.
3. Archive the tabs into Notion via :func:`newsletter.pipeline.run_batch`.
4. Wait for the green light to clean up.
5. Run :func:`newsletter.normalize_names.run` (titles → sentence case).
6. Run :func:`newsletter.normalize_url.run` (strip tracking params).
7. Prompt for the newsletter number and run
   :func:`newsletter.build_newsletter.run` — HTML written under
   ``results/newsletter/N{NNN}.html``, opened in the browser, then prompt
   for the must-read topic and copy the composed line to the clipboard.

Mirrors the shape of ``reporting_pipeline.py`` / ``planning_pipeline.py``.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Force UTF-8 stdio so emoji log lines don't crash Windows' cp1252 console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

import requests  # noqa: E402

from newsletter import build_newsletter, normalize_names, normalize_url  # noqa: E402
from newsletter import pipeline as archive_pipeline  # noqa: E402

BOOTSTRAP_BAT = REPO_ROOT / "newsletter" / "bootstrap_chrome.bat"
DEBUG_URL = "http://127.0.0.1:9222/json/version"

logger = logging.getLogger("newsletter_pipeline")


# ----------------------------------------------------------------- step helpers


def _wait_for_user(prompt: str) -> None:
    print()
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("❌ Cancelled by user.")
        raise SystemExit(2)


def _debug_port_up(timeout: float = 1.0) -> bool:
    try:
        r = requests.get(DEBUG_URL, timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


# ---------------------------------------------------------------------- steps


def step_bootstrap_chrome() -> None:
    print("=" * 60)
    print(">>> Step 1/5: bootstrap Chrome on :9222")
    print("=" * 60)
    if not BOOTSTRAP_BAT.exists():
        raise FileNotFoundError(f"Missing bootstrap_chrome.bat at {BOOTSTRAP_BAT}")
    # The bat kills every chrome.exe, relaunches with the debug port, and polls
    # until :9222 responds. We don't capture output — let the user see it.
    result = subprocess.run([str(BOOTSTRAP_BAT)], shell=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"bootstrap_chrome.bat exited with code {result.returncode}"
        )
    # Belt-and-braces — confirm the port is actually reachable from here.
    for _ in range(10):
        if _debug_port_up():
            print("✅ Chrome :9222 is ready")
            return
        time.sleep(1)
    raise RuntimeError(":9222 not reachable after bootstrap")


def step_archive(debug: bool) -> None:
    print()
    print("=" * 60)
    print(">>> Step 3/5: archive open Chrome tabs to Notion")
    print("=" * 60)
    rc = archive_pipeline.run_batch(write=True, debug=debug)
    if rc != 0:
        raise RuntimeError(f"newsletter archive returned exit code {rc}")


def step_normalize(days: int, debug: bool) -> None:
    print()
    print("=" * 60)
    print(f">>> Step 4a/5: normalize_names (last {days} days)")
    print("=" * 60)
    normalize_names.run(days=days, dry_run=False, debug=debug)

    print()
    print("=" * 60)
    print(f">>> Step 4b/5: normalize_url (last {days} days)")
    print("=" * 60)
    normalize_url.run(days=days, dry_run=False, testing_mode=False, debug=debug)


def step_build_newsletter(newsletter_number: str | None, debug: bool) -> None:
    print()
    print("=" * 60)
    print(">>> Step 5/5: build newsletter HTML")
    print("=" * 60)
    if not newsletter_number:
        try:
            newsletter_number = input("Enter newsletter number (e.g. 057): ").strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(2)
    if not newsletter_number:
        raise SystemExit("❌ newsletter number is required")
    out_path = build_newsletter.run(newsletter_number, debug=debug)
    print()
    print(f"🎉 Newsletter pipeline complete. HTML: {out_path}")


# --------------------------------------------------------------- orchestration


def run_pipeline(*, days: int, newsletter_number: str | None,
                 debug: bool, skip_bootstrap: bool) -> int:
    if not skip_bootstrap:
        step_bootstrap_chrome()
    else:
        if not _debug_port_up():
            print("❌ Chrome isn't responding on :9222 — cannot archive tabs.")
            print("   Bootstrap is skipped by default (it kills every chrome.exe,")
            print("   including your everyday browser). Open a SEPARATE console and run:")
            print("       newsletter\\bootstrap_chrome.bat")
            print("   open your newsletter article tabs in that window, then re-run this")
            print("   pipeline. To let the pipeline bootstrap for you, pass --no-skip-bootstrap.")
            return 1
        print("⏭️  Using the existing Chrome on :9222 (bootstrap skipped by default)")

    _wait_for_user(
        ">>> Step 2/5: open every newsletter article tab in the Chrome window, "
        "then press Enter…"
    )

    step_archive(debug=debug)

    _wait_for_user(
        ">>> Press Enter when you're ready to normalise names + URLs…"
    )

    step_normalize(days=days, debug=debug)

    step_build_newsletter(newsletter_number, debug=debug)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Newsletter pipeline: bootstrap → archive → normalize → build."
    )
    parser.add_argument(
        "--days", type=int, default=14,
        help="Look back N days for normalize_names / normalize_url (default 14)",
    )
    parser.add_argument(
        "--newsletter", type=str, default=None,
        help="Newsletter number (e.g. 057). Prompted if omitted.",
    )
    parser.add_argument(
        "--skip-bootstrap", action=argparse.BooleanOptionalAction, default=True,
        help="Skip the Chrome kill/relaunch and reuse the existing :9222 instance "
             "(default). Use --no-skip-bootstrap to let the pipeline kill every "
             "chrome.exe and relaunch the dedicated newsletter profile.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    return run_pipeline(
        days=args.days,
        newsletter_number=args.newsletter,
        debug=args.debug,
        skip_bootstrap=args.skip_bootstrap,
    )


if __name__ == "__main__":
    raise SystemExit(main())
