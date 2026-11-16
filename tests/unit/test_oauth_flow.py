import base64
import secrets

from starlette.testclient import TestClient

from wpmcp.app import create_app
from wpmcp.auth.oauth import GLOBAL_FAILURE_LIMIT, IP_FAILURE_LIMIT
from wpmcp.auth.store import Store
from wpmcp.auth.tokens import pkce_s256

from ..conftest import (
    BASE,
    REDIRECT,
    authorize,
    consent_token,
    decide,
    exchange,
    full_flow,
    location_params,
    make_settings,
    make_store,
    refresh,
    register,
)


# ----------------------------------------------------------------------------- metadata
def test_protected_resource_metadata(client):
    for path in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ):
        body = client.get(path).json()
        assert body["resource"] == f"{BASE}/mcp"
        assert body["authorization_servers"] == [BASE]
        assert body["scopes_supported"] == ["wp:read", "wp:write"]


def test_authorization_server_metadata(client):
    for suffix in ("", "/mcp"):
        body = client.get(f"/.well-known/oauth-authorization-server{suffix}").json()
        assert body["issuer"] == BASE
        assert body["code_challenge_methods_supported"] == ["S256"]
        assert body["authorization_response_iss_parameter_supported"] is True
        # Confidential, pre-registered clients only.
        assert "none" not in body["token_endpoint_auth_methods_supported"]
        assert "registration_endpoint" not in body


def test_no_dynamic_registration_and_no_oidc(client):
    assert client.post("/register", json={"redirect_uris": [REDIRECT]}).status_code == 404
    assert client.get("/.well-known/openid-configuration").status_code == 404


# ----------------------------------------------------------------------------- happy path
def test_full_flow_read_write(client, clock, reg):
    tokens = full_flow(client, clock, reg)
    assert tokens["token_type"] == "Bearer"
    assert tokens["scope"] == "wp:read wp:write"
    assert tokens["expires_in"] == 900
    principal = client.app.state.oauth.verify_access_token(tokens["access_token"])
    assert principal.client_name == "Claude"
    assert principal.can("wp:write") and principal.can("wp:read")


def test_read_only_choice_narrows_the_grant(client, clock, reg):
    tokens = full_flow(client, clock, reg, access="read")
    assert tokens["scope"] == "wp:read"
    principal = client.app.state.oauth.verify_access_token(tokens["access_token"])
    assert principal.can("wp:read") and not principal.can("wp:write")


def test_write_is_never_granted_when_not_requested(client, clock, reg):
    """The client asked for wp:read only: choosing 'write' on a forged form adds nothing."""
    tokens = full_flow(client, clock, reg, access="write", scope="wp:read")
    assert tokens["scope"] == "wp:read"


def test_write_choice_is_not_offered_when_not_requested(client, reg):
    response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": reg["client_id"],
            "redirect_uri": REDIRECT,
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256",
            "scope": "wp:read",
        },
    )
    assert "value=read" in response.text and "value=write" not in response.text


def test_write_only_request_with_read_choice_still_reads(client, clock, reg):
    tokens = full_flow(client, clock, reg, access="read", scope="wp:write")
    assert tokens["scope"] == "wp:read"


def test_state_and_iss_are_returned(client, clock, reg):
    params = authorize(client, clock, reg, secrets.token_urlsafe(48))
    assert params["state"] == "st4te"
    assert params["iss"] == BASE


def test_redirect_uri_may_be_omitted_with_a_single_registration(client, reg):
    response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": reg["client_id"],
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 200


def test_consent_page_names_the_client_and_warns(client, reg):
    response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": reg["client_id"],
            "redirect_uri": REDIRECT,
            "code_challenge": "x" * 43,
            "code_challenge_method": "S256",
        },
    )
    assert "Claude" in response.text
    assert "Example Books &amp; Co" in response.text
    assert "did not start this connection" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_confidential_client_basic_auth(client, clock, reg):
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier)["code"]
    basic = base64.b64encode(f"{reg['client_id']}:{reg['client_secret']}".encode()).decode()
    data = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier}
    bad = client.post("/token", data=data, headers={"authorization": "Basic Zm9vOmJhcg=="})
    assert bad.status_code == 401
    malformed = client.post("/token", data=data, headers={"authorization": "Basic !!!"})
    assert malformed.status_code == 401
    ok = client.post("/token", data=data, headers={"authorization": f"Basic {basic}"})
    assert ok.status_code == 200, ok.text


# ----------------------------------------------------------------------- authorize failures
def test_unknown_client_does_not_redirect(client):
    response = client.get(
        "/authorize", params={"client_id": "nope", "redirect_uri": REDIRECT}, follow_redirects=False
    )
    assert response.status_code == 400


