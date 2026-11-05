"""Static-asset cache-busting for the recorder's ``/static`` mount.

A bare Starlette ``StaticFiles`` sends only ``ETag`` and ``Last-Modified``, so an
installed iOS PWA heuristic-caches the page's JS and CSS and keeps running the old
build after a deploy. The fix is one **fleet hash** (a SHA-256 over every ``.js`` and
``.css`` file under the static folder) used as the cache key of every asset URL, plus
an explicit ``Cache-Control`` per suffix and a shell that always revalidates:

* ``stamp_assets`` appends ``?v=<hash>`` to each quoted ``/static/...js|css`` URL, in
  ``index.html`` and in each served ``.js`` (the page imports its vendored modules
  with absolute ``import("/static/...")`` URLs, so a stale copy of one would never be
  refetched otherwise),
* ``CachingStaticFiles`` serves ``.js`` / ``.css`` as immutable (the stamp changes on
  any edit) and the manifest and icons for a day.

One hash for the whole graph, not one per file: a changed module must rotate the URL
of every module that imports it. This is the only module that touches
``StaticFiles``; the conventions ratchet's ``must_use`` entry names it as the helper.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

logger = logging.getLogger(__name__)

_HASH_LEN = 8
_HASHED_SUFFIXES = (".js", ".css")
_LONG_CACHE = "public, max-age=31536000, immutable"  # the stamp is the cache key
_DAY_CACHE = "public, max-age=86400"  # manifest and icons: rarely change, never for a year
_DAILY_SUFFIXES = frozenset({".webmanifest", ".png", ".ico"})

# A quoted /static/ URL to a .js or .css file, with any ?v= already on it captured so
# stamping a body twice gives the same result.
_ASSET_URL = re.compile(r"""(["'])(/static/[\w\-./]+\.(?:js|css))(?:\?v=[^"']*)?\1""")


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:_HASH_LEN]


def fleet_hash(static_dir: Path) -> str:
    """The one hash of every ``.js`` / ``.css`` under ``static_dir``, or ``""`` when
    there are none or one cannot be read: a partial deploy then serves unstamped URLs
    instead of failing the page."""
    if not static_dir.exists():
        return ""
    parts = []
    for path in sorted(static_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _HASHED_SUFFIXES:
            continue
        try:
            parts.append(f"{path.relative_to(static_dir).as_posix()}:{_short_hash(path.read_bytes())}")
        except OSError as exc:
            logger.warning("⚠️ could not hash %s: %s", path, exc)
            return ""
    return _short_hash("\n".join(parts).encode("utf-8")) if parts else ""


def stamp_assets(body: str, asset_hash: str) -> str:
    """``body`` with ``?v=<asset_hash>`` on each quoted ``/static/...js|css`` URL;
    unchanged when ``asset_hash`` is empty."""
    if not asset_hash:
        return body
    return _ASSET_URL.sub(lambda m: f"{m[1]}{m[2]}?v={asset_hash}{m[1]}", body)


class CachingStaticFiles(StaticFiles):
    """``StaticFiles`` that sends an explicit ``Cache-Control`` per suffix and stamps
    the asset URLs inside each served ``.js``."""

    def __init__(self, *, directory: Path, asset_hash: str) -> None:
        super().__init__(directory=directory)
        self.asset_hash = asset_hash

    def file_response(self, full_path, stat_result, scope: Scope, status_code: int = 200) -> Response:
        path = Path(full_path)
        suffix = path.suffix.lower()
        if suffix == ".js":
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                logger.warning("⚠️ could not stamp %s, serving it as stored: %s", path, exc)
            else:
                return Response(stamp_assets(body, self.asset_hash), status_code=status_code,
                                media_type="text/javascript", headers={"Cache-Control": _LONG_CACHE})
        response = super().file_response(full_path, stat_result, scope, status_code)
        if suffix in _HASHED_SUFFIXES:
            response.headers["Cache-Control"] = _LONG_CACHE
        elif suffix in _DAILY_SUFFIXES:
            response.headers["Cache-Control"] = _DAY_CACHE
        return response
