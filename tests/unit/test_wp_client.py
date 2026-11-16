from __future__ import annotations

import asyncio
import base64
import json
import logging

import httpcore
import httpx
import pytest

from tests.conftest import make_settings
from wpmcp.wp import client as wp_client
from wpmcp.wp.client import PinnedBackend, WordPressClient, WordPressError

WP_URL = "https://books.example.test"


def wp_settings(**overrides):
    values = dict(wp_url=WP_URL, wp_user="wpmcp", wp_password="abcd efgh ijkl mnop")
    values.update(overrides)
    return make_settings(**values)


def make_client(handler, **overrides) -> WordPressClient:
    return WordPressClient(wp_settings(**overrides), transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


async def get(client: WordPressClient, path: str, params=None):
    try:
        return await client.get(path, params)
    finally:
        await client.aclose()


def test_get_sends_credentials_and_drops_empty_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"ok": True})

    result = run(
        get(make_client(handler), "/content", {"lang": "es", "search": "", "x": None, "f": True})
    )
    assert result == {"ok": True}
    assert seen["url"] == f"{WP_URL}/wp-json/wpmcp/v1/content?lang=es&f=true"
    user, password = base64.b64decode(seen["auth"].split()[1]).decode().split(":", 1)
    assert (user, password) == ("wpmcp", "abcd efgh ijkl mnop")


@pytest.mark.parametrize("path", ["content", "/../wp/v2/users", "/content?x=1", "/a b"])
def test_rejects_paths_outside_the_bridge_shape(path):
    with pytest.raises(ValueError):
        run(get(make_client(lambda r: httpx.Response(200, json={})), path))


def test_credentials_refused_is_reported_and_audited(caplog):
    def handler(request):
        return httpx.Response(401, json={"code": "rest_forbidden", "message": "nope"})

    with (
        caplog.at_level(logging.ERROR, logger="wpmcp.audit"),
        pytest.raises(WordPressError, match="refused the server's credentials") as err,
    ):
        run(get(make_client(handler), "/overview"))
    assert err.value.status == 401
    assert "wp_auth_failed" in caplog.text


@pytest.mark.parametrize("code", ["wpmcp_route_denied", "wpmcp_remote_address"])
def test_bridge_access_denials_count_as_credential_problems(code):
    def handler(request):
        return httpx.Response(403, json={"code": code, "message": "denied"})

    with pytest.raises(WordPressError, match="credentials"):
        run(get(make_client(handler), "/overview"))


def test_bridge_errors_pass_their_message_without_markup():
    def handler(request):
        return httpx.Response(
            404, json={"code": "wpmcp_not_found", "message": "No <b>post</b> with that ID."}
        )

    with pytest.raises(WordPressError) as err:
        run(get(make_client(handler), "/content/9"))
    assert str(err.value) == "No post with that ID."
    assert (err.value.status, err.value.code) == (404, "wpmcp_not_found")


def test_redirects_are_not_followed():
    def handler(request):
        return httpx.Response(301, headers={"location": "https://evil.example/"})

    with pytest.raises(WordPressError, match="redirect"):
        run(get(make_client(handler), "/overview"))


def test_non_json_error():
    def handler(request):
        return httpx.Response(502, text="<html>bad gateway</html>")

    with pytest.raises(WordPressError, match="HTTP 502"):
        run(get(make_client(handler), "/overview"))


def test_success_without_json_is_an_error():
    with pytest.raises(WordPressError, match="HTTP 200"):
        run(get(make_client(lambda r: httpx.Response(200, text="not json")), "/overview"))


def test_response_size_is_capped(monkeypatch):
    monkeypatch.setattr(wp_client, "MAX_RESPONSE_BYTES", 100)
    body = json.dumps({"x": "y" * 500})
    with pytest.raises(WordPressError, match="too large"):
        run(get(make_client(lambda r: httpx.Response(200, text=body)), "/overview"))


def test_unreachable(caplog):
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(WordPressError, match="could not be reached"):
        run(get(make_client(handler), "/overview"))


def test_pinned_backend_refuses_other_hosts():
    backend = PinnedBackend("books.example.test", "127.0.0.1")
    with pytest.raises(httpcore.ConnectError):
        run(backend.connect_tcp("evil.example", 443))
    with pytest.raises(httpcore.ConnectError):
        run(backend.connect_unix_socket("wp.sock"))
    run(backend.sleep(0))


def test_pinned_transport_connects_to_the_pinned_address_with_the_real_host():
    """A real socket: the URL names a host that does not resolve, the pin reaches loopback."""

    async def scenario():
        seen = {}

        async def serve(reader, writer):
            head = await reader.readuntil(b"\r\n\r\n")
            seen["head"] = head.decode()
            body = b'{"pinned": true}'
            writer.write(
                b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: "
                + str(len(body)).encode()
                + b"\r\nconnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        settings = make_settings(
            wp_url=f"http://wp.invalid:{port}",
            wp_password="secret",
            wp_connect_ip="127.0.0.1",
        )
        client = WordPressClient(settings, transport=wp_client._transport(settings))
        try:
            result = await client.get("/overview")
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()
        return result, seen["head"]

    result, head = run(scenario())
    assert result == {"pinned": True}
    assert "host: wp.invalid:" in head.lower()
    assert head.startswith("GET /wp-json/wpmcp/v1/overview HTTP/1.1")


def test_unpinned_transport_is_plain():
    transport = wp_client._transport(wp_settings())
    assert isinstance(transport, httpx.AsyncHTTPTransport)
