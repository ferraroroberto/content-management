"""Newsletter pipeline orchestrator (issues #18, #59).

The weekly newsletter workflow, split into independent, non-interactive steps
so each can run on its own and stream cleanly to the Streamlit app. The same
subcommands back both the app and ``launch_newsletter.bat`` — there is no
Streamlit-only path.

Subcommands::

    newsletter_pipeline.py bootstrap                         # ensure Chrome on :9222 (targeted)
    newsletter_pipeline.py archive   [--debug]               # open tabs -> Notion
    newsletter_pipeline.py normalize [--days 14] [--debug]   # titles + URLs
    newsletter_pipeline.py build     --newsletter NNN [--debug] [--no-open]
                                     [--must-read 1|2|3 | --no-must-read]
    newsletter_pipeline.py create    --newsletter NNN [--days 14] [--debug]
    newsletter_pipeline.py all       [--newsletter NNN] [--days 14] [--debug]

* ``bootstrap`` launches the dedicated newsletter Chrome on :9222 **without**
  killing the everyday browser (see ``newsletter/bootstrap_chrome.py``).
* ``archive`` / ``normalize`` / ``build`` / ``create`` are all non-interactive.
* ``create`` = archive -> normalize -> build (no must-read prompt), chained.
* ``all`` is the one sanctioned interactive console flow: bootstrap -> a single
  "open your tabs, press Enter" pause (you can't archive tabs that aren't open
  yet) -> archive -> normalize -> build with the interactive must-read prompt.
* No subcommand defaults to ``all`` so existing muscle memory keeps working.

Mirrors the shape of ``reporting_pipeline.py`` / ``planning_pipeline.py``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Force UTF-8 stdio so emoji log lines don't crash Windows' cp1252 console.
from config.console import force_utf8_stdio  # noqa: E402
force_utf8_stdio()

from newsletter import bootstrap_chrome  # noqa: E402
from newsletter import build_newsletter, normalize_names, normalize_url  # noqa: E402
from newsletter import pipeline as archive_pipeline  # noqa: E402

logger = logging.getLogger("newsletter_pipeline")


# ----------------------------------------------------------------- step helpers


def _wait_for_user(prompt: str) -> None:
    print()
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("❌ Cancelled by user.")
        raise SystemExit(2)


def _banner(title: str) -> None:
    print()
    print("=" * 60)
    print(f">>> {title}")
    print("=" * 60)


# ---------------------------------------------------------------------- steps


def step_bootstrap() -> int:
    _banner("bootstrap Chrome on :9222 (targeted — everyday browser untouched)")
    return bootstrap_chrome.ensure_chrome()


def step_archive(debug: bool) -> int:
    _banner("archive open Chrome tabs to Notion")
    if not bootstrap_chrome.debug_port_up():
        print("❌ Chrome isn't responding on :9222 — run the bootstrap step first")
        print("   (the '① Bootstrap Chrome' button, or:")
        print("       newsletter_pipeline.py bootstrap )")
        return 1
    return archive_pipeline.run_batch(write=True, debug=debug)


def step_normalize(days: int, debug: bool) -> int:
    _banner(f"normalize_names (last {days} days)")
    normalize_names.run(days=days, dry_run=False, debug=debug)
    _banner(f"normalize_url (last {days} days)")
    normalize_url.run(days=days, dry_run=False, testing_mode=False, debug=debug)
    return 0


def step_build(newsletter_number: str | None, debug: bool, *,
               interactive_must_read: bool, open_browser: bool,
               must_read: int | None) -> int:
    _banner("build newsletter HTML")
    if not newsletter_number:
        if interactive_must_read:
            try:
                newsletter_number = input("Enter newsletter number (e.g. 057): ").strip()
            except (EOFError, KeyboardInterrupt):
                raise SystemExit(2)
        if not newsletter_number:
            print("❌ newsletter number is required (--newsletter NNN)")
            return 1
    out_path = build_newsletter.run(
        newsletter_number, debug=debug,
        interactive_must_read=interactive_must_read,
        open_browser=open_browser, must_read=must_read,
    )
    print(f"🎉 Newsletter HTML: {out_path}")
    return 0


# --------------------------------------------------------------- composite flows


def run_create(*, days: int, newsletter_number: str | None, debug: bool) -> int:
    """archive -> normalize -> build (no must-read prompt). Non-interactive."""
    if not newsletter_number:
        print("❌ newsletter number is required for 'create' (--newsletter NNN)")
        return 1
    rc = step_archive(debug=debug)
    if rc != 0:
        return rc
    rc = step_normalize(days=days, debug=debug)
    if rc != 0:
        return rc
    return step_build(
        newsletter_number, debug=debug,
        interactive_must_read=False, open_browser=True, must_read=None,
    )


def run_all(*, days: int, newsletter_number: str | None, debug: bool) -> int:
    """Full interactive console sequence with a single manual pause."""
    rc = step_bootstrap()
    if rc != 0:
        return rc
    _wait_for_user(
        ">>> Open every newsletter article tab in the Chrome window, then press Enter…"
    )
    rc = step_archive(debug=debug)
    if rc != 0:
        return rc
    _wait_for_user(">>> Press Enter when you're ready to normalise names + URLs…")
    rc = step_normalize(days=days, debug=debug)
    if rc != 0:
        return rc
    return step_build(
        newsletter_number, debug=debug,
        interactive_must_read=True, open_browser=True, must_read=None,
    )


# --------------------------------------------------------------------- CLI


def _add_days(p: argparse.ArgumentParser) -> None:
    p.add_argument("--days", type=int, default=14,
                   help="Look back N days for normalize (default 14)")


def _add_newsletter(p: argparse.ArgumentParser, *, required: bool) -> None:
    p.add_argument("--newsletter", type=str, default=None, required=required,
                   help="Newsletter number, e.g. 057 or N057")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Newsletter pipeline: bootstrap → archive → normalize → build."
    )
    parser.add_argument("--debug", action="store_true",
                        help="Verbose logs (also accepted per-subcommand)")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("bootstrap", help="Ensure Chrome is up on :9222 (targeted)")

    p_arch = sub.add_parser("archive", help="Archive open Chrome tabs to Notion")
    p_arch.add_argument("--debug", action="store_true")

    p_norm = sub.add_parser("normalize", help="Normalize article titles + URLs")
    _add_days(p_norm)
    p_norm.add_argument("--debug", action="store_true")

    p_build = sub.add_parser("build", help="Build the newsletter HTML")
    _add_newsletter(p_build, required=False)
    p_build.add_argument("--debug", action="store_true")
    p_build.add_argument("--no-open", action="store_true",
                         help="Don't open the rendered HTML in the browser")
    mr = p_build.add_mutually_exclusive_group()
    mr.add_argument("--must-read", type=int, choices=(1, 2, 3), default=None,
                    help="Compose + copy the must-read line non-interactively")
    mr.add_argument("--no-must-read", action="store_true",
                    help="Skip the must-read step entirely (app's non-blocking path)")

    p_create = sub.add_parser("create", help="archive → normalize → build (non-interactive)")
    _add_newsletter(p_create, required=True)
    _add_days(p_create)
    p_create.add_argument("--debug", action="store_true")

    p_all = sub.add_parser("all", help="Full interactive console sequence")
    _add_newsletter(p_all, required=False)
    _add_days(p_all)
    p_all.add_argument("--debug", action="store_true")

    args = parser.parse_args(argv)
    debug = bool(getattr(args, "debug", False))
    cmd = args.cmd or "all"

    if cmd == "bootstrap":
        return step_bootstrap()
    if cmd == "archive":
        return step_archive(debug=debug)
    if cmd == "normalize":
        return step_normalize(days=args.days, debug=debug)
    if cmd == "build":
        # build default (no must-read flags) → interactive prompt.
        interactive = args.must_read is None and not args.no_must_read
        return step_build(
            args.newsletter, debug=debug,
            interactive_must_read=interactive,
            open_browser=not args.no_open,
            must_read=args.must_read,
        )
    if cmd == "create":
        return run_create(days=args.days, newsletter_number=args.newsletter, debug=debug)
    # "all" (explicit or default)
    return run_all(
        days=getattr(args, "days", 14),
        newsletter_number=getattr(args, "newsletter", None),
        debug=debug,
    )


if __name__ == "__main__":
    raise SystemExit(main())
