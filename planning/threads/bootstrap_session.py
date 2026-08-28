"""One-time interactive login that prepares a dedicated Chrome profile for Threads.

Usage:
    python -m threads.bootstrap_session [--debug]

See ``planning._bootstrap.run_bootstrap`` for the shared implementation
(mirror of ``instagram/bootstrap_session.py``, ``twitter/bootstrap_session.py``,
``linkedin/bootstrap_session.py``, ``substack/bootstrap_session.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning._bootstrap import parse_bootstrap_args, run_bootstrap  # noqa: E402
from planning.threads.threads_session import (  # noqa: E402
    _resolve_user_data_dir,
    configure_logger,
    load_threads_config,
)

LOGIN_URL_DEFAULT = "https://www.threads.com/@ferraroroberto"

LOGIN_PROMPT = (
    "\n>>> Log in to Threads inside the opened Chrome window.\n"
    "    (Threads authenticates via Instagram, so you may need to sign in there too.)\n"
    "    Once you can see your profile feed at threads.com/@ferraroroberto,\n"
    "    return here and press Enter to save the session...\n"
)


def main() -> int:
    args = parse_bootstrap_args("One-time Threads session bootstrap.")
    return run_bootstrap(
        platform_label="Threads",
        logger_name="threads_bootstrap",
        load_config=load_threads_config,
        resolve_user_data_dir=_resolve_user_data_dir,
        configure_logger=configure_logger,
        default_login_url=LOGIN_URL_DEFAULT,
        login_prompt=LOGIN_PROMPT,
        debug=args.debug,
    )


if __name__ == "__main__":
    raise SystemExit(main())
