from __future__ import annotations

import asyncio

import httpx
import pytest

from wpmcp.media.fetch import DOWNLOAD_HOSTS, check_url, download, host_allowed
from wpmcp.media.images import ImageError

FILE_URL = "https://files.oaiusercontent.com/file-abc?sig=1"


async def public(_host: str) -> list[str]:
    return ["20.60.1.1"]


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("files.oaiusercontent.com", True),
        ("oaisdmntprnortheu.blob.core.windows.net", True),
        ("oai.evil.blob.core.windows.net", False),
        ("oaisdmntpr.blob.core.windows.net.evil.com", False),
        ("oaiusercontent.com", False),
        ("evil-oaiusercontent.com", False),
        ("covers.openlibrary.org", False),
        ("localhost", False),
    ],
)
def test_host_allowlist(host, allowed):
    assert host_allowed(host, DOWNLOAD_HOSTS) is allowed


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://files.oaiusercontent.com/x", "https"),
        ("https://user:pw@files.oaiusercontent.com/x", "credentials"),
        ("https://example.org/x.png", "not allowed"),
    ],
)
def test_check_url_rejects(url, message):
    with pytest.raises(ImageError, match=message):
        run(check_url(url, DOWNLOAD_HOSTS, public))


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "fd00::1"])
def test_check_url_rejects_hosts_resolving_to_private_addresses(address):
    async def resolver(_host):
        return ["20.60.1.1", address]

    with pytest.raises(ImageError, match="non-public"):
        run(check_url(FILE_URL, DOWNLOAD_HOSTS, resolver))


def test_unresolvable_host():
    async def nothing(_host):
        return []

    async def failing(_host):
        raise OSError("no such host")

    for resolver in (nothing, failing):
        with pytest.raises(ImageError, match="cannot resolve"):
            run(check_url(FILE_URL, DOWNLOAD_HOSTS, resolver))


def fetch(handler, url=FILE_URL, max_bytes=1000):
    return run(
        download(url, max_bytes=max_bytes, transport=httpx.MockTransport(handler), resolver=public)
    )


def test_download_returns_the_body_and_sends_no_credentials():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"image-bytes")

    assert fetch(handler) == b"image-bytes"
    assert "authorization" not in seen[0].headers and "cookie" not in seen[0].headers


def test_redirects_are_checked_hop_by_hop():
    def handler(request):
        if request.url.host == "files.oaiusercontent.com":
            return httpx.Response(302, headers={"location": "https://evil.example/x.png"})
        return httpx.Response(200, content=b"should not be fetched")

    with pytest.raises(ImageError, match="not allowed"):
        fetch(handler)


def test_redirect_within_the_allowlist_is_followed():
    def handler(request):
        if request.url.path == "/file-abc":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, content=b"ok")

    assert fetch(handler) == b"ok"


def test_too_many_redirects():
    def handler(_request):
        return httpx.Response(302, headers={"location": "/again"})

    with pytest.raises(ImageError, match="too many redirects"):
        fetch(handler)


def test_size_cap_from_header_and_while_streaming():
    def declared(_request):
        return httpx.Response(200, content=b"x" * 10, headers={"content-length": "5000"})

    def streamed(_request):
        return httpx.Response(200, content=b"x" * 5000)

    for handler in (declared, streamed):
        with pytest.raises(ImageError, match="too large"):
            fetch(handler)


def test_http_errors():
    with pytest.raises(ImageError, match="HTTP 403"):
        fetch(lambda _request: httpx.Response(403))
