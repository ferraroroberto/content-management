"""The recorder's HTTP server: the page, and the chunk upload API behind a
per-episode link token.

Each episode gets two links, one per side (``host`` and ``guest``), kept in
``<episode folder>/recorder.json`` beside ``episode.json`` so a restart keeps
the links already sent. The token in the link is the only credential: a
request with an unknown token gets a 404, the same as a path that does not
exist.

Routes (``{token}`` is a link token, ``{rid}`` a recording id the page makes):

* ``GET  /r/{token}`` — the recorder page
* ``GET  /api/{token}`` — which side this link records
* ``PUT  /api/{token}/rec/{rid}/chunk/{seq}`` — one chunk (body = bytes), idempotent
* ``GET  /api/{token}/rec/{rid}/have`` — chunk numbers the server holds
* ``POST /api/{token}/rec/{rid}/finish`` — ``{"total", "ext"}``: join, remux into
  ``<episode>/video editing/``, reply with the file name and duration
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from recorder.store import ChunkStore, MissingChunks

logger = logging.getLogger("recorder.server")

LINKS_FILE = "recorder.json"
SIDES = ("host", "guest")
OUTPUT_DIR = "video editing"
MAX_CHUNK_BYTES = 64 * 1024 * 1024  # a 4K chunk at ~40 Mbps is ~12 MB
STATIC = Path(__file__).parent / "static"


def ensure_links(folder: Path) -> dict[str, str]:
    """``{token: side}`` for the episode, creating one random token per side the first time."""
    path = folder / LINKS_FILE
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"links": {}}
    links = data.setdefault("links", {})
    for side in SIDES:
        if side not in links.values():
            links[secrets.token_urlsafe(18)] = side
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return dict(links)


def _bad(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(episode: Path, store: ChunkStore, links: dict[str, str]) -> Starlette:
    """The app for one episode folder."""
    out_dir = episode / OUTPUT_DIR

    def side_of(request: Request) -> str:
        token = request.path_params["token"]
        for known, side in links.items():
            if secrets.compare_digest(known, token):
                return side
        raise LookupError

    def guarded(handler):
        async def wrapper(request: Request) -> Response:
            try:
                side = side_of(request)
            except LookupError:
                return _bad(404, "unknown link")
            try:
                return await handler(request, side)
            except ValueError as exc:
                return _bad(400, str(exc))
        return wrapper

    @guarded
    async def page(request: Request, side: str) -> Response:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @guarded
    async def info(request: Request, side: str) -> Response:
        return JSONResponse({"side": side})

    @guarded
    async def chunk(request: Request, side: str) -> Response:
        size = int(request.headers.get("content-length") or 0)
        if size > MAX_CHUNK_BYTES:
            return _bad(413, "chunk too large")
        body = await request.body()
        rid, seq = request.path_params["rid"], int(request.path_params["seq"])
        fresh = await run_in_threadpool(store.put, side, rid, seq, body)
        return JSONResponse({"ok": True, "duplicate": not fresh})

    @guarded
    async def have(request: Request, side: str) -> Response:
        rid = request.path_params["rid"]
        return JSONResponse({"have": await run_in_threadpool(store.have, side, rid),
                             "finished": await run_in_threadpool(store.finished, side, rid)})

    @guarded
    async def finish(request: Request, side: str) -> Response:
        payload = await request.json()
        try:
            total, ext = int(payload["total"]), str(payload["ext"])
        except (KeyError, TypeError, ValueError):
            return _bad(400, "finish needs total and ext")
        rid = request.path_params["rid"]
        try:
            result = await run_in_threadpool(store.finish, side, rid, total, ext, out_dir, label=side)
        except MissingChunks as exc:
            return JSONResponse({"error": str(exc), "missing": exc.missing[:1000]}, status_code=409)
        return JSONResponse(result)

    routes = [
        Route("/r/{token}", page),
        Route("/api/{token}", info),
        Route("/api/{token}/rec/{rid}/chunk/{seq:int}", chunk, methods=["PUT"]),
        Route("/api/{token}/rec/{rid}/have", have),
        Route("/api/{token}/rec/{rid}/finish", finish, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    return Starlette(routes=routes)
