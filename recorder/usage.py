"""The recorder's usage log: the fleet's vendored router behind a Starlette route,
with reads limited to the owner.

``src/usage_log.py`` (vendored verbatim) is a FastAPI router. The recorder is a
Starlette app, so the router lives in a small FastAPI app that one route hands
every ``/api/usage/...`` request to. That route comes before the ``/api/{token}``
ones, so a usage request is never read as a link token and no token route sees
a different request than it did.

``POST /api/usage/events`` is open to every caller of the page, guests included:
the page's controls are what the log counts. ``GET /api/usage/summary`` and
``/time`` answer only the owner, because they show how the owner uses the tool.

Who is the owner: the server binds loopback and a guest arrives through
Tailscale Funnel, which connects from the machine itself. The peer address
therefore cannot tell them apart, so the headers Tailscale's proxy sets decide
(``Tailscale-Funnel-Request`` marks a funnelled request and
``X-Forwarded-For`` carries the caller's address, replaced, never appended).
A request with no proxy header, or whose forwarded address is loopback or on the
tailnet, is the owner; anything else, or an address that does not parse, is not.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.routing import Route

from src.usage_log import usage_router

TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
FUNNEL_HEADER = "tailscale-funnel-request"
FORWARDED_HEADER = "x-forwarded-for"


def _address(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """``value`` as an address (an IPv4-mapped IPv6 one as its IPv4), or ``None``."""
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


def _local_or_tailnet(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_loopback or ip in TAILNET_V4 or ip in TAILNET_V6


def is_owner(peer: str | None, headers: Mapping[str, str]) -> bool:
    """Whether a request comes from this machine or the owner's tailnet."""
    peer_ip = _address(peer)
    if peer_ip is None:
        return False
    if not peer_ip.is_loopback:
        return _local_or_tailnet(peer_ip)
    if headers.get(FUNNEL_HEADER) is not None:
        return False
    forwarded = headers.get(FORWARDED_HEADER)
    if forwarded is None:
        return True
    origin = _address(forwarded.split(",")[-1])
    return origin is not None and _local_or_tailnet(origin)


def owner_reads(request: Request) -> None:
    """Router dependency: a read (anything but the event POST) is for the owner only."""
    if request.method == "POST":
        return
    if not is_owner(request.client.host if request.client else None, request.headers):
        raise HTTPException(status_code=403, detail="owner only")


def usage_route(db_path: Path) -> Route:
    """The route that serves ``/api/usage/...`` from the usage log at ``db_path``."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(usage_router(db_path, dependencies=[Depends(owner_reads)]))
    return Route("/api/usage/{rest:path}", app)
