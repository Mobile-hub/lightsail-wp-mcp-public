from __future__ import annotations

import json

import httpx
import pytest
from starlette.testclient import TestClient

from tests.conftest import BASE, access_token, make_settings, rpc, rpc_result
from wpmcp.app import create_app
from wpmcp.wp.client import WordPressClient

READ_TOOLS = {
    "get_connection_info",
    "get_site_overview",
    "search_content",
    "get_content",
    "list_terms",
    "list_media",
    "list_revisions",
    "get_menus",
    "get_recent_changes",
    "inspect_page",
    "check_sitemap",
}


class FakeWordPress:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responses: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/wp-json/wpmcp/v1")
        if path == "/abilities" and path not in self.responses:
            # The site-ability list, read on tools/list: none unless a test sets some.
            return httpx.Response(200, json={"items": []})
        self.requests.append(request)
        if path in self.responses:
            return self.responses[path]
        return httpx.Response(200, json={"path": path, "params": dict(request.url.params)})


@pytest.fixture
def fake():
    return FakeWordPress()


@pytest.fixture
def wp_app(store, clock, fake):
    settings = make_settings(wp_url="https://books.example.test", wp_password="pw")
    wp = WordPressClient(settings, transport=httpx.MockTransport(fake))
    return create_app(settings, store=store, clock=clock, wp=wp)


@pytest.fixture
def wp_client(wp_app):
    with TestClient(wp_app, base_url=BASE) as test_client:
        yield test_client


def call(client, token, name, arguments=None):
    return rpc_result(
        rpc(client, token, "tools/call", {"name": name, "arguments": arguments or {}})
    )


def payload(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_read_tools_are_listed_for_a_read_only_connection(wp_app, wp_client, clock):
    token, _ = access_token(wp_app, clock, scope="wp:read")
    tools = rpc_result(rpc(wp_client, token, "tools/list"))["tools"]
    assert {t["name"] for t in tools} == READ_TOOLS
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["openWorldHint"] is False


def test_search_content_maps_arguments_to_bridge_parameters(wp_app, wp_client, clock, fake):
    token, _ = access_token(wp_app, clock, scope="wp:read")
    result = call(
        wp_client,
        token,
        "search_content",
        {"query": "principito", "lang": "es", "untranslated_to": "pt", "per_page": 5},
    )
    data = payload(result)
    assert data["path"] == "/content"
    assert data["params"] == {
        "search": "principito",
        "lang": "es",
        "type": "any",
        "status": "any",
        "untranslated_to": "pt",
        "orderby": "date",
        "page": "1",
        "per_page": "5",
    }


@pytest.mark.parametrize(
    ("name", "arguments", "path"),
    [
        ("get_site_overview", {}, "/overview"),
        ("get_content", {"content_id": 42}, "/content/42"),
        ("list_revisions", {"content_id": 42}, "/content/42/revisions"),
        ("list_terms", {"lang": "pt"}, "/terms"),
        ("list_media", {"attached_to": 7}, "/media"),
        ("get_menus", {}, "/menus"),
        ("get_recent_changes", {"since": "2026-11-01T00:00:00Z"}, "/changes"),
    ],
)
def test_each_read_tool_reaches_its_route(wp_app, wp_client, clock, name, arguments, path):
    token, _ = access_token(wp_app, clock, scope="wp:read")
    assert payload(call(wp_client, token, name, arguments))["path"] == path


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search_content", {"lang": "../../"}),
        ("search_content", {"per_page": 500}),
        ("get_content", {"content_id": 0}),
        ("get_content", {"content_id": "1/../../users"}),
        ("list_terms", {"taxonomy": "nav_menu"}),
    ],
)
def test_invalid_arguments_never_reach_wordpress(wp_app, wp_client, clock, fake, name, arguments):
    token, _ = access_token(wp_app, clock, scope="wp:read")
    result = call(wp_client, token, name, arguments)
    assert result["isError"] is True
    assert fake.requests == []


def item_with_body(body: str) -> httpx.Response:
    # The bridge's order before 0.6.0: the version last, after the body.
    return httpx.Response(
        200,
        json={"id": 9, "status": "draft", "title": "T", "content": body, "seo": None,
              "content_length": len(body), "version": "0123456789abcdef"},
    )  # fmt: skip


