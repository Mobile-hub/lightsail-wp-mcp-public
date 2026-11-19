"""Site abilities as tools, against a fake bridge. The bridge's own rules (the owner's list,
the integration user's capabilities) are tested in tests/integration."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from starlette.testclient import TestClient

from tests.conftest import BASE, access_token, rpc, rpc_result
from tests.unit.test_write_tools import build, call, payload
from wpmcp.tools.abilities import without_content_keys

LISTED = [
    {
        "name": "core/get-user-info",
        "label": "Get user info",
        "description": "The current user.",
        "input_schema": None,
        "annotations": {"readonly": True, "destructive": False, "idempotent": True},
    },
    {
        "name": "example/set-banner",
        "label": "Set the banner",
        "description": "Changes the site banner.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "maxLength": 80}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "annotations": {"readonly": False, "idempotent": True},
    },
    # Skipped: a scalar input, a malformed name, a name a fixed tool already has.
    {"name": "example/count", "input_schema": {"type": "integer"}, "annotations": {}},
    {"name": "Bad Name", "input_schema": None},
    {"name": "get/content", "input_schema": None, "annotations": {"readonly": True}},
]


class FakeBridge:
    def __init__(self, items=None) -> None:
        self.items = LISTED if items is None else items
        self.requests: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/wp-json/wpmcp/v1")
        self.requests.append(request)
        if self.down:
            return httpx.Response(502, text="bad gateway")
        if path == "/abilities":
            return httpx.Response(200, json={"items": self.items})
        if path == "/abilities/run":
            body = json.loads(request.content)
            if body["name"] == "example/set-banner" and body["input"]["text"] == "refuse":
                return httpx.Response(
                    403, json={"code": "wpmcp_ability_failed", "message": "Not allowed here."}
                )
            result = {"id": 2, "content": "<p>x</p>", "nested": [{"content": 1, "body": 2}]}
            return httpx.Response(200, json={"ability": body["name"], "result": result})
        return httpx.Response(200, json={"id": 501, "path": path})

    def runs(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path.endswith("/run")]

    def lists(self) -> int:
        return sum(r.url.path.endswith("/abilities") for r in self.requests)


@pytest.fixture
def bridge():
    return FakeBridge()


@pytest.fixture
def site_client(store, clock, bridge):
    app = build(store, clock, bridge)
    with TestClient(app, base_url=BASE) as client:
        yield app, client


def tools(client, token) -> dict:
    return {t["name"]: t for t in rpc_result(rpc(client, token, "tools/list"))["tools"]}


def test_listed_abilities_become_typed_tools(site_client, clock):
    app, client = site_client
    read_only, _ = access_token(app, clock, scope="wp:read")
    listed = tools(client, read_only)
    assert "core_get_user_info" in listed and "example_set_banner" not in listed

    token, _ = access_token(app, clock)
    listed = tools(client, token)
    user, banner = listed["core_get_user_info"], listed["example_set_banner"]
    assert user["annotations"]["readOnlyHint"] is True
    assert user["securitySchemes"] == [{"type": "oauth2", "scopes": ["wp:read"]}]
    assert user["inputSchema"] == {"type": "object", "properties": {}}
    assert banner["annotations"]["destructiveHint"] is True
    assert banner["annotations"]["idempotentHint"] is True
    assert banner["securitySchemes"] == [{"type": "oauth2", "scopes": ["wp:write"]}]
    assert banner["inputSchema"]["required"] == ["text"]
    assert banner["outputSchema"]["type"] == "object"
    assert "may not be undoable" in banner["description"]
    assert {"example_count", "get_content"} & set(listed) == {"get_content"}
    # get/content did not replace the fixed get_content tool.
    assert listed["get_content"]["description"].startswith("Full post or page")


def test_running_an_ability_sends_its_name_and_input(site_client, clock, bridge):
    app, client = site_client
    token, _ = access_token(app, clock)
    result = call(client, token, "core_get_user_info")
    data = payload(result)
    assert data["ability"] == "core/get-user-info"
    assert data["result"]["body"] == "<p>x</p>" and "content" not in data["result"]
    assert data["result"]["nested"] == [{"content_value": 1, "body": 2}]
    assert result["structuredContent"] == data
    call(client, token, "example_set_banner", {"text": "Hello"})
    assert bridge.runs() == [
        {"name": "core/get-user-info"},
        {"name": "example/set-banner", "input": {"text": "Hello"}},
    ]


def test_ability_calls_are_checked_like_any_tool(store, clock, bridge, caplog):
    app = build(store, clock, bridge, live_budget=1)
    with TestClient(app, base_url=BASE) as client:
        read_only, _ = access_token(app, clock, scope="wp:read")
        refused = call(client, read_only, "example_set_banner", {"text": "x"})
        assert refused["isError"] is True and "read-only" in str(refused)

        token, grant = access_token(app, clock)
        unknown = call(client, token, "example_set_banner", {"text": "x", "html": "<b>"})
        assert unknown["isError"] is True and "'html'" in str(unknown)
        assert bridge.runs() == []

        with caplog.at_level(logging.INFO, logger="wpmcp.audit"):
            assert not call(client, token, "example_set_banner", {"text": "x"}).get("isError")
        writes = [json.loads(r.message) for r in caplog.records if r.name == "wpmcp.audit"]
        assert {"event": "wp_write", "tool": "example_set_banner",
                "client": grant.client_id, "grant": grant.grant_id} in writes  # fmt: skip
        assert "Hello" not in caplog.text
        budget = call(client, token, "example_set_banner", {"text": "y"})
        assert budget["isError"] is True and "live changes" in str(budget)
        # Read-only abilities do not spend the live budget.
        assert not call(client, token, "core_get_user_info").get("isError")


def test_bridge_refusals_become_tool_errors(site_client, clock):
    app, client = site_client
    token, _ = access_token(app, clock)
    result = call(client, token, "example_set_banner", {"text": "refuse"})
    assert result["isError"] is True and "Not allowed here." in str(result)


def test_the_list_is_cached_and_retried(store, clock, bridge):
    bridge.down = True
    app = build(store, clock, bridge)
    with TestClient(app, base_url=BASE) as client:
        token, _ = access_token(app, clock)
        assert "core_get_user_info" not in tools(client, token)
        assert "get_content" in tools(client, token)  # the fixed tools stay
        assert bridge.lists() == 1  # failure kept for a minute
        bridge.down = False
        clock.advance(61)
        assert "core_get_user_info" in tools(client, token)
        tools(client, token)
        assert bridge.lists() == 2  # kept for ten minutes
        bridge.items = []
        clock.advance(601)
        assert "core_get_user_info" not in tools(client, token)
        refused = call(client, token, "core_get_user_info")
        assert refused["isError"] is True and "unknown tool" in str(refused)


def test_without_content_keys():
    assert without_content_keys({"content": {"content": [{"content": 1}]}}) == {
        "body": {"body": [{"body": 1}]}
    }
    assert without_content_keys([1, "content"]) == [1, "content"]
