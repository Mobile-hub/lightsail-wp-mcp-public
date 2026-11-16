"""Client address behind the local reverse proxy (Apache mod_proxy).

uvicorn runs with ``proxy_headers=False`` (see ``cli.py``), so ``request.client`` is
always the TCP peer. ``X-Forwarded-For`` counts only when that peer is a trusted proxy,
and only its *last* value: the proxy appends the address it saw, so earlier values are
client-controlled. ``X-Forwarded-Host`` and ``X-Forwarded-Proto`` are never used: the
public origin is the fixed ``WPMCP_PUBLIC_URL``.

Adapted from editorial.workflow ``mcp_oauth_gateway/network.py`` (see PROVENANCE.md).
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

from .config import Settings


def peer_ip(request: Request) -> str:
    """Address of the TCP peer; empty for a Unix-socket connection."""
    return (request.client.host if request.client else "") or ""


def peer_is_trusted(request: Request, settings: Settings) -> bool:
    peer = peer_ip(request)
    if not peer:
        return settings.trust_unix_peer
    return _in(peer, settings.trusted_proxy_cidrs)


def client_ip(request: Request, settings: Settings) -> str:
    """Peer address, or the address the proxy appended to X-Forwarded-For when it is trusted."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and peer_is_trusted(request, settings):
        # The proxy appends the real client last; anything before it is client-controlled.
        return forwarded.split(",")[-1].strip()
    return peer_ip(request)


def _in(ip: str, networks) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in net for net in networks)
