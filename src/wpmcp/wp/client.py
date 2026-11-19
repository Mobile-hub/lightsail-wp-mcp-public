"""HTTP client for the WordPress bridge routes (``/wp-json/wpmcp/v1``).

It reaches only that namespace (the plugin also denies everything else to the integration
user), never follows redirects, caps response sizes and, in production, connects to the
site's own loopback address while keeping the real host name for TLS and Host: the
Application Password is accepted from loopback only. Public pages, read without the
credential, go through ``public`` (wp/public.py).
"""

from __future__ import annotations

import base64
import json
import logging
import re
import ssl
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

from ..audit import audit
from ..config import Settings

BRIDGE_PREFIX = "/wp-json/wpmcp/v1"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_PATH = re.compile(r"^/[a-z0-9/-]*$")

log = logging.getLogger(__name__)


class WordPressError(Exception):
    """WordPress answered with an error, or could not be reached."""

    def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """Connects every request for ``host`` to ``ip``; refuses any other host."""

    def __init__(self, host: str, ip: str) -> None:
        self._host = host
        self._ip = ip
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host != self._host:
            raise httpcore.ConnectError(f"refusing to connect to {host!r}")
        return await self._inner.connect_tcp(
            self._ip,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError("unix sockets are not used")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def _transport(settings: Settings) -> httpx.AsyncHTTPTransport:
    context = ssl.create_default_context()
    transport = httpx.AsyncHTTPTransport(verify=context, retries=0)
    if settings.wp_connect_ip:
        host = urlsplit(settings.wp_url).hostname or ""
        # httpx has no public hook for the network backend: swap its connection pool for
        # one with the pinned backend (same SSL context, HTTP/1.1 only).
        transport._pool = httpcore.AsyncConnectionPool(
            ssl_context=context,
            network_backend=PinnedBackend(host, settings.wp_connect_ip),
            retries=0,
        )
    return transport


class WordPressClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        from .public import PublicSite  # it imports this module

        token = base64.b64encode(f"{settings.wp_user}:{settings.wp_password}".encode()).decode()
        self._base = settings.wp_url + BRIDGE_PREFIX
        # A separate client: no Authorization header ever goes to a public page.
        self.public = PublicSite(settings, transport=transport)
        self._http = httpx.AsyncClient(
            transport=transport or _transport(settings),
            timeout=settings.wp_timeout,
            follow_redirects=False,
            headers={
                "authorization": f"Basic {token}",
                "accept": "application/json",
                "user-agent": "wpmcp",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()
        await self.public.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, body: dict[str, Any]) -> Any:
        """JSON body; keys whose value is None are left out (the bridge treats a missing
        key as "leave unchanged")."""
        return await self._request(
            "POST", path, json={k: v for k, v in body.items() if v is not None}
        )

    async def upload(
        self, path: str, *, data: bytes, filename: str, mime: str, fields: dict[str, Any]
    ) -> Any:
        """Multipart upload of one file (``file``) with form fields."""
        form = {k: _param(v) for k, v in fields.items() if v is not None and v != ""}
        return await self._request("POST", path, data=form, files={"file": (filename, data, mime)})

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, **body: Any
    ) -> Any:
        if not _PATH.match(path):
            raise ValueError(f"bad bridge path {path!r}")
        query = {k: _param(v) for k, v in (params or {}).items() if v is not None and v != ""}
        try:
            async with self._http.stream(
                method, self._base + path, params=query, **body
            ) as response:
                body_bytes = await _read_capped(response)
        except httpx.HTTPError as exc:
            log.warning("wordpress unreachable: %s", type(exc).__name__)
            raise WordPressError("WordPress could not be reached.") from exc
        return _decode(response, body_bytes, path)


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


async def _read_capped(response: httpx.Response) -> bytes:
    chunks = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise WordPressError("WordPress returned a response that is too large.")
        chunks.append(chunk)
    return b"".join(chunks)


def _decode(response: httpx.Response, body: bytes, path: str) -> Any:
    status = response.status_code
    try:
        data = json.loads(body) if body else None
    except ValueError:
        data = None
    if 200 <= status < 300 and data is not None:
        return data
    if status in (401, 403) and not (isinstance(data, dict) and _is_bridge_error(data)):
        # The credential was refused (or the route is not allowed): an operator problem.
        audit("wp_auth_failed", level=logging.ERROR, status=status, path=path)
        raise WordPressError(
            "WordPress refused the server's credentials. The site owner must check the "
            "integration user and its Application Password.",
            status=status,
        )
    if isinstance(data, dict) and isinstance(data.get("message"), str):
        message = re.sub(r"<[^>]+>", "", data["message"])[:300]
        raise WordPressError(message, status=status, code=str(data.get("code", "")))
    if 300 <= status < 400:
        raise WordPressError(
            "WordPress answered with a redirect: check WPMCP_WP_URL.", status=status
        )
    raise WordPressError(f"WordPress answered with HTTP {status}.", status=status)


def _is_bridge_error(data: dict) -> bool:
    """Errors the bridge raises on purpose (not found, bad language) are not auth failures."""
    return str(data.get("code", "")).startswith("wpmcp_") and data.get("code") not in {
        "wpmcp_route_denied",
        "wpmcp_remote_address",
        "wpmcp_user",
    }
