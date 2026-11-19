"""Live tools (phase 5 and menus): argument mapping, protections and the live budget,
against a fake bridge. The bridge's own rules are tested in tests/integration."""

from __future__ import annotations

import json
import logging

import pytest
from starlette.testclient import TestClient

from tests.conftest import BASE, access_token, rpc, rpc_result
from tests.unit.test_write_tools import FakeWordPress, build, call, payload

LIVE_TOOLS = {
    "publish_content",
    "schedule_content",
    "unpublish_content",
    "update_published_content",
    "insert_image",
    "restore_revision",
    "trash_content",
    "link_translations",
    "apply_menu",
    "restore_menu",
}
V = "0123456789abcdef"


@pytest.fixture
def fake():
    return FakeWordPress()


@pytest.fixture
def live_app(store, clock, fake):
    return build(store, clock, fake, protected_ids=frozenset({42}))


@pytest.fixture
def live_client(live_app):
    with TestClient(live_app, base_url=BASE) as test_client:
        yield test_client


def test_live_tools_need_write_and_say_what_they_do(live_app, live_client, clock):
    read_only, _ = access_token(live_app, clock, scope="wp:read")
    names = {t["name"] for t in rpc_result(rpc(live_client, read_only, "tools/list"))["tools"]}
    assert names.isdisjoint(LIVE_TOOLS | {"restore_from_trash"})

    token, _ = access_token(live_app, clock)
    tools = {t["name"]: t for t in rpc_result(rpc(live_client, token, "tools/list"))["tools"]}
    assert set(tools) >= LIVE_TOOLS | {"restore_from_trash"}
    for name in LIVE_TOOLS:
        assert tools[name]["securitySchemes"] == [{"type": "oauth2", "scopes": ["wp:write"]}]
        assert tools[name]["annotations"]["readOnlyHint"] is False
    # Publishing adds; the others take away or replace what visitors see.
    assert tools["publish_content"]["annotations"]["destructiveHint"] is False
    assert tools["schedule_content"]["annotations"]["destructiveHint"] is False
    assert tools["restore_from_trash"]["annotations"]["destructiveHint"] is False
    for name in LIVE_TOOLS - {"publish_content", "schedule_content"}:
        assert tools[name]["annotations"]["destructiveHint"] is True, name


def test_read_only_connection_cannot_publish(live_app, live_client, clock, fake):
    token, _ = access_token(live_app, clock, scope="wp:read")
    result = call(live_client, token, "publish_content", {"content_id": 8, "expected_version": V})
    assert result["isError"] is True and "read-only" in result["content"][0]["text"]
    assert fake.requests == []


@pytest.mark.parametrize(
    ("name", "arguments", "path", "body"),
    [
        ("publish_content", {"content_id": 8, "expected_version": V}, "/content/8/publish",
         {"expected_version": V}),
        ("unpublish_content", {"content_id": 8, "expected_version": V}, "/content/8/unpublish",
         {"expected_version": V}),
        ("trash_content", {"content_id": 8, "expected_version": V}, "/content/8/trash",
         {"expected_version": V}),
        ("restore_from_trash", {"content_id": 8}, "/content/8/untrash", {}),
        ("restore_revision", {"content_id": 8, "expected_version": V, "revision_id": 90},
         "/content/8/restore-revision", {"expected_version": V, "revision_id": 90}),
        ("update_published_content",
         {"content_id": 8, "expected_version": V, "title": "Novo", "seo": {"title": None}},
         "/content/8/live", {"expected_version": V, "title": "Novo", "seo": {"title": None}}),
        ("update_published_content",
         {"content_id": 8, "expected_version": V, "content": "<p>x</p>", "allow_shrink": True},
         "/content/8/live", {"expected_version": V, "content": "<p>x</p>", "allow_shrink": True}),
        ("update_published_content",
         {"content_id": 8, "expected_version": V, "title": "T", "allow_shrink": True},
         "/content/8/live", {"expected_version": V, "title": "T"}),
        ("insert_image", {"content_id": 8, "expected_version": V, "media_id": 77},
         "/content/8/insert-image",
         {"expected_version": V, "media_id": 77, "position": "end", "size": "large"}),
        ("insert_image",
         {"content_id": 8, "expected_version": V, "media_id": 77, "position": "after_text",
          "after_text": "Quantum chips", "alt_text": "Um chip", "caption": "Foto", "size": "full"},
         "/content/8/insert-image",
         {"expected_version": V, "media_id": 77, "position": "after_text",
          "anchor_text": "Quantum chips", "alt": "Um chip", "caption": "Foto", "size": "full"}),
        ("link_translations", {"translations": {"en": 3, "es": 4}}, "/translations",
         {"items": {"en": 3, "es": 4}}),
        ("restore_menu", {"menu_id": 5}, "/menus/5/restore", {}),
    ],
)  # fmt: skip
def test_live_tools_map_to_their_bridge_routes(
    live_app, live_client, clock, fake, name, arguments, path, body
):
    token, _ = access_token(live_app, clock)
    assert payload(call(live_client, token, name, arguments))["id"] == 501
    assert fake.requests[-1].method == "POST"
    assert fake.requests[-1].url.path.endswith(path)
    assert fake.json_body() == body


