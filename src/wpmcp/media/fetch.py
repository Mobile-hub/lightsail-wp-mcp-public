"""Download a file that ChatGPT hands over (``openai/fileParams`` -> ``download_url``).

Guards: https only, host must match the allowlist (exact, ``.suffix`` for any subdomain,
or ``*`` standing for part of ONE label, as in ``oaisdmntpr*.blob.core.windows.net``),
every resolved address must be public (no SSRF into localhost/LAN), redirects are
followed by hand and re-checked hop by hop, size is capped while streaming, no
cookies or credentials are ever sent. In production this runs only in the image worker,
whose systemd unit also denies private and loopback destinations at the kernel level
(that closes the gap between this resolution and httpx's own).

Copied from editorial.workflow ``editorial_assets/fetch.py`` (see PROVENANCE.md); only the
error class changed. Do not widen the host list: it is what keeps this from being a
general URL fetcher.
"""

from __future__ import annotations

import asyncio
import functools
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx

from .images import ImageError

Resolver = Callable[[str], Awaitable[list[str]]]
MAX_REDIRECTS = 3
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Where ChatGPT serves files passed to tools.
DOWNLOAD_HOSTS = (
    ".oaiusercontent.com",
    ".openai.com",
    ".chatgpt.com",
    "oaisdmntpr*.blob.core.windows.net",
)


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def host_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in allowed:
        if pattern.startswith("."):
            if host.endswith(pattern) and len(host) > len(pattern):
                return True
        elif "*" in pattern:
            if _glob(pattern).fullmatch(host):
                return True
        elif host == pattern:
            return True
    return False


@functools.lru_cache(maxsize=64)
def _glob(pattern: str) -> re.Pattern[str]:
    """``*`` matches one or more label characters, never a dot: ``oai*.blob.core.windows.net``
    takes ``oaisdmntprnortheu.blob.core.windows.net`` but not ``oai.evil.blob.core.windows.net``."""
    parts = re.split(r"(\*)", pattern.lower())
    return re.compile("".join("[a-z0-9-]+" if part == "*" else re.escape(part) for part in parts))


async def check_url(url: str, allowed: tuple[str, ...], resolver: Resolver) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ImageError("download_url must be https")
    if parts.username or parts.password:
        raise ImageError("download_url must not contain credentials")
    host = parts.hostname or ""
    if not host_allowed(host, allowed):
        raise ImageError(f"download host not allowed: {host}")
    try:
        addresses = await resolver(host)
    except OSError as exc:
        raise ImageError(f"cannot resolve {host}") from exc
    if not addresses:
        raise ImageError(f"cannot resolve {host}")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ImageError(f"{host} resolves to a non-public address")


async def download(
    url: str,
    *,
    allowed_hosts: tuple[str, ...] = DOWNLOAD_HOSTS,
    max_bytes: int,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver = system_resolver,
) -> bytes:
    async with httpx.AsyncClient(
        transport=transport, timeout=TIMEOUT, follow_redirects=False, trust_env=False
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url, allowed_hosts, resolver)
            async with client.stream("GET", url, headers={"accept": "image/*"}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code != 200:
                    raise ImageError(f"download failed with HTTP {response.status_code}")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise ImageError(f"image too large ({declared} bytes, max {max_bytes})")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise ImageError(f"image too large (max {max_bytes} bytes)")
                    chunks.append(chunk)
                return b"".join(chunks)
        raise ImageError("too many redirects")
