from starlette.requests import Request
from starlette.testclient import TestClient

from wpmcp.app import create_app
from wpmcp.auth.tokens import Signer
from wpmcp.config import _networks
from wpmcp.net import client_ip, peer_is_trusted

from ..conftest import BASE, SECRET, access_token, make_settings, make_store, register, rpc


def test_missing_token_gets_a_discovery_challenge(client):
    response = rpc(client, None, "tools/list")
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge == (
        f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"'
    )


def test_non_bearer_scheme_is_unauthorized(client):
    response = client.post("/mcp", json={}, headers={"authorization": "Basic abc"})
    assert response.status_code == 401


def test_garbage_token(client):
    response = rpc(client, "a.b.c", "tools/list")
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_valid_token_reaches_the_mcp_server(client, app, clock):
    token, _grant = access_token(app, clock)
    assert rpc(client, token, "tools/list").status_code == 200


def test_expired_token(client, app, clock):
    token, _grant = access_token(app, clock)
    clock.advance(901)
    response = rpc(client, token, "tools/list")
    assert response.status_code == 401 and "expired" in response.json()["error_description"]


def _forged(app, clock, **claims) -> str:
    """A correctly signed access token whose claims differ from what this server issues."""
    _token, grant = access_token(app, clock)
    base = dict(
        sub="owner",
        client_id=grant.client_id,
        scope="wp:read wp:write",
        iss=BASE,
        aud=f"{BASE}/mcp",
        gid=grant.grant_id,
        dbid=app.state.store.db_id,
    )
    base.update(claims)
    return Signer(SECRET).issue("access", 900, now=clock(), **base)


def test_wrong_audience(client, app, clock):
    response = rpc(client, _forged(app, clock, aud="https://other.example/mcp"), "tools/list")
    assert response.status_code == 401 and "audience" in response.json()["error_description"]


def test_wrong_issuer(client, app, clock):
    response = rpc(client, _forged(app, clock, iss="https://other.example"), "tools/list")
    assert response.status_code == 401 and "issuer" in response.json()["error_description"]


def test_token_from_another_database(client, app, clock):
    response = rpc(client, _forged(app, clock, dbid="another"), "tools/list")
    assert response.status_code == 401 and "database" in response.json()["error_description"]


def test_unknown_grant(client, app, clock):
    assert rpc(client, _forged(app, clock, gid="missing"), "tools/list").status_code == 401


def test_token_of_another_type_is_not_an_access_token(client, app, clock):
    token = Signer(SECRET).issue("authz_request", 900, now=clock(), aud=f"{BASE}/mcp", iss=BASE)
    assert rpc(client, token, "tools/list").status_code == 401


def test_token_cannot_claim_more_than_its_grant(client, app, clock):
    """Scopes are intersected with the stored grant: a read grant never gets write."""
    _token, grant = access_token(app, clock, scope="wp:read")
    token = _forged(app, clock, gid=grant.grant_id, scope="wp:read wp:write")
    principal = app.state.oauth.verify_access_token(token)
    assert principal.scopes == frozenset({"wp:read"})


def test_token_without_usable_scope_is_forbidden(client, app, clock):
    _token, grant = access_token(app, clock, scope="wp:read")
    token = _forged(app, clock, gid=grant.grant_id, scope="other")
    response = rpc(client, token, "tools/list")
    assert response.status_code == 403 and response.json()["error"] == "insufficient_scope"


def test_revoked_grant(client, app, clock):
    token, grant = access_token(app, clock)
    app.state.store.revoke_grant(grant.grant_id, "test")
    response = rpc(client, token, "tools/list")
    assert response.status_code == 401 and "revoked" in response.json()["error_description"]


def test_disabled_client(client, app, clock):
    token, grant = access_token(app, clock)
    app.state.store.disable_client(grant.client_id)
    assert rpc(client, token, "tools/list").status_code == 401


def test_only_post_is_served(client, app, clock):
    token, _grant = access_token(app, clock)
    for method in ("GET", "DELETE", "PUT"):
        response = client.request(method, "/mcp", headers={"authorization": f"Bearer {token}"})
        assert response.status_code == 405 and response.headers["allow"] == "POST"


def test_declared_oversized_body_is_refused(clock):
    store = make_store(clock)
    app = create_app(make_settings(max_body_bytes=200), store=store, clock=clock)
    token, _grant = access_token(app, clock)
    with TestClient(app, base_url=BASE) as client:
        response = client.post(
            "/mcp",
            content=b"x" * 500,
            headers={"authorization": f"Bearer {token}", "content-type": "application/json"},
        )
    assert response.status_code == 413


