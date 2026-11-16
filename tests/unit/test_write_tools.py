from __future__ import annotations

import io
import json
import logging

import httpx
import pytest
from PIL import Image
from starlette.testclient import TestClient

from tests.conftest import BASE, access_token, make_settings, rpc, rpc_result
from wpmcp.app import create_app
from wpmcp.media.worker import MediaProcessor
from wpmcp.wp.client import WordPressClient

WRITE_TOOLS = {
    "create_draft",
    "update_draft",
    "create_category",
    "import_image",
    "request_image_upload",
    "update_media",
}
FILE_URL = "https://files.oaiusercontent.com/file-abc"


def png(width=10, height=10) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "teal").save(out, "PNG")
    return out.getvalue()


class FakeWordPress:
    """The bridge: records requests and answers each POST with an item carrying an id."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responses: dict[str, httpx.Response] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/wp-json/wpmcp/v1")
        if path in self.responses:
            return self.responses[path]
        return httpx.Response(200, json={"id": 501, "path": path})

    def json_body(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].content)


def downloads(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=png(40, 30))


async def public(_host):
    return ["20.60.1.1"]


@pytest.fixture
def fake():
    return FakeWordPress()


def build(store, clock, fake, **overrides):
    settings = make_settings(wp_url="https://books.example.test", wp_password="pw", **overrides)
    wp = WordPressClient(settings, transport=httpx.MockTransport(fake))
    media = MediaProcessor(settings, transport=httpx.MockTransport(downloads), resolver=public)
    return create_app(settings, store=store, clock=clock, wp=wp, media=media)


@pytest.fixture
def wp_app(store, clock, fake):
    return build(store, clock, fake)


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


def test_write_tools_are_hidden_from_read_only_connections(wp_app, wp_client, clock):
    token, _ = access_token(wp_app, clock, scope="wp:read")
    names = {t["name"] for t in rpc_result(rpc(wp_client, token, "tools/list"))["tools"]}
    assert names.isdisjoint(WRITE_TOOLS)


def test_read_only_connection_cannot_call_a_write_tool(wp_app, wp_client, clock, fake):
    token, _ = access_token(wp_app, clock, scope="wp:read")
    result = call(wp_client, token, "create_draft", {"type": "post", "lang": "pt", "title": "x"})
    assert result["isError"] is True and "read-only" in result["content"][0]["text"]
    assert fake.requests == []


def test_write_tools_and_their_annotations(wp_app, wp_client, clock):
    token, _ = access_token(wp_app, clock)
    tools = {t["name"]: t for t in rpc_result(rpc(wp_client, token, "tools/list"))["tools"]}
    assert set(tools) >= WRITE_TOOLS
    for name in WRITE_TOOLS:
        hints = tools[name]["annotations"]
        assert hints["readOnlyHint"] is False
        assert tools[name]["securitySchemes"] == [{"type": "oauth2", "scopes": ["wp:write"]}]
    # Drafts are invisible to visitors; image details show on published posts.
    assert tools["create_draft"]["annotations"]["destructiveHint"] is False
    assert tools["update_draft"]["annotations"]["destructiveHint"] is False
    assert tools["update_media"]["annotations"]["destructiveHint"] is True
    assert tools["import_image"]["annotations"]["openWorldHint"] is True
    assert tools["import_image"]["_meta"]["openai/fileParams"] == ["image"]


def test_create_draft_maps_arguments(wp_app, wp_client, clock, fake):
    token, _ = access_token(wp_app, clock)
    result = call(
        wp_client,
        token,
        "create_draft",
        {
            "type": "post",
            "lang": "pt",
            "title": "O Principezinho",
            "content": "<!-- wp:paragraph --><p>Olá</p><!-- /wp:paragraph -->",
            "translation_of": 4,
            "category_ids": [17],
            "seo": {"title": "SEO PT", "description": None},
        },
    )
    assert payload(result)["id"] == 501
    request = fake.requests[-1]
    assert request.method == "POST" and request.url.path.endswith("/content")
    assert fake.json_body() == {
        "type": "post",
        "lang": "pt",
        "title": "O Principezinho",
        "content": "<!-- wp:paragraph --><p>Olá</p><!-- /wp:paragraph -->",
        "excerpt": "",
        "translation_of": 4,
        "categories": [17],
        # Unset SEO fields are not sent (left unchanged); an explicit null clears one.
        "seo": {"title": "SEO PT", "description": None},
    }


def test_update_draft_sends_only_what_changes(wp_app, wp_client, clock, fake):
    token, _ = access_token(wp_app, clock)
    call(
        wp_client,
        token,
        "update_draft",
        {"content_id": 8, "expected_version": "0123456789abcdef", "featured_image_id": 0},
    )
    assert fake.requests[-1].url.path.endswith("/content/8")
    assert fake.json_body() == {
        "expected_version": "0123456789abcdef",
        "featured_media": 0,
    }


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("update_draft", {"content_id": 8, "expected_version": "0123456789abcdef"}),
        ("update_media", {"media_id": 3}),
        ("create_draft", {"type": "post", "lang": "pt", "title": ""}),
        ("create_draft", {"type": "attachment", "lang": "pt", "title": "x"}),
        ("create_draft", {"type": "post", "lang": "../x", "title": "x"}),
        ("create_draft", {"type": "post", "lang": "pt", "title": "x", "status": "publish"}),
        ("create_draft", {"type": "post", "lang": "pt", "title": "x", "seo": {"robots": "x"}}),
        ("create_category", {"name": "x", "lang": "pt", "parent_id": 0}),
        ("import_image", {"image": {"file_id": "f"}}),
    ],
)
def test_invalid_writes_never_reach_wordpress(wp_app, wp_client, clock, fake, name, arguments):
    token, _ = access_token(wp_app, clock)
    result = call(wp_client, token, name, arguments)
    assert result["isError"] is True
    assert fake.requests == []


def test_bridge_refusals_become_tool_errors(wp_app, wp_client, clock, fake):
    fake.responses["/content/7"] = httpx.Response(
        409, json={"code": "wpmcp_not_draft", "message": "Item 7 is publish: only drafts..."}
    )
    token, _ = access_token(wp_app, clock)
    result = call(
        wp_client,
        token,
        "update_draft",
        {"content_id": 7, "expected_version": "0123456789abcdef", "title": "x"},
    )
    assert result["isError"] is True and "Item 7 is publish" in result["content"][0]["text"]


def test_writes_are_audited_without_their_contents(wp_app, wp_client, clock, caplog):
    token, grant = access_token(wp_app, clock)
    with caplog.at_level(logging.INFO, logger="wpmcp.audit"):
        call(wp_client, token, "create_category", {"name": "Clássicos secretos", "lang": "pt"})
    records = [json.loads(r.message) for r in caplog.records]
    writes = [r for r in records if r["event"] == "wp_write"]
    assert writes == [
        {
            "event": "wp_write",
            "tool": "create_category",
            "object": 501,
            "client": grant.client_id,
            "grant": grant.grant_id,
        }
    ]
    assert "secretos" not in caplog.text


def test_write_budget_per_connection(store, clock, fake):
    app = build(store, clock, fake, write_budget=2)
    with TestClient(app, base_url=BASE) as client:
        token, _ = access_token(app, clock)
        other, _ = access_token(app, clock)
        args = {"name": "x", "lang": "pt"}
        assert not call(client, token, "create_category", args).get("isError")
        assert not call(client, token, "create_category", args).get("isError")
        refused = call(client, token, "create_category", args)
        assert refused["isError"] is True and "limit of changes" in refused["content"][0]["text"]
        # Reads go on, and another connection has its own budget.
        assert not call(client, token, "get_site_overview").get("isError")
        assert not call(client, other, "create_category", args).get("isError")
    assert len([r for r in fake.requests if r.method == "POST"]) == 3


def test_import_image_downloads_sanitizes_and_uploads(wp_app, wp_client, clock, fake):
    token, _ = access_token(wp_app, clock)
    result = call(
        wp_client,
        token,
        "import_image",
        {
            "image": {"download_url": FILE_URL, "file_id": "file-abc", "file_name": "IMG_0001.PNG"},
            "title": "Capa: O Principezinho",
            "alt_text": "Capa do livro",
            "attach_to": 8,
        },
    )
    assert payload(result)["id"] == 501
    upload = fake.requests[-1]
    assert upload.url.path.endswith("/media")
    body = upload.content
    assert b'filename="capa-o-principezinho.png"' in body
    assert b"Content-Type: image/png" in body
    for field in (b'name="title"', b'name="alt"', b'name="attach_to"'):
        assert field in body
    assert b'name="caption"' not in body


def test_import_image_refuses_other_hosts(wp_app, wp_client, clock, fake):
    token, _ = access_token(wp_app, clock)
    result = call(
        wp_client,
        token,
        "import_image",
        {"image": {"download_url": "https://example.org/cover.png", "file_id": "f"}},
    )
    assert result["isError"] is True and "not allowed" in result["content"][0]["text"]
    assert fake.requests == []


def test_request_image_upload_returns_a_single_use_link(wp_app, wp_client, clock, store):
    token, grant = access_token(wp_app, clock)
    link = payload(
        call(wp_client, token, "request_image_upload", {"title": "Capa", "attach_to": 8})
    )
    assert link["upload_url"].startswith(f"{BASE}/upload/")
    assert link["single_use"] is True and link["expires_in_seconds"] == 900
    secret = link["upload_url"].rsplit("/", 1)[1]
    stored = store.get_upload_link(secret, now=clock())
    assert stored.grant_id == grant.grant_id
    assert stored.params == {"title": "Capa", "attach_to": 8}
