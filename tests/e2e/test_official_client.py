"""End to end over real HTTP: discovery, the OAuth flow, then the official MCP client.

Runs the real ASGI app under uvicorn on a loopback port, the way production does, and
talks to it only through public endpoints.
"""

import asyncio
import json
import secrets
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from wpmcp.app import create_app
from wpmcp.auth.owner import hash_password, totp_at
from wpmcp.auth.store import Store
from wpmcp.auth.tokens import pkce_s256

from ..conftest import PASSWORD, TOTP_SECRET, make_settings


@pytest.fixture
def server():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    store = Store(":memory:")
    store.set_owner(hash_password(PASSWORD, n=2**4, r=8, p=1), TOTP_SECRET)
    app = create_app(make_settings(public_url=base, insecure_dev=True, port=port), store=store)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", proxy_headers=False
    )
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert instance.started, "server did not start"
    yield base, store
    instance.should_exit = True
    thread.join(timeout=10)


def _connect(base: str, store: Store, access: str) -> dict:
    """What a connector does: discover, open the consent screen, exchange the code."""
    registered, client_secret = store.add_client("E2E client", ["https://client.example/callback"])
    verifier = secrets.token_urlsafe(48)
    with httpx.Client() as http:
        unauthorized = http.post(f"{base}/mcp", json={})
        assert unauthorized.status_code == 401
        metadata_url = unauthorized.headers["www-authenticate"].split('resource_metadata="')[1][:-1]
        resource = http.get(metadata_url).json()
        issuer = resource["authorization_servers"][0]
        server = http.get(f"{issuer}/.well-known/oauth-authorization-server").json()
        assert server["issuer"] == issuer

        page = http.get(
            server["authorization_endpoint"],
            params={
                "response_type": "code",
                "client_id": registered.client_id,
                "redirect_uri": "https://client.example/callback",
                "code_challenge": pkce_s256(verifier),
                "code_challenge_method": "S256",
                "state": "abc",
                "scope": " ".join(resource["scopes_supported"]),
                "resource": resource["resource"],
            },
        )
        assert page.status_code == 200, page.text
        request_token = page.text.split('name=request value="')[1].split('"')[0]
        decision = http.post(
            server["authorization_endpoint"],
            data={
                "request": request_token,
                "password": PASSWORD,
                "totp": totp_at(TOTP_SECRET, int(time.time()) // 30),
                "decision": "approve",
                "access": access,
            },
        )
        assert decision.status_code == 302, decision.text
        callback = parse_qs(urlsplit(decision.headers["location"]).query)
        assert callback["state"] == ["abc"] and callback["iss"] == [issuer]
        tokens = http.post(
            server["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": callback["code"][0],
                "redirect_uri": "https://client.example/callback",
                "code_verifier": verifier,
                "resource": resource["resource"],
            },
            auth=(registered.client_id, client_secret),
        )
        assert tokens.status_code == 200, tokens.text
        return {"resource": resource["resource"], **tokens.json()}


async def _session(resource: str, token: str):
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx.AsyncClient(headers=headers) as http,
        streamable_http_client(resource, http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("get_connection_info", {})
        return initialized, tools, result


def test_oauth_flow_then_official_mcp_client(server):
    base, store = server
    connection = _connect(base, store, access="read")
    assert connection["scope"] == "wp:read"

    initialized, tools, result = asyncio.run(
        _session(connection["resource"], connection["access_token"])
    )
    assert initialized.serverInfo.name == "wpmcp"
    assert [tool.name for tool in tools.tools] == ["get_connection_info"]
    assert tools.tools[0].annotations.readOnlyHint is True
    assert result.isError is False
    info = json.loads(result.content[0].text)
    assert info["client"] == "E2E client" and info["can_write"] is False

    # Kill switch: the same token stops working at once.
    assert store.revoke_all("e2e") == 1
    after = httpx.post(
        connection["resource"],
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"authorization": f"Bearer {connection['access_token']}"},
    )
    assert after.status_code == 401