def test_disabled_client_cannot_start(client, store, reg):
    store.disable_client(reg["client_id"])
    response = client.get(
        "/authorize",
        params={"client_id": reg["client_id"], "redirect_uri": REDIRECT},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_redirect_uri_must_match_exactly(client, reg):
    for uri in (
        "https://attacker.example/cb",
        f"{REDIRECT}/extra",
        f"{REDIRECT}?x=1",
        "http://claude.ai/api/mcp/auth_callback",
    ):
        response = client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": reg["client_id"],
                "redirect_uri": uri,
                "code_challenge": "x" * 43,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        assert response.status_code == 400, uri


def _authorize_error(client, reg, **params) -> dict:
    query = {
        "response_type": "code",
        "client_id": reg["client_id"],
        "redirect_uri": REDIRECT,
        "code_challenge": "x" * 43,
        "code_challenge_method": "S256",
    }
    query.update(params)
    query = {k: v for k, v in query.items() if v is not None}
    return location_params(client.get("/authorize", params=query, follow_redirects=False))


def test_authorize_requires_response_type_code(client, reg):
    assert _authorize_error(client, reg, response_type="token")["error"] == (
        "unsupported_response_type"
    )


def test_authorize_requires_pkce_s256(client, reg):
    assert _authorize_error(client, reg, code_challenge=None)["error"] == "invalid_request"
    assert _authorize_error(client, reg, code_challenge_method="plain")["error"] == (
        "invalid_request"
    )
    assert _authorize_error(client, reg, code_challenge="short")["error"] == "invalid_request"


def test_authorize_rejects_unknown_scope(client, reg):
    error = _authorize_error(client, reg, scope="wp:read admin")
    assert error["error"] == "invalid_scope" and error["iss"] == BASE


def test_authorize_rejects_foreign_resource(client, reg):
    error = _authorize_error(client, reg, resource="https://other.example/mcp")
    assert error["error"] == "invalid_target"


def test_authorize_accepts_own_resource(client, reg):
    token = consent_token(client, reg, resource=f"{BASE}/mcp/")
    assert token


# ------------------------------------------------------------------------- consent failures
def test_deny_redirects_with_access_denied(client, clock, reg):
    token = consent_token(client, reg)
    params = location_params(decide(client, clock, token, decision="deny"))
    assert params["error"] == "access_denied"
    assert params["state"] == "st4te" and params["iss"] == BASE


def test_access_choice_is_required(client, clock, reg):
    token = consent_token(client, reg)
    response = decide(client, clock, token, access=None)
    assert response.status_code == 400 and "Choose read only" in response.text
    # Nothing was burned: the same code still works with a choice.
    clock.advance(-30)
    assert decide(client, clock, token).status_code == 302


def test_wrong_password_shows_form_again(client, clock, reg):
    token = consent_token(client, reg)
    response = decide(client, clock, token, password="bad")
    assert response.status_code == 401 and "Wrong password or code" in response.text


def test_wrong_totp_is_rejected(client, clock, reg):
    token = consent_token(client, reg)
    response = decide(client, clock, token, totp="000000")
    assert response.status_code == 401 and "Wrong password or code" in response.text


def test_missing_totp_is_rejected(client, clock, reg):
    token = consent_token(client, reg)
    assert decide(client, clock, token, totp="").status_code == 401


def test_totp_code_cannot_be_replayed(client, clock, reg):
    token = consent_token(client, reg)
    assert decide(client, clock, token).status_code == 302
    clock.advance(-30)  # the helper advances 30 s: stay on the step that was just used
    replay = decide(client, clock, token)
    assert replay.status_code == 401


def test_wrong_password_does_not_burn_the_totp_step(client, clock, reg):
    token = consent_token(client, reg)
    assert decide(client, clock, token, password="bad").status_code == 401
    clock.advance(-30)
    assert decide(client, clock, token).status_code == 302


def test_consent_lockout_per_address(client, clock, reg):
    token = consent_token(client, reg)
    for _ in range(IP_FAILURE_LIMIT):
        assert decide(client, clock, token, password="bad").status_code == 401
    # Even the right credentials are refused while locked out.
    assert decide(client, clock, token).status_code == 429
    clock.advance(601)
    token = consent_token(client, reg)
    assert decide(client, clock, token).status_code == 302


def test_lockout_key_ignores_spoofed_forwarded_for(client, clock, reg):
    """The TestClient peer is not a trusted proxy, so X-Forwarded-For cannot reset the count."""
    token = consent_token(client, reg)
    for i in range(IP_FAILURE_LIMIT):
        clock.advance(30)
        client.post(
            "/authorize",
            data={
                "request": token,
                "password": "x",
                "totp": "1",
                "decision": "approve",
                "access": "read",
            },
            headers={"x-forwarded-for": f"198.51.100.{i}"},
        )
    response = client.post(
        "/authorize",
        data={
            "request": token,
            "password": "x",
            "totp": "1",
            "decision": "approve",
            "access": "read",
        },
        headers={"x-forwarded-for": "198.51.100.99"},
    )
    assert response.status_code == 429


def test_lockout_survives_a_restart(tmp_path, clock):
    path = str(tmp_path / "state.sqlite")
    store = make_store(clock, path)
    reg = register(store, clock)
    with TestClient(create_app(make_settings(), store=store, clock=clock), base_url=BASE) as first:
        token = consent_token(first, reg)
        for _ in range(IP_FAILURE_LIMIT):
            decide(first, clock, token, password="bad")
    store.close()
    reopened = Store(path)
    with TestClient(
        create_app(make_settings(), store=reopened, clock=clock), base_url=BASE
    ) as second:
        token = consent_token(second, reg)
        assert decide(second, clock, token).status_code == 429


def test_global_lockout_across_addresses(client, clock, store, reg):
    for i in range(GLOBAL_FAILURE_LIMIT):
        store.record_failure(f"ip:203.0.113.{i}", clock())
    token = consent_token(client, reg)
    assert decide(client, clock, token).status_code == 429


def test_owner_not_configured(clock):
    store = Store(":memory:")
    reg = register(store, clock)
    with TestClient(create_app(make_settings(), store=store, clock=clock), base_url=BASE) as c:
        token = consent_token(c, reg)
        response = decide(c, clock, token)
    assert response.status_code == 503 and "wpmcp owner set" in response.text


def test_tampered_consent_request_rejected(client):
    response = client.post(
        "/authorize",
        data={"request": "a.b.c", "password": "x", "decision": "approve", "access": "read"},
    )
    assert response.status_code == 400


def test_expired_consent_request_rejected(client, clock, reg):
    token = consent_token(client, reg)
    clock.advance(601)
    assert decide(client, clock, token).status_code == 400


def test_client_disabled_between_consent_and_decision(client, clock, store, reg):
    token = consent_token(client, reg)
    store.disable_client(reg["client_id"])
    assert decide(client, clock, token).status_code == 400


# ---------------------------------------------------------------------------- token failures
def test_code_is_single_use_and_replay_revokes_the_grant(client, clock, reg):
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier)["code"]
    first = exchange(client, reg, code, verifier)
    assert first.status_code == 200
    replay = exchange(client, reg, code, verifier)
    assert replay.status_code == 400
    assert "already used" in replay.json()["error_description"]
    # The tokens issued from the leaked code are dead too.
    assert rpc_status(client, first.json()["access_token"]) == 401
    assert refresh(client, reg, first.json()["refresh_token"]).json()["error"] == "invalid_grant"


