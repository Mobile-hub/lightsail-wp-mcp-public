from __future__ import annotations

import json
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from wpmcp.app import create_app
from wpmcp.auth.owner import hash_password, totp_at
from wpmcp.auth.store import Store
from wpmcp.auth.tokens import pkce_s256
from wpmcp.config import Settings

SECRET = "x" * 48
PASSWORD = "correct horse battery staple"
TOTP_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
BASE = "https://mcp.example.test"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"


class Clock:
    """Controllable time: every consent needs a fresh TOTP step, so tests advance it."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_settings(**overrides) -> Settings:
    values = dict(
        secret=SECRET, public_url=BASE, db_path=":memory:", site_name="Example Books & Co"
    )
    values.update(overrides)
    return Settings(**values)


def make_store(clock: Clock, path: str = ":memory:") -> Store:
    store = Store(path)
    # Cheap scrypt parameters: the cost is a property of the stored hash, not of the code.
    store.set_owner(hash_password(PASSWORD, n=2**4, r=8, p=1), TOTP_SECRET, clock())
    return store


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def store(clock):
    opened = make_store(clock)
    yield opened
    opened.close()


@pytest.fixture
def app(settings, store, clock):
    return create_app(settings, store=store, clock=clock)


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE) as test_client:
        yield test_client


@pytest.fixture
def reg(store, clock) -> dict:
    return register(store, clock)


def register(store: Store, clock: Clock, name: str = "Claude", redirect: str = REDIRECT) -> dict:
    registered, secret = store.add_client(name, [redirect], now=clock())
    return {"client_id": registered.client_id, "client_secret": secret, "redirect_uri": redirect}


def totp(clock: Clock) -> str:
    return totp_at(TOTP_SECRET, int(clock()) // 30)


def consent_token(client, reg: dict, challenge: str = "x" * 43, **params) -> str:
    """Open the consent screen and return the signed request it embeds."""
    response = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": reg["client_id"],
            "redirect_uri": reg["redirect_uri"],
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "st4te",
            **params,
        },
    )
    assert response.status_code == 200, response.text
    return response.text.split('name=request value="')[1].split('"')[0]


def decide(client, clock: Clock, request_token: str, *, access="write", password=PASSWORD, **form):
    """Submit the consent form with a fresh TOTP code. Returns the raw response."""
    clock.advance(30)
    data = {
        "request": request_token,
        "password": password,
        "totp": totp(clock),
        "decision": "approve",
        "access": access,
    }
    data.update(form)
    data = {k: v for k, v in data.items() if v is not None}
    return client.post("/authorize", data=data, follow_redirects=False)


def location_params(response) -> dict:
    assert response.status_code == 302, response.text
    return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}


def authorize(client, clock: Clock, reg: dict, verifier: str, *, access="write", **params) -> dict:
    """Run the consent screen and return the redirect's query parameters."""
    token = consent_token(client, reg, pkce_s256(verifier), **params)
    return location_params(decide(client, clock, token, access=access))


def exchange(client, reg: dict, code: str, verifier: str, **extra):
    return client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": reg["redirect_uri"],
            "client_id": reg["client_id"],
            "client_secret": reg["client_secret"],
            "code_verifier": verifier,
            **extra,
        },
    )


def full_flow(client, clock: Clock, reg: dict, *, access="write", **params) -> dict:
    verifier = secrets.token_urlsafe(48)
    code = authorize(client, clock, reg, verifier, access=access, **params)["code"]
    response = exchange(client, reg, code, verifier)
    assert response.status_code == 200, response.text
    return response.json()


def refresh(client, reg: dict, refresh_token: str, **extra):
    return client.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": reg["client_id"],
            "client_secret": reg["client_secret"],
            **extra,
        },
    )


def access_token(app, clock: Clock, scope: str = "wp:read wp:write", client_id: str | None = None):
    """An access token for a fresh grant, without the browser flow."""
    store, settings = app.state.store, app.state.settings
    if client_id is None:
        client_id = register(store, clock)["client_id"]
    grant = store.create_grant(client_id, scope, settings.resource, now=clock())
    return app.state.oauth.mint_access_token(grant), grant


def rpc(client, token: str | None, method: str, params: dict | None = None, **headers):
    """One JSON-RPC message to the MCP endpoint (stateless Streamable HTTP)."""
    auth = {"authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"accept": "application/json, text/event-stream", **auth, **headers},
    )


def rpc_result(response) -> dict:
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("text/event-stream"):
        data = [line[5:] for line in response.text.splitlines() if line.startswith("data:")]
        message = json.loads(data[-1])
    else:
        message = response.json()
    assert "error" not in message, message
    return message["result"]