def test_schedule_content_sends_utc(live_app, live_client, clock, fake):
    token, _ = access_token(live_app, clock)
    # The test clock starts at 1_800_000_000 (2027-01-15T08:00:00Z).
    arguments = {"content_id": 8, "expected_version": V, "publish_at": "2027-01-20T10:30:00+01:00"}
    payload(call(live_client, token, "schedule_content", arguments))
    assert fake.requests[-1].url.path.endswith("/content/8/schedule")
    assert fake.json_body() == {"expected_version": V, "publish_at": "2027-01-20T09:30:00Z"}


@pytest.mark.parametrize(
    "publish_at",
    ["2027-01-20T10:30:00", "tomorrow", "2027-13-01T00:00:00Z"],
)
def test_schedule_content_refuses_bad_dates(live_app, live_client, clock, fake, publish_at):
    token, _ = access_token(live_app, clock)
    arguments = {"content_id": 8, "expected_version": V, "publish_at": publish_at}
    result = call(live_client, token, "schedule_content", arguments)
    assert result["isError"] is True and "publish_at" in result["content"][0]["text"]
    assert fake.requests == []


def test_apply_menu_sends_typed_items(live_app, live_client, clock, fake):
    token, _ = access_token(live_app, clock)
    items = [
        {"kind": "page", "id": 2},
        {"kind": "category", "id": 7, "title": "Aventuras", "parent": 0},
        {"kind": "link", "url": "https://example.org/x", "title": "X"},
        {"kind": "keep", "id": 99},
    ]
    payload(call(live_client, token, "apply_menu", {"menu_id": 5, "expected_version": V,
                                                    "items": items}))  # fmt: skip
    assert fake.requests[-1].url.path.endswith("/menus/5")
    assert fake.json_body() == {"expected_version": V, "items": items}


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("update_published_content", {"content_id": 8, "expected_version": V}),
        ("publish_content", {"content_id": 8, "expected_version": "stale"}),
        ("publish_content", {"content_id": 8}),
        ("link_translations", {"translations": {"en": 3}}),
        ("link_translations", {"translations": {"../x": 3, "es": 4}}),
        ("apply_menu", {"menu_id": 5, "expected_version": V, "items": [{"kind": "widget"}]}),
        ("apply_menu", {"menu_id": 5, "expected_version": V,
                        "items": [{"kind": "page", "id": 2, "classes": "x"}]}),
        ("trash_content", {"content_id": 8, "expected_version": V, "force": True}),
        ("update_published_content",
         {"content_id": 8, "expected_version": V, "allow_shrink": True}),
        ("insert_image", {"content_id": 8, "expected_version": V}),
        ("insert_image", {"content_id": 8, "expected_version": V, "media_id": 7,
                          "position": "after_text"}),
        ("insert_image", {"content_id": 8, "expected_version": V, "media_id": 7,
                          "after_text": "only with after_text"}),
        ("insert_image", {"content_id": 8, "expected_version": V, "media_id": 7,
                          "position": "middle"}),
        ("insert_image", {"content_id": 8, "expected_version": V, "media_id": 7,
                          "size": "thumbnail"}),
        ("insert_image", {"content_id": 8, "expected_version": V, "media_id": 7,
                          "html": "<img src=x onerror=alert(1)>"}),
    ],
)  # fmt: skip
def test_invalid_live_calls_never_reach_wordpress(
    live_app, live_client, clock, fake, name, arguments
):
    token, _ = access_token(live_app, clock)
    result = call(live_client, token, name, arguments)
    assert result["isError"] is True
    assert fake.requests == []


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("unpublish_content", {"content_id": 42, "expected_version": V}),
        ("trash_content", {"content_id": 42, "expected_version": V}),
        ("update_published_content", {"content_id": 42, "expected_version": V, "title": "x"}),
        ("restore_revision", {"content_id": 42, "expected_version": V, "revision_id": 1}),
        ("link_translations", {"translations": {"en": 42, "es": 4}}),
        ("insert_image", {"content_id": 42, "expected_version": V, "media_id": 7}),
    ],
)
def test_protected_items_are_refused_before_wordpress(
    live_app, live_client, clock, fake, name, arguments
):
    token, _ = access_token(live_app, clock)
    result = call(live_client, token, name, arguments)
    assert result["isError"] is True and "protected" in result["content"][0]["text"]
    assert fake.requests == []