def test_security_headers_on_every_response(client):
    for path in ("/healthz", "/mcp", "/nope"):
        headers = client.get(path).headers
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_root_and_health(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/").json()["mcp"] == "/mcp"


def test_oauth_body_cap(client):
    response = client.post(
        "/token",
        content=b"a=" + b"x" * 70_000,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 413


# ------------------------------------------------------------------------- address allowlist
def _allowlist_app(tmp_path, clock, mode, networks="203.0.113.0/24\n"):
    cidrs = tmp_path / "cidrs.txt"
    cidrs.write_text(networks)
    # The TestClient peer ("testclient") is trusted here so X-Forwarded-For sets the address,
    # exactly as the local reverse proxy does in production.
    settings = make_settings(cidr_file=str(cidrs), cidr_mode=mode, trust_unix_peer=True)
    store = make_store(clock)
    return create_app(settings, store=store, clock=clock)


def test_allowlist_enforced_on_mcp_and_token(tmp_path, clock, monkeypatch):
    monkeypatch.setattr("wpmcp.net.peer_ip", lambda _request: "")
    app = _allowlist_app(tmp_path, clock, "enforce")
    token, _grant = access_token(app, clock)
    with TestClient(app, base_url=BASE) as client:
        inside = {"x-forwarded-for": "203.0.113.9"}
        outside = {"x-forwarded-for": "198.51.100.7"}
        assert rpc(client, token, "tools/list", **inside).status_code == 200
        assert rpc(client, token, "tools/list", **outside).status_code == 403
        assert client.post("/token", data={}, headers=outside).status_code == 403
        assert client.post("/token", data={}, headers=inside).status_code == 401
        # The owner's browser reaches the consent screen from anywhere.
        assert client.get("/authorize", headers=outside).status_code == 400
        assert client.get("/healthz", headers=outside).status_code == 200


def test_allowlist_takes_the_last_forwarded_hop(tmp_path, clock, monkeypatch):
    """A client-supplied first hop inside the allowlist does not help."""
    monkeypatch.setattr("wpmcp.net.peer_ip", lambda _request: "")
    app = _allowlist_app(tmp_path, clock, "enforce")
    token, _grant = access_token(app, clock)
    with TestClient(app, base_url=BASE) as client:
        spoofed = {"x-forwarded-for": "203.0.113.9, 198.51.100.7"}
        assert rpc(client, token, "tools/list", **spoofed).status_code == 403


def test_allowlist_monitor_mode_allows(tmp_path, clock, monkeypatch, caplog):
    monkeypatch.setattr("wpmcp.net.peer_ip", lambda _request: "")
    app = _allowlist_app(tmp_path, clock, "monitor")
    token, _grant = access_token(app, clock)
    with TestClient(app, base_url=BASE) as client, caplog.at_level("WARNING", "wpmcp.cidrs"):
        response = rpc(client, token, "tools/list", **{"x-forwarded-for": "198.51.100.7"})
    assert response.status_code == 200
    assert "outside the CIDR allowlist" in caplog.text


# ------------------------------------------------------------------------------ rate limits
def test_mcp_rate_limit(client, app, clock, monkeypatch):
    from wpmcp.auth import bearer

    token, _grant = access_token(app, clock)
    gate = next(r for r in app.routes if getattr(r, "path", "") == "/mcp").app
    assert isinstance(gate, bearer.McpGate)
    gate.limiter = bearer.SlidingWindow(limit=2, window=60.0)
    assert rpc(client, token, "tools/list").status_code == 200
    assert rpc(client, token, "tools/list").status_code == 200
    limited = rpc(client, token, "tools/list")
    assert limited.status_code == 429 and limited.headers["retry-after"] == "60"


def test_token_endpoint_rate_limit(client):
    for _ in range(60):
        assert client.post("/token", data={}).status_code == 401
    assert client.post("/token", data={}).status_code == 429


# --------------------------------------------------------------------------- client address
def _request(peer, forwarded=None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request({"type": "http", "headers": headers, "client": peer})


def test_forwarded_for_ignored_from_untrusted_peer():
    settings = make_settings()
    request = _request(("198.51.100.1", 1234), "203.0.113.5")
    assert client_ip(request, settings) == "198.51.100.1"
    assert not peer_is_trusted(request, settings)


def test_forwarded_for_last_hop_from_trusted_peer():
    settings = make_settings(trusted_proxy_cidrs=_networks(("127.0.0.0/8",)))
    request = _request(("127.0.0.1", 1234), "10.0.0.1, 203.0.113.5")
    assert client_ip(request, settings) == "203.0.113.5"


def test_unix_socket_peer_trusted_only_when_configured():
    request = _request(None, "203.0.113.5")
    assert client_ip(request, make_settings()) == ""
    assert client_ip(request, make_settings(trust_unix_peer=True)) == "203.0.113.5"
    assert client_ip(_request(("", 0), "203.0.113.5"), make_settings(trust_unix_peer=True)) == (
        "203.0.113.5"
    )


def test_trusted_peer_without_header_is_itself():
    settings = make_settings()
    assert client_ip(_request(("127.0.0.1", 1)), settings) == "127.0.0.1"
    assert client_ip(_request(("not-an-ip", 1), "203.0.113.5"), settings) == "not-an-ip"


def test_register_helper_creates_distinct_clients(store, clock):
    assert register(store, clock)["client_id"] != register(store, clock)["client_id"]
