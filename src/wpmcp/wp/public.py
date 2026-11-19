"""Reads the site's public pages as a visitor or a search engine would: GET only, without
credentials or cookies, on the site's own host only (in production connected to the
loopback address, like the bridge client), redirects reported rather than followed, sizes
capped. Callers pass addresses that come from the bridge (permalinks of published items)
or fixed paths (/robots.txt, /sitemap.xml), never one from the model: anything off the
site's origin is refused here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from ..config import Settings
from .client import WordPressError, _transport

PAGE_MAX_BYTES = 3 * 1024 * 1024
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PublicPage:
    url: str
    status: int
    location: str | None
    content_type: str
    robots_header: str | None
    text: str


class PublicSite:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        parts = urlsplit(settings.wp_url)
        self.origin = f"{parts.scheme}://{parts.netloc}".lower()
        self._http = httpx.AsyncClient(
            transport=transport or _transport(settings),
            timeout=settings.wp_timeout,
            follow_redirects=False,
            headers={"user-agent": "wpmcp site check", "accept": "*/*"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def on_site(self, url: str) -> bool:
        """Whether ``url`` is an address of this site (same scheme, host and port)."""
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}".lower() == self.origin and not parts.fragment

    async def get(self, url: str, *, max_bytes: int = PAGE_MAX_BYTES) -> PublicPage:
        """One page of this site. Raises WordPressError when it cannot be read."""
        if not self.on_site(url):
            raise ValueError(f"not an address of this site: {url!r}")
        try:
            async with self._http.stream("GET", url) as response:
                body = await _read(response, max_bytes)
        except httpx.HTTPError as exc:
            log.warning("public page unreachable: %s", type(exc).__name__)
            raise WordPressError("The site's public pages could not be reached.") from exc
        return PublicPage(
            url=url,
            status=response.status_code,
            location=response.headers.get("location"),
            content_type=response.headers.get("content-type", ""),
            robots_header=response.headers.get("x-robots-tag"),
            text=body.decode(response.encoding or "utf-8", errors="replace"),
        )


async def _read(response: httpx.Response, max_bytes: int) -> bytes:
    chunks = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > max_bytes:
            raise WordPressError(f"{response.url} is larger than {max_bytes // 1024} KiB.")
        chunks.append(chunk)
    return b"".join(chunks)