def test_live_budget_is_separate_and_lower(store, clock, fake):
    app = build(store, clock, fake, write_budget=10, live_budget=2)
    with TestClient(app, base_url=BASE) as client:
        token, _ = access_token(app, clock)
        live = {"content_id": 8, "expected_version": V}
        assert not call(client, token, "publish_content", live).get("isError")
        assert not call(client, token, "trash_content", live).get("isError")
        refused = call(client, token, "unpublish_content", live)
        assert refused["isError"] is True and "live changes" in refused["content"][0]["text"]
        # Drafts and untrashing are not live changes: they go on.
        assert not call(client, token, "create_category", {"name": "x", "lang": "pt"}).get(
            "isError"
        )
        assert not call(client, token, "restore_from_trash", {"content_id": 8}).get("isError")


def test_insert_image_counts_as_a_live_change(store, clock, fake):
    # The bridge decides whether the item is published; the budget is charged either way.
    app = build(store, clock, fake, live_budget=1)
    with TestClient(app, base_url=BASE) as client:
        token, _ = access_token(app, clock)
        image = {"content_id": 8, "expected_version": V, "media_id": 7}
        assert not call(client, token, "insert_image", image).get("isError")
        refused = call(client, token, "insert_image", image)
        assert refused["isError"] is True and "live changes" in refused["content"][0]["text"]
    assert len(fake.requests) == 1


def test_live_budget_zero_turns_live_changes_off(store, clock, fake):
    app = build(store, clock, fake, live_budget=0)
    with TestClient(app, base_url=BASE) as client:
        token, _ = access_token(app, clock)
        result = call(client, token, "publish_content", {"content_id": 8, "expected_version": V})
        assert result["isError"] is True
    assert fake.requests == []


def test_live_changes_are_audited_without_contents(live_app, live_client, clock, caplog):
    token, grant = access_token(live_app, clock)
    arguments = {"content_id": 8, "expected_version": V, "title": "Título secreto"}
    with caplog.at_level(logging.INFO, logger="wpmcp.audit"):
        call(live_client, token, "update_published_content", arguments)
    writes = [json.loads(r.message) for r in caplog.records]
    writes = [r for r in writes if r["event"] == "wp_write"]
    assert writes == [
        {
            "event": "wp_write",
            "tool": "update_published_content",
            "object": 501,
            "client": grant.client_id,
            "grant": grant.grant_id,
        }
    ]
    assert "secreto" not in caplog.text


def test_settings_read_protected_ids_and_live_budget(tmp_path):
    from tests.conftest import make_settings
    from wpmcp.config import Settings

    env = {
        "WPMCP_SECRET": "s" * 40,
        "WPMCP_PUBLIC_URL": "https://mcp.example.org",
        "WPMCP_DB": str(tmp_path / "x.sqlite"),
        "WPMCP_PROTECTED_IDS": "12, 34",
        "WPMCP_LIVE_BUDGET": "5",
    }
    settings = Settings.from_env(env)
    assert settings.protected_ids == frozenset({12, 34})
    assert settings.live_budget == 5
    with pytest.raises(ValueError, match="LIVE_BUDGET"):
        make_settings(live_budget=-1)


IDEMPOTENT = {
    "update_draft", "update_media", "publish_content", "schedule_content",
    "unpublish_content", "update_published_content", "insert_image", "restore_revision",
    "trash_content", "link_translations", "apply_menu",
}  # fmt: skip
NOT_IDEMPOTENT = {
    "create_draft", "create_category", "import_image", "request_image_upload",
    "restore_menu", "restore_from_trash",
}  # fmt: skip


def test_every_tool_declares_an_object_output_and_its_idempotency(live_app, live_client, clock):
    token, _ = access_token(live_app, clock)
    tools = {t["name"]: t for t in rpc_result(rpc(live_client, token, "tools/list"))["tools"]}
    for name, tool in tools.items():
        assert tool["outputSchema"]["type"] == "object", name
        hints = tool["annotations"]
        if hints["readOnlyHint"]:
            assert hints["idempotentHint"] is True, name
    for name in IDEMPOTENT:
        assert tools[name]["annotations"]["idempotentHint"] is True, name
    for name in NOT_IDEMPOTENT:
        assert tools[name]["annotations"]["idempotentHint"] is False, name


def test_results_come_as_structured_content_too(live_app, live_client, clock, fake):
    token, _ = access_token(live_app, clock)
    result = call(live_client, token, "publish_content", {"content_id": 8, "expected_version": V})
    assert result["structuredContent"] == json.loads(result["content"][0]["text"])
    assert result["structuredContent"]["id"] == 501