def rpc_status(client, token: str) -> int:
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "authorization": f"Bearer {token}",
            "accept": "application/json, text/event-stream",
        },
    ).status_code


def test_unknown_code(client, reg):
    assert exchange(client, reg, "nope", "v" * 50).json()["error"] == "invalid_grant"
    assert exchange(client, reg, "", "v" * 50).json()["error"] == "invalid_grant"


def test_expired_code(client, clock, reg):
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier)["code"]
    clock.advance(121)
    response = exchange(client, reg, code, verifier)
    assert response.json()["error"] == "invalid_grant"
    assert "expired" in response.json()["error_description"]


def test_wrong_pkce_verifier(client, clock, reg):
    code = authorize(client, clock, reg, "a" * 50)["code"]
    assert exchange(client, reg, code, "b" * 50).json()["error"] == "invalid_grant"
    # A failed attempt does not consume the code.
    assert exchange(client, reg, code, "a" * 50).status_code == 200


def test_malformed_code_verifier_is_invalid_grant(client, clock, reg):
    """RFC 7636 charset/length is checked first: no 500 on a non-ASCII verifier."""
    code = authorize(client, clock, reg, secrets.token_urlsafe(48))["code"]
    for bad in ("ñ" * 50, "short", ""):
        response = exchange(client, reg, code, bad)
        assert response.status_code == 400, bad
        assert response.json()["error"] == "invalid_grant"


def test_code_bound_to_client(client, clock, store, reg):
    other = register(store, clock, name="ChatGPT")
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier)["code"]
    assert exchange(client, other, code, verifier).json()["error"] == "invalid_grant"


def test_redirect_uri_mismatch_at_token(client, clock, reg):
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier)["code"]
    response = exchange(client, reg, code, verifier, redirect_uri="https://claude.ai/other")
    assert response.json()["error"] == "invalid_grant"


def test_resource_at_token_must_match(client, clock, reg):
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier)["code"]
    bad = exchange(client, reg, code, verifier, resource="https://other.example/mcp")
    assert bad.json()["error"] == "invalid_target"
    assert exchange(client, reg, code, verifier, resource=f"{BASE}/mcp").status_code == 200


