"""The recorder's HTTP server: the page, the chunk upload API and the call's
signalling, behind a per-episode link token.

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
* ``POST /api/{token}/rec/{rid}/finish`` — ``{"total", "ext", "kind"}``: join, remux into
  ``<episode>/video editing/``, reply with the file name and duration
* ``GET  /api/{token}/ice`` — the STUN/TURN servers for the call
* ``WS   /ws/{token}`` — signalling: the page's offers, answers and ICE
  candidates are relayed to the other side (#342)

``kind`` is ``main`` (this side's camera and mic) or ``ref`` (the other side's
voice as received over the call, low quality: the reference the sync stage
aligns the two full-quality tracks against, #343).

The call is peer to peer: the server only relays signalling. One socket per
side; a side that reconnects replaces its stale socket. Each page load names
itself (``?peer=<id>``) and presence messages carry the other side's id, so a
page can tell a reloaded peer (new id: start the call afresh) from the same
page reconnecting its signalling (same id: keep the call).
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Optional

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from recorder.store import RECORDING_ID, ChunkStore, MissingChunks

logger = logging.getLogger("recorder.server")

LINKS_FILE = "recorder.json"
SIDES = ("host", "guest")
OUTPUT_DIR = "video editing"
MAX_CHUNK_BYTES = 64 * 1024 * 1024  # a 4K chunk at ~40 Mbps is ~12 MB
STATIC = Path(__file__).parent / "static"
KINDS = {"main": "{side}", "ref": "{side} remote-ref"}
SIGNALS = ("offer", "answer", "candidate", "bye")
MAX_SIGNAL_BYTES = 64 * 1024
DEFAULT_STUN = ["stun:stun.l.google.com:19302"]
UNKNOWN_LINK = 4404  # WebSocket close code for a bad token
PEER_ID = re.compile(r"^[0-9a-f-]{8,64}$")
REPLACED = 4001      # ... for a socket its side opened again


def reference_of(payload: dict) -> dict:
    """For a ``ref`` recording: which main recording of this side it ran
    beside, and where in that recording it starts (seconds, measured on the
    page's own clock). The sync stage needs both to place the reference."""
    if payload.get("main") is None:
        return {}
    main, offset = str(payload["main"]), float(payload["offset_s"])
    if not RECORDING_ID.match(main) or not -60.0 <= offset <= 86400.0:
        raise ValueError("bad main/offset_s")
    return {"main": main, "offset_in_main_s": round(offset, 4)}


def ice_servers(rcfg: dict) -> list[dict]:
    """STUN plus, when configured, the hosted TURN relay. The TURN credential
    comes from ``.env`` (``RECORDER_TURN_USERNAME`` / ``RECORDER_TURN_CREDENTIAL``)
    when set there, else from ``podcast.recorder.turn``."""
    servers = [{"urls": rcfg.get("stun_urls") or DEFAULT_STUN}]
    turn = rcfg.get("turn") or {}
    if turn.get("urls"):
        servers.append({"urls": turn["urls"],
                        "username": os.environ.get("RECORDER_TURN_USERNAME") or turn.get("username", ""),
                        "credential": os.environ.get("RECORDER_TURN_CREDENTIAL") or turn.get("credential", "")})
    return servers


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


class Room:
    """The two sides' signalling sockets for one episode."""

    def __init__(self) -> None:
        self.sockets: dict[str, WebSocket] = {}
        self.peers: dict[str, str] = {}  # side -> the page's own id

    def other(self, side: str) -> Optional[WebSocket]:
        return next((ws for s, ws in self.sockets.items() if s != side), None)

    def other_peer(self, side: str) -> Optional[str]:
        return next((pid for s, pid in self.peers.items() if s != side and s in self.sockets), None)

    async def tell(self, ws: Optional[WebSocket], message: dict) -> None:
        if ws is not None:
            try:
                await ws.send_json(message)
            except (RuntimeError, WebSocketDisconnect):
                pass  # it is going away; its own handler cleans up

    async def presence(self) -> None:
        for side, ws in list(self.sockets.items()):
            await self.tell(ws, {"type": "peer", "present": self.other(side) is not None,
                                 "peer": self.other_peer(side)})


def create_app(episode: Path, store: ChunkStore, links: dict[str, str],
               ice: Optional[list[dict]] = None) -> Starlette:
    """The app for one episode folder."""
    out_dir = episode / OUTPUT_DIR
    ice = ice or [{"urls": DEFAULT_STUN}]
    room = Room()

    def lookup(token: str) -> str:
        for known, side in links.items():
            if secrets.compare_digest(known, token):
                return side
        raise LookupError

    def side_of(request: Request) -> str:
        return lookup(request.path_params["token"])

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
    async def ice_config(request: Request, side: str) -> Response:
        return JSONResponse({"iceServers": ice}, headers={"Cache-Control": "no-store"})

    async def signal(ws: WebSocket) -> None:
        try:
            side = lookup(ws.path_params["token"])
        except LookupError:
            await ws.close(code=UNKNOWN_LINK)
            return
        peer = ws.query_params.get("peer", "")
        if not PEER_ID.match(peer):
            await ws.close(code=1008)
            return
        await ws.accept()
        stale = room.sockets.get(side)
        room.sockets[side], room.peers[side] = ws, peer
        if stale is not None:
            await stale.close(code=REPLACED)
        logger.info("ℹ️ %s joined the call", side)
        await room.presence()
        try:
            while True:
                text = await ws.receive_text()
                if len(text) > MAX_SIGNAL_BYTES:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    continue
                if isinstance(message, dict) and message.get("type") in SIGNALS:
                    await room.tell(room.other(side), {**message, "from": side})
        except WebSocketDisconnect:
            pass
        finally:
            if room.sockets.get(side) is ws:
                del room.sockets[side]
                logger.info("ℹ️ %s left the call", side)
                await room.presence()

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
            total, ext, kind = int(payload["total"]), str(payload["ext"]), payload.get("kind", "main")
            label = KINDS[kind].format(side=side)
            sidecar = {"kind": kind, **reference_of(payload)}
        except (KeyError, TypeError, ValueError):
            return _bad(400, "finish needs total, ext, a known kind and a valid main/offset_s")
        rid = request.path_params["rid"]
        try:
            result = await run_in_threadpool(store.finish, side, rid, total, ext, out_dir, label=label,
                                              sidecar=sidecar)
        except MissingChunks as exc:
            return JSONResponse({"error": str(exc), "missing": exc.missing[:1000]}, status_code=409)
        return JSONResponse(result)

    routes = [
        Route("/r/{token}", page),
        Route("/api/{token}", info),
        Route("/api/{token}/ice", ice_config),
        WebSocketRoute("/ws/{token}", signal),
        Route("/api/{token}/rec/{rid}/chunk/{seq:int}", chunk, methods=["PUT"]),
        Route("/api/{token}/rec/{rid}/have", have),
        Route("/api/{token}/rec/{rid}/finish", finish, methods=["POST"]),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ]
    return Starlette(routes=routes)