def raw_text(result) -> str:
    assert not result.get("isError"), result
    return result["content"][0]["text"]


def test_get_content_puts_the_version_first_and_the_body_last(wp_app, wp_client, clock, fake):
    fake.responses["/content/9"] = item_with_body("<p>short 🐻</p>")
    token, _ = access_token(wp_app, clock, scope="wp:read")
    result = call(wp_client, token, "get_content", {"content_id": 9})
    data = payload(result)
    assert list(data)[:2] == ["id", "version"] and list(data)[-1] == "body"
    # ChatGPT shows a result with a top-level "content" key as {}.
    assert "content" not in data
    assert data["body"] == "<p>short 🐻</p>"
    assert data["content_complete"] is True and data["next_offset"] is None
    assert data["content_length"] == len("<p>short 🐻</p>")
    assert "notes" not in data
    # The same object as structuredContent, also without a "content" key.
    assert result["structuredContent"] == data and "content" not in result["structuredContent"]
    # A client that keeps only the first lines of the answer still has the version.
    text = raw_text(result)
    assert text.index('"version"') < text.index('"body"')


def test_get_content_returns_a_long_body_in_parts(wp_app, wp_client, clock, fake):
    body = "".join(f"<!-- wp:paragraph -->\n<p>Párrafo {n} 🐻</p>\n<!-- /wp:paragraph -->\n\n"
                   for n in range(700))  # fmt: skip
    fake.responses["/content/9"] = item_with_body(body)
    token, _ = access_token(wp_app, clock, scope="wp:read")
    parts, offset = [], 0
    while True:
        data = payload(call(wp_client, token, "get_content", {"content_id": 9, "offset": offset}))
        assert data["content_offset"] == offset and data["content_length"] == len(body)
        assert len(data["body"]) <= 8_000
        assert data["version"] == "0123456789abcdef"
        parts.append(data["body"])
        if data["content_complete"]:
            assert data["next_offset"] is None
            break
        assert f"offset={data['next_offset']}" in data["notes"][-1]
        offset = data["next_offset"]
    assert len(parts) > 3 and "".join(parts) == body

    small = payload(call(wp_client, token, "get_content", {"content_id": 9, "max_chars": 1000}))
    assert small["next_offset"] == 1000 and small["body"] == body[:1000]


@pytest.mark.parametrize(
    "arguments",
    [{"offset": 10_000}, {"max_chars": 999}, {"max_chars": 60_001}, {"offset": -1}],
)
def test_get_content_refuses_bad_parts(wp_app, wp_client, clock, fake, arguments):
    fake.responses["/content/9"] = item_with_body("<p>x</p>")
    token, _ = access_token(wp_app, clock, scope="wp:read")
    result = call(wp_client, token, "get_content", {"content_id": 9, **arguments})
    assert result["isError"] is True


def test_get_content_passes_other_answers_through(wp_app, wp_client, clock, fake):
    token, _ = access_token(wp_app, clock, scope="wp:read")
    assert payload(call(wp_client, token, "get_content", {"content_id": 42}))["path"] == (
        "/content/42"
    )


def test_wordpress_errors_become_tool_errors(wp_app, wp_client, clock, fake):
    fake.responses["/content/9"] = httpx.Response(
        404, json={"code": "wpmcp_not_found", "message": "No post or page with that ID."}
    )
    token, _ = access_token(wp_app, clock, scope="wp:read")
    result = call(wp_client, token, "get_content", {"content_id": 9})
    assert result["isError"] is True
    assert "No post or page with that ID." in result["content"][0]["text"]


def test_without_wordpress_only_the_diagnostic_tool_exists(app, client, clock):
    token, _ = access_token(app, clock, scope="wp:read")
    tools = rpc_result(rpc(client, token, "tools/list"))["tools"]
    assert [t["name"] for t in tools] == ["get_connection_info"]


def test_app_builds_its_own_client_from_settings(store, clock):
    settings = make_settings(wp_url="https://books.example.test", wp_password="pw")
    app = create_app(settings, store=store, clock=clock)
    with TestClient(app, base_url=BASE) as test_client:
        token, _ = access_token(app, clock, scope="wp:read")
        tools = rpc_result(rpc(test_client, token, "tools/list"))["tools"]
    assert "get_site_overview" in {t["name"] for t in tools}
