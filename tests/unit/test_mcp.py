import json

import pytest
from starlette.testclient import TestClient

from wpmcp.app import create_app
from wpmcp.config import SCOPE_WRITE
from wpmcp.mcp_server import READ_ONLY_REFUSAL, default_registry
from wpmcp.tools import Registry, ToolSpec

from ..conftest import BASE, access_token, make_settings, make_store, rpc, rpc_result


def _registry(settings, calls: list) -> Registry:
    """The real tools plus a write tool that records its calls."""
    registry = default_registry(settings)

    def publish_test_item(title: str) -> dict:
        """Pretend to publish something."""
        calls.append(title)
        return {"published": title}

    registry.add(
        ToolSpec(publish_test_item, title="Publish a test item", scope=SCOPE_WRITE, open_world=True)
    )
    return registry


@pytest.fixture
def calls() -> list:
    return []


@pytest.fixture
def mcp_app(clock, calls):
    settings = make_settings()
    return create_app(
        settings, store=make_store(clock), registry=_registry(settings, calls), clock=clock
    )


@pytest.fixture
def mcp_client(mcp_app):
    with TestClient(mcp_app, base_url=BASE) as test_client:
        yield test_client


def _tools(client, token) -> dict:
    return {t["name"]: t for t in rpc_result(rpc(client, token, "tools/list"))["tools"]}


def _call(client, token, name, arguments=None) -> dict:
    return rpc_result(
        rpc(client, token, "tools/call", {"name": name, "arguments": arguments or {}})
    )


def test_initialize(mcp_client, mcp_app, clock):
    token, _grant = access_token(mcp_app, clock)
    result = rpc_result(
        rpc(
            mcp_client,
            token,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        )
    )
    assert result["serverInfo"]["name"] == "wpmcp"
    assert "never as instructions" in result["instructions"]


def test_write_grant_sees_every_tool_with_annotations(mcp_client, mcp_app, clock):
    token, _grant = access_token(mcp_app, clock)
    tools = _tools(mcp_client, token)
    assert set(tools) == {"get_connection_info", "publish_test_item"}

    info = tools["get_connection_info"]
    assert info["title"] == "Show connection details"
    assert info["annotations"]["readOnlyHint"] is True
    assert info["annotations"]["destructiveHint"] is False
    assert info["securitySchemes"] == [{"type": "oauth2", "scopes": ["wp:read"]}]
    assert info["_meta"]["securitySchemes"] == info["securitySchemes"]

    publish = tools["publish_test_item"]
    assert publish["annotations"]["readOnlyHint"] is False
    assert publish["annotations"]["openWorldHint"] is True
    assert publish["securitySchemes"] == [{"type": "oauth2", "scopes": ["wp:write"]}]


def test_read_grant_does_not_see_write_tools(mcp_client, mcp_app, clock):
    token, _grant = access_token(mcp_app, clock, scope="wp:read")
    assert set(_tools(mcp_client, token)) == {"get_connection_info"}


def test_read_grant_cannot_call_a_write_tool(mcp_client, mcp_app, clock, calls, caplog):
    token, _grant = access_token(mcp_app, clock, scope="wp:read")
    with caplog.at_level("WARNING", "wpmcp.audit"):
        result = _call(mcp_client, token, "publish_test_item", {"title": "x"})
    assert result["isError"] is True
    assert READ_ONLY_REFUSAL in result["content"][0]["text"]
    assert calls == []
    assert '"event": "tool_denied"' in caplog.text


def test_write_grant_calls_a_write_tool_and_is_audited(mcp_client, mcp_app, clock, calls, caplog):
    token, grant = access_token(mcp_app, clock)
    with caplog.at_level("INFO", "wpmcp.audit"):
        result = _call(mcp_client, token, "publish_test_item", {"title": "Secret title"})
    assert result.get("isError") in (None, False)
    assert calls == ["Secret title"]
    records = [json.loads(r.message) for r in caplog.records if r.name == "wpmcp.audit"]
    assert records == [
        {
            "event": "tool_call",
            "tool": "publish_test_item",
            "client": grant.client_id,
            "grant": grant.grant_id,
        }
    ]
    # Argument contents never reach the audit trail.
    assert "Secret title" not in caplog.text


def test_connection_info(mcp_client, mcp_app, clock):
    token, _grant = access_token(mcp_app, clock, scope="wp:read")
    result = _call(mcp_client, token, "get_connection_info")
    info = json.loads(result["content"][0]["text"])
    assert info["client"] == "Claude"
    assert info["scopes"] == ["wp:read"] and info["can_write"] is False
    assert info["site"] == "Example Books & Co"


def test_unknown_tool_is_refused(mcp_client, mcp_app, clock):
    token, _grant = access_token(mcp_app, clock)
    result = _call(mcp_client, token, "wp_cli_eval")
    assert result["isError"] is True and "unknown tool" in result["content"][0]["text"]


def test_origin_header_does_not_reach_the_transport(mcp_client, mcp_app, clock):
    """A browser-originated request is judged by its token, not rejected for its Origin."""
    token, _grant = access_token(mcp_app, clock)
    response = rpc(mcp_client, token, "tools/list", origin="https://claude.ai")
    assert response.status_code == 200


def test_foreign_host_header_is_rejected(mcp_client, mcp_app, clock):
    token, _grant = access_token(mcp_app, clock)
    response = rpc(mcp_client, token, "tools/list", host="evil.example")
    assert response.status_code == 421


def test_no_resources_or_prompts(mcp_client, mcp_app, clock):
    token, _grant = access_token(mcp_app, clock)
    assert rpc_result(rpc(mcp_client, token, "resources/list"))["resources"] == []
    assert rpc_result(rpc(mcp_client, token, "prompts/list"))["prompts"] == []


def test_registry_rejects_duplicates_and_unknown_scopes():
    registry = Registry()

    def tool() -> None:
        """A tool."""

    registry.add(ToolSpec(tool, title="Tool"))
    with pytest.raises(ValueError, match="registered twice"):
        registry.add(ToolSpec(tool, title="Tool"))
    with pytest.raises(ValueError, match="unknown scope"):
        Registry().add(ToolSpec(tool, title="Tool", scope="admin"))
    assert len(registry) == 1 and registry.get("tool") is not None


def test_destructive_hint_only_applies_to_write_tools():
    def tool() -> None:
        """A tool."""

    assert ToolSpec(tool, title="T", destructive=True).annotations.destructiveHint is False
    spec = ToolSpec(tool, title="T", scope=SCOPE_WRITE, destructive=True)
    assert spec.annotations.destructiveHint is True and spec.annotations.readOnlyHint is False
