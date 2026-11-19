"""The image worker and the service's client for it.

In production every image is processed by a fresh ``wpmcp media-worker`` process that
systemd starts per connection (``wpmcp-media.socket``, ``Accept=yes``) with the connection
on stdin and stdout. That process holds no credential, cannot read the service's files or
reach WordPress, and is the only place where untrusted images are decoded or where a
connection to the internet is opened (ChatGPT's file links, and the image hosts the owner
listed). The main service keeps its network sealed to loopback and never imports Pillow.

Each download op has its own host list, held by the worker itself: ChatGPT's file hosts
for "fetch", and for "fetch_listed" the owner's ``WPMCP_IMAGE_HOSTS`` from the worker's
own environment (a systemd drop-in written at deploy). The request names a URL, never the
hosts, so the service cannot reach any other address through the worker.

Protocol, one request per connection:

    request   one JSON line; for "sanitize" followed by ``length`` raw bytes
              {"op": "sanitize", "length": N}  |  {"op": "fetch", "url": "https://..."}
              {"op": "fetch_listed", "url": "https://..."}
    response  one JSON line; on success followed by ``length`` raw bytes
              {"ok": true, "format": "JPEG", "width": W, "height": H, "length": M}
              {"ok": false, "error": "<message safe to show>"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, BinaryIO

import httpx

from ..config import Settings, parse_image_hosts
from .fetch import DOWNLOAD_HOSTS, Resolver, download, system_resolver
from .images import MAX_BYTES, CleanImage, ImageError, sanitize

log = logging.getLogger(__name__)
MAX_HEADER = 8192
# A re-encoded PNG can be larger than its source; anything beyond this is not an image.
MAX_RESULT = 4 * MAX_BYTES
TIMEOUT = 90.0
GENERIC_ERROR = "The image could not be processed."


# ------------------------------------------------------------------- worker process
def serve(
    stdin: BinaryIO,
    stdout: BinaryIO,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver = system_resolver,
    image_hosts: tuple[str, ...] | None = None,
) -> int:
    """Answer one request from ``stdin`` on ``stdout``. Always answers; returns 0.

    ``image_hosts`` (tests) stands for the worker's ``WPMCP_IMAGE_HOSTS``."""
    try:
        request = _parse(stdin.readline(MAX_HEADER + 1))
        op = request.get("op")
        if op == "sanitize":
            length = request.get("length")
            if not isinstance(length, int) or not 0 < length <= MAX_BYTES:
                raise ImageError(f"image too large or empty (max {MAX_BYTES} bytes)")
            data = stdin.read(length)
            if len(data) != length:
                raise ImageError("incomplete upload")
        elif op in ("fetch", "fetch_listed"):
            url = request.get("url")
            if not isinstance(url, str) or len(url) > 4096:
                raise ImageError("bad download_url")
            hosts = DOWNLOAD_HOSTS if op == "fetch" else _listed_hosts(image_hosts)
            data = asyncio.run(
                download(
                    url,
                    allowed_hosts=hosts,
                    max_bytes=MAX_BYTES,
                    transport=transport,
                    resolver=resolver,
                )
            )
        else:
            raise ImageError("unknown operation")
        image = sanitize(data)
        _answer(stdout, {"ok": True, **_describe(image)}, image.data)
    except ImageError as exc:
        _answer(stdout, {"ok": False, "error": str(exc)})
    except Exception:
        log.exception("image worker failed")
        _answer(stdout, {"ok": False, "error": GENERIC_ERROR})
    return 0


def _listed_hosts(override: tuple[str, ...] | None) -> tuple[str, ...]:
    hosts = override
    if hosts is None:
        try:
            hosts = parse_image_hosts(os.environ.get("WPMCP_IMAGE_HOSTS", ""))
        except ValueError as exc:
            raise ImageError("the site's image host list is not valid") from exc
    if not hosts:
        raise ImageError("no image hosts are configured for this site")
    return hosts


def _parse(line: bytes) -> dict[str, Any]:
    if len(line) > MAX_HEADER or not line.endswith(b"\n"):
        raise ImageError("bad request")
    try:
        request = json.loads(line)
    except ValueError as exc:
        raise ImageError("bad request") from exc
    if not isinstance(request, dict):
        raise ImageError("bad request")
    return request


def _describe(image: CleanImage) -> dict[str, Any]:
    return {
        "format": image.format,
        "width": image.width,
        "height": image.height,
        "length": len(image.data),
    }


def _answer(stdout: BinaryIO, header: dict[str, Any], data: bytes = b"") -> None:
    stdout.write(json.dumps(header).encode() + b"\n" + data)
    stdout.flush()


# ------------------------------------------------------------------ service side
class MediaProcessor:
    """What the service calls: the worker socket in production, in-process otherwise."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = system_resolver,
    ) -> None:
        self.socket = settings.media_socket
        self.max_bytes = min(settings.media_max_bytes, MAX_BYTES)
        self.image_hosts = settings.image_hosts
        self._transport = transport
        self._resolver = resolver

    async def sanitize(self, data: bytes) -> CleanImage:
        if not data:
            raise ImageError("empty file")
        if len(data) > self.max_bytes:
            raise ImageError(f"image too large ({len(data)} bytes, max {self.max_bytes})")
        if self.socket:
            return await self._ask({"op": "sanitize", "length": len(data)}, data)
        return await asyncio.to_thread(sanitize, data, max_bytes=self.max_bytes)

    async def fetch(self, url: str) -> CleanImage:
        """A file ChatGPT hands over (its file hosts only)."""
        return await self._fetch("fetch", url, DOWNLOAD_HOSTS)

    async def fetch_listed(self, url: str) -> CleanImage:
        """An image on one of the owner's image hosts (``WPMCP_IMAGE_HOSTS``)."""
        if not self.image_hosts:
            raise ImageError("no image hosts are configured for this site")
        return await self._fetch("fetch_listed", url, self.image_hosts)

    async def _fetch(self, op: str, url: str, hosts: tuple[str, ...]) -> CleanImage:
        if self.socket:
            return await self._ask({"op": op, "url": url})
        data = await download(
            url,
            allowed_hosts=hosts,
            max_bytes=self.max_bytes,
            transport=self._transport,
            resolver=self._resolver,
        )
        return await asyncio.to_thread(sanitize, data, max_bytes=self.max_bytes)

    async def _ask(self, request: dict[str, Any], data: bytes = b"") -> CleanImage:
        try:
            return await asyncio.wait_for(self._exchange(request, data), TIMEOUT)
        except ImageError:
            raise
        except (OSError, TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
            log.warning("image worker unavailable: %s", type(exc).__name__)
            raise ImageError(GENERIC_ERROR) from exc

    async def _exchange(self, request: dict[str, Any], data: bytes) -> CleanImage:
        reader, writer = await asyncio.open_unix_connection(self.socket, limit=MAX_HEADER + 1)
        try:
            writer.write(json.dumps(request).encode() + b"\n" + data)
            await writer.drain()
            header = json.loads(await reader.readuntil(b"\n"))
            if not header.get("ok"):
                raise ImageError(str(header.get("error") or GENERIC_ERROR)[:300])
            length = header["length"]
            if header.get("format") not in ("JPEG", "PNG", "WEBP") or not 0 < length <= MAX_RESULT:
                raise ValueError("bad worker answer")
            body = await reader.readexactly(length)
            return CleanImage(body, header["format"], int(header["width"]), int(header["height"]))
        finally:
            writer.close()
            await writer.wait_closed()
