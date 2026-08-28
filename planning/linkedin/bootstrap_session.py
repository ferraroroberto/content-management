"""One-time interactive login that prepares a dedicated Chrome profile for LinkedIn.

Usage:
    python -m linkedin.bootstrap_session [--debug]

See ``planning._bootstrap.run_bootstrap`` for the shared implementation
(mirror of ``instagram/bootstrap_session.py``, ``twitter/bootstrap_session.py``,
``threads/bootstrap_session.py``, ``substack/bootstrap_session.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning._bootstrap import parse_bootstrap_args, run_bootstrap  # noqa: E402
from planning.linkedin.linkedin_session import (  # noqa: E402
    _resolve_user_data_dir,
    configure_logger,
    load_linkedin_config,
)

LOGIN_URL_DEFAULT = "https://www.linkedin.com/login"

LOGIN_PROMPT = (
    "\n>>> Log in to LinkedIn inside the opened Chrome window, "
    "then press Enter here to save the session...\n"
)


def main() -> int:
    args = parse_bootstrap_args("One-time LinkedIn session bootstrap.")
    return run_bootstrap(
        platform_label="LinkedIn",
        logger_name="linkedin_bootstrap",
        load_config=load_linkedin_config,
        resolve_user_data_dir=_resolve_user_data_dir,
        configure_logger=configure_logger,
        default_login_url=LOGIN_URL_DEFAULT,
        login_prompt=LOGIN_PROMPT,
        debug=args.debug,
    )


if __name__ == "__main__":
    raise SystemExit(main())
