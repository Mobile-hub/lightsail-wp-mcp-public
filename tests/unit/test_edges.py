"""Branches that only a race or a call outside a request can reach."""

import asyncio
import secrets
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from wpmcp.mcp_server import build_mcp
from wpmcp.tools import principal_of

from ..conftest import access_token, authorize, exchange, full_flow, make_settings, refresh, rpc


def test_code_redeemed_by_a_concurrent_request(client, clock, store, reg, monkeypatch):
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier)["code"]
    monkeypatch.setattr(store, "redeem_code", lambda *_args, **_kwargs: None)
    response = exchange(client, reg, code, verifier)
    assert response.json()["error"] == "invalid_grant"
    assert store.list_grants() == []


def test_refresh_token_invalidated_between_check_and_rotation(
    client, clock, store, reg, monkeypatch
):
    tokens = full_flow(client, clock, reg)
    monkeypatch.setattr(store, "rotate_refresh", lambda *_a, **_k: ("invalid", None, None))
    assert refresh(client, reg, tokens["refresh_token"]).json()["error"] == "invalid_grant"


def test_mcp_server_fails_closed_outside_an_authenticated_request():
    mcp = build_mcp(make_settings())
    assert asyncio.run(mcp.list_tools()) == []
    with pytest.raises(ToolError, match="unauthenticated"):
        asyncio.run(mcp.call_tool("get_connection_info", {}))


def test_tool_without_a_principal_is_refused():
    for request in (None, SimpleNamespace(scope={})):
        ctx = SimpleNamespace(request_context=SimpleNamespace(request=request))
        with pytest.raises(PermissionError):
            principal_of(ctx)


def test_client_disabled_without_revoking_its_grant(client, app, clock, store):
    """Defence in depth: a disabled client is refused even if its grant row is still active."""
    token, grant = access_token(app, clock)
    with store._tx() as db:
        db.execute("UPDATE clients SET disabled_at = 1 WHERE client_id = ?", (grant.client_id,))
    response = rpc(client, token, "tools/list")
    assert response.status_code == 401 and "client disabled" in response.json()["error_description"]
