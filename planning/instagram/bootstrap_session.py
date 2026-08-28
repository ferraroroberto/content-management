"""One-time interactive login that prepares a dedicated Chrome profile for the Meta planner.

Usage:
    python -m instagram.bootstrap_session [--debug]

See ``planning._bootstrap.run_bootstrap`` for the shared implementation
(mirror of ``linkedin/bootstrap_session.py``, ``twitter/bootstrap_session.py``,
``threads/bootstrap_session.py``, ``substack/bootstrap_session.py``).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from planning._bootstrap import parse_bootstrap_args, run_bootstrap  # noqa: E402
from planning.instagram.instagram_session import (  # noqa: E402
    _resolve_user_data_dir,
    configure_logger,
    load_instagram_config,
)

LOGIN_URL_DEFAULT = "https://business.facebook.com/"

LOGIN_PROMPT = (
    "\n>>> Log in to Meta inside the opened Chrome window.\n"
    "    Make sure both Facebook and the connected Instagram business account are accessible.\n"
    "    Then return here and press Enter to save the session...\n"
)


def main() -> int:
    args = parse_bootstrap_args("One-time Meta (FB+IG) session bootstrap.")
    return run_bootstrap(
        platform_label="Meta (Instagram + Facebook)",
        logger_name="instagram_bootstrap",
        load_config=load_instagram_config,
        resolve_user_data_dir=_resolve_user_data_dir,
        configure_logger=configure_logger,
        default_login_url=LOGIN_URL_DEFAULT,
        login_prompt=LOGIN_PROMPT,
        debug=args.debug,
    )


if __name__ == "__main__":
    raise SystemExit(main())