def test_client_secret_is_required(client, clock, reg):
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier)["code"]
    assert exchange(client, reg, code, verifier, client_secret="wrong").status_code == 401
    assert exchange(client, reg, code, verifier, client_secret="").status_code == 401
    assert exchange(client, reg, code, verifier).status_code == 200


def test_unknown_or_disabled_client_at_token(client, clock, store, reg):
    response = client.post("/token", data={"grant_type": "authorization_code", "client_id": "x"})
    assert response.status_code == 401 and response.json()["error"] == "invalid_client"
    assert client.post("/token", data={"grant_type": "authorization_code"}).status_code == 401
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier)["code"]
    store.disable_client(reg["client_id"])
    assert exchange(client, reg, code, verifier).status_code == 401


def test_unsupported_grant(client, reg):
    response = client.post(
        "/token",
        data={
            "grant_type": "client_credentials",
            "client_id": reg["client_id"],
            "client_secret": reg["client_secret"],
        },
    )
    assert response.json()["error"] == "unsupported_grant_type"


# ------------------------------------------------------------------------------- refresh
def test_refresh_rotates(client, clock, reg):
    tokens = full_flow(client, clock, reg)
    clock.advance(10)
    response = refresh(client, reg, tokens["refresh_token"])
    assert response.status_code == 200
    body = response.json()
    assert body["refresh_token"] != tokens["refresh_token"]
    assert body["access_token"] != tokens["access_token"]
    assert body["scope"] == "wp:read wp:write"
    # The new one works in turn.
    clock.advance(10)
    assert refresh(client, reg, body["refresh_token"]).status_code == 200


def test_refresh_retry_within_grace_is_tolerated(client, clock, reg):
    tokens = full_flow(client, clock, reg)
    assert refresh(client, reg, tokens["refresh_token"]).status_code == 200
    clock.advance(30)
    assert refresh(client, reg, tokens["refresh_token"]).status_code == 200


def test_refresh_reuse_after_grace_revokes_the_grant(client, clock, reg):
    tokens = full_flow(client, clock, reg)
    rotated = refresh(client, reg, tokens["refresh_token"]).json()
    clock.advance(61)
    reuse = refresh(client, reg, tokens["refresh_token"])
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"
    # The whole connection is gone, including the legitimately rotated token.
    assert refresh(client, reg, rotated["refresh_token"]).json()["error"] == "invalid_grant"
    assert rpc_status(client, rotated["access_token"]) == 401


def test_expired_refresh_token_is_invalid_grant(client, clock, reg):
    tokens = full_flow(client, clock, reg)
    clock.advance(14 * 24 * 3600 + 1)
    response = refresh(client, reg, tokens["refresh_token"])
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


def test_unknown_refresh_token(client, reg):
    assert refresh(client, reg, "nope").json()["error"] == "invalid_grant"
    assert refresh(client, reg, "").json()["error"] == "invalid_grant"


def test_refresh_cannot_widen_scope(client, clock, reg):
    tokens = full_flow(client, clock, reg, access="read")
    response = refresh(client, reg, tokens["refresh_token"], scope="wp:read wp:write")
    assert response.json()["error"] == "invalid_scope"


def test_refresh_can_narrow_scope(client, clock, reg):
    tokens = full_flow(client, clock, reg)
    body = refresh(client, reg, tokens["refresh_token"], scope="wp:read").json()
    assert body["scope"] == "wp:read"
    principal = client.app.state.oauth.verify_access_token(body["access_token"])
    assert not principal.can("wp:write")


def test_refresh_bound_to_client(client, clock, store, reg):
    other = register(store, clock, name="ChatGPT")
    tokens = full_flow(client, clock, reg)
    assert refresh(client, other, tokens["refresh_token"]).json()["error"] == "invalid_grant"
    # ...and the attempt by the wrong client did not consume it.
    assert refresh(client, reg, tokens["refresh_token"]).status_code == 200


def test_refresh_resource_must_match(client, clock, reg):
    tokens = full_flow(client, clock, reg)
    bad = refresh(client, reg, tokens["refresh_token"], resource="https://other.example/mcp")
    assert bad.json()["error"] == "invalid_target"


def test_refresh_after_revocation(client, clock, store, reg):
    tokens = full_flow(client, clock, reg)
    assert store.revoke_all("test") == 1
    assert refresh(client, reg, tokens["refresh_token"]).json()["error"] == "invalid_grant"
    assert rpc_status(client, tokens["access_token"]) == 401


def test_pkce_helper_matches_flow(client, clock, reg):
    verifier = "v" * 60
    token = consent_token(client, reg, pkce_s256(verifier))
    code = location_params(decide(client, clock, token))["code"]
    assert exchange(client, reg, code, verifier).status_code == 200
