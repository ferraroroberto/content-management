"""One-time interactive login that prepares a dedicated Chrome profile for Substack.

Usage:
    python -m substack.bootstrap_session [--debug]

After login the dedicated profile retains the session, so subsequent runs of
``post_substack_note`` (and ``reporting/scrape_client/substack.py``) reuse it
without prompting. See ``planning._bootstrap.run_bootstrap`` for the shared
implementation (mirror of ``instagram/bootstrap_session.py``,
``twitter/bootstrap_session.py``, ``threads/bootstrap_session.py``,
``linkedin/bootstrap_session.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning._bootstrap import parse_bootstrap_args, run_bootstrap  # noqa: E402
from planning.substack.substack_session import (  # noqa: E402
    _resolve_user_data_dir,
    configure_logger,
    load_substack_config,
)

SIGN_IN_URL = "https://substack.com/sign-in"

LOGIN_PROMPT = (
    "\n>>> Log in inside the opened Chrome window, "
    "then press Enter here to save the session...\n"
)


def main() -> int:
    args = parse_bootstrap_args("One-time Substack session bootstrap.")
    return run_bootstrap(
        platform_label="Substack",
        logger_name="substack_bootstrap",
        load_config=load_substack_config,
        resolve_user_data_dir=_resolve_user_data_dir,
        configure_logger=configure_logger,
        default_login_url=SIGN_IN_URL,
        login_prompt=LOGIN_PROMPT,
        debug=args.debug,
    )


if __name__ == "__main__":
    raise SystemExit(main())
