"""Bulk tools: argument mapping, budgets counted per item, protections and the audit line,
against a fake bridge. What the bridge does with each item is tested in tests/integration."""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from starlette.testclient import TestClient

from tests.conftest import BASE, access_token, rpc, rpc_result
from tests.unit.test_write_tools import FakeWordPress, build, call, payload

V = "0123456789abcdef"
LINK = '<p>See <a href="https://books.example.test/guide/">the guide</a>.</p>'


def items(*ids: int) -> list[dict]:
    return [{"content_id": i, "expected_version": V} for i in ids]


def bulk_answer(changed: int = 1) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "dry_run": False,
            "batch_id": "0123456789ab",
            "counts": {"changed": changed, "unchanged": 0, "refused": 0},
            "changed": [{"id": 8, "version": V}],
            "unchanged": [],
            "refused": [],
        },
    )


@pytest.fixture
def fake():
    wp = FakeWordPress()
    wp.responses["/bulk/links"] = bulk_answer()
    wp.responses["/bulk/seo"] = bulk_answer()
    return wp


@pytest.fixture
def app(store, clock, fake):
    return build(store, clock, fake, protected_ids=frozenset({42}), live_budget=5)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE) as test_client:
        yield test_client


def test_bulk_tools_need_write_and_are_marked(app, client, clock):
    read_only, _ = access_token(app, clock, scope="wp:read")
    names = {t["name"] for t in rpc_result(rpc(client, read_only, "tools/list"))["tools"]}
    assert names.isdisjoint({"bulk_add_links", "bulk_update_seo", "undo_bulk_change"})
    token, _ = access_token(app, clock)
    tools = {t["name"]: t for t in rpc_result(rpc(client, token, "tools/list"))["tools"]}
    for name in ("bulk_add_links", "bulk_update_seo", "undo_bulk_change"):
        hints = tools[name]["annotations"]
        assert (hints["readOnlyHint"], hints["destructiveHint"], hints["idempotentHint"]) == (
            False,
            True,
            True,
        ), name
    assert tools["bulk_add_links"]["inputSchema"]["properties"]["items"]["maxItems"] == 50


def test_bulk_add_links_maps_to_the_bridge(app, client, clock, fake):
    token, _ = access_token(app, clock)
    arguments = {"target_id": 9, "html": LINK, "items": items(8, 10), "dry_run": True}
    payload(call(client, token, "bulk_add_links", arguments))
    request = fake.requests[-1]
    assert request.method == "POST" and request.url.path.endswith("/bulk/links")
    assert fake.json_body() == {
        "target_id": 9,
        "html": LINK,
        "position": "end",
        "dry_run": True,
        "items": [{"id": 8, "expected_version": V}, {"id": 10, "expected_version": V}],
    }


def test_bulk_update_seo_sends_only_the_fields_given(app, client, clock, fake):
    token, _ = access_token(app, clock)
    seo_items = [
        {"content_id": 8, "expected_version": V, "seo": {"title": "T", "description": None}},
        {"content_id": 10, "expected_version": V, "seo": {}},
    ]
    payload(call(client, token, "bulk_update_seo", {"items": seo_items}))
    assert fake.requests[-1].url.path.endswith("/bulk/seo")
    assert fake.json_body() == {
        "dry_run": False,
        "items": [
            {"id": 8, "expected_version": V, "seo": {"title": "T", "description": None}},
            {"id": 10, "expected_version": V, "seo": {}},
        ],
    }


def test_each_item_counts_against_the_budgets_and_a_dry_run_none(app, client, clock, fake):
    token, _ = access_token(app, clock)
    for _ in range(3):  # dry runs: free
        dry = {"target_id": 9, "html": LINK, "items": items(1, 2, 3, 4), "dry_run": True}
        assert not call(client, token, "bulk_add_links", dry).get("isError")
    run = {"target_id": 9, "html": LINK, "items": items(1, 2, 3, 4)}
    assert not call(client, token, "bulk_add_links", run).get("isError")  # 4 of 5
    refused = call(client, token, "bulk_add_links", run)
    assert refused["isError"] is True
    assert "would make 4 changes" in str(refused) and "1 live changes left" in str(refused)
    # Refused whole: the one change left is still there.
    one = {"target_id": 9, "html": LINK, "items": items(1)}
    assert not call(client, token, "bulk_add_links", one).get("isError")
    spent = call(client, token, "bulk_add_links", one)
    assert spent["isError"] is True and "limit of live changes" in str(spent)
    assert sum(r.url.path.endswith("/bulk/links") for r in fake.requests) == 5


def test_protected_items_refuse_the_whole_call(app, client, clock, fake):
    token, _ = access_token(app, clock)
    result = call(
        client, token, "bulk_add_links", {"target_id": 9, "html": LINK, "items": items(8, 42)}
    )
    assert result["isError"] is True and "[42] are protected" in str(result)
    assert fake.requests == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"target_id": 9, "html": LINK, "items": []},
        {"target_id": 9, "html": LINK, "items": items(*range(1, 52))},
        {"target_id": 9, "html": "", "items": items(8)},
        {"target_id": 9, "html": LINK, "items": [{"content_id": 8, "expected_version": "x"}]},
        {"target_id": 9, "html": LINK, "items": items(8), "position": "start"},
    ],
)
def test_invalid_bulk_calls_never_reach_wordpress(app, client, clock, fake, arguments):
    token, _ = access_token(app, clock)
    assert call(client, token, "bulk_add_links", arguments)["isError"] is True
    assert fake.requests == []


def test_bulk_writes_are_audited_by_batch_without_contents(app, client, clock, caplog):
    token, grant = access_token(app, clock)
    with caplog.at_level(logging.INFO, logger="wpmcp.audit"):
        call(client, token, "bulk_add_links", {"target_id": 9, "html": LINK, "items": items(8)})
    writes = [json.loads(r.message) for r in caplog.records if "wp_write" in r.message]
    assert writes == [
        {
            "event": "wp_write",
            "tool": "bulk_add_links",
            "object": "0123456789ab",
            "items": 1,
            "client": grant.client_id,
            "grant": grant.grant_id,
        }
    ]
    assert "the guide" not in caplog.text


def test_undo_bulk_change(app, client, clock, fake):
    fake.responses["/bulk/0123456789ab/undo"] = httpx.Response(
        200, json={"batch_id": "0123456789ab", "counts": {"undone": 2, "skipped": 0}}
    )
    token, _ = access_token(app, clock)
    result = payload(call(client, token, "undo_bulk_change", {"batch_id": "0123456789ab"}))
    assert result["counts"]["undone"] == 2
    assert fake.requests[-1].url.path.endswith("/bulk/0123456789ab/undo")
    bad = call(client, token, "undo_bulk_change", {"batch_id": "../x"})
    assert bad["isError"] is True and len(fake.requests) == 1
