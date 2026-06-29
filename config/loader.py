"""Single-source config.json loader.

Every module that needs the project config reads it through here instead of
re-implementing its own open/parse helper. ``load_full_config`` is cached so
the file is read once per process; ``load_block`` resolves a named top-level
block and fails loud when it's missing.

Both raise on a missing or corrupt ``config.json`` rather than returning
``None`` — config is mandatory, so a clear exception beats a silent skip.
This matches the contracts already used by ``reporting/scrape_client/base.py``
and ``planning/_session_base.py``.
"""

import json
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


@lru_cache(maxsize=1)
def load_full_config() -> dict:
    """Load and return the full ``config.json`` as a dict (cached per process)."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
        return json.load(fp)


def load_block(name: str) -> dict:
    """Return a named top-level block from ``config.json``.

    Raises ``RuntimeError`` if the block is missing or empty.
    """
    block = load_full_config().get(name)
    if not block:
        raise RuntimeError(f"Missing '{name}' block in config.json")
    return block
