import io
import json

import pytest
from starlette.testclient import TestClient

from wpmcp import cli
from wpmcp.app import create_app
from wpmcp.auth.owner import match_totp, verify_password
from wpmcp.auth.store import Store
from wpmcp.config import Settings

from ..conftest import BASE, SECRET, rpc, rpc_result


@pytest.fixture
def env(monkeypatch, tmp_path) -> str:
    path = str(tmp_path / "state.sqlite")
    monkeypatch.setenv("WPMCP_DB", path)
    monkeypatch.setenv("WPMCP_SECRET", SECRET)
    monkeypatch.setenv("WPMCP_PUBLIC_URL", BASE)
    monkeypatch.delenv("WPMCP_SECRET_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("WPMCP_UDS", raising=False)
    return path


def test_db_is_required(monkeypatch, capsys):
    monkeypatch.delenv("WPMCP_DB", raising=False)
    assert cli.main(["client", "list"]) == 2
    assert "WPMCP_DB is required" in capsys.readouterr().err


def test_owner_set_from_stdin(env, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("a very long passphrase\n"))
    assert cli.main(["owner", "set", "--password-stdin", "--issuer", "EXAMPLE"]) == 0
    out = capsys.readouterr().out
    secret = out.split("secret: ")[1].split()[0]
    assert f"secret={secret}" in out and "issuer=EXAMPLE" in out
    owner = Store(env).get_owner()
    assert owner.totp_secret == secret
    assert verify_password("a very long passphrase", owner.password_hash)
    assert "a very long passphrase" not in out
    assert match_totp(secret, "000000", 0) is None


def test_owner_set_interactive(env, monkeypatch, capsys):
    answers = iter(["a very long passphrase", "a very long passphrase"])
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(answers))
    assert cli.main(["owner", "set"]) == 0
    assert Store(env).get_owner() is not None


def test_owner_set_rejects_mismatch_and_short_passwords(env, monkeypatch, capsys):
    answers = iter(["one long passphrase", "another long passphrase"])
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(answers))
    assert cli.main(["owner", "set"]) == 2
    assert "do not match" in capsys.readouterr().err
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert cli.main(["owner", "set", "--password-stdin"]) == 2
    assert "at least 12" in capsys.readouterr().err
    assert Store(env).get_owner() is None


def test_client_add_presets(env, capsys):
    assert cli.main(["client", "add", "--preset", "claude"]) == 0
    out = capsys.readouterr().out
    client_id = out.split("client_id:")[1].split()[0]
    secret = out.split("client_secret:")[1].split()[0]
    store = Store(env)
    client = store.get_client(client_id)
    assert client.name == "Claude"
    assert client.redirect_uris == ("https://claude.ai/api/mcp/auth_callback",)
    assert store.verify_client_secret(client_id, secret)

    assert cli.main(["client", "add", "--preset", "chatgpt", "--name", "My ChatGPT"]) == 0
    capsys.readouterr()
    names = {c.name: c.redirect_uris for c in Store(env).list_clients()}
    assert names["My ChatGPT"] == ("https://chatgpt.com/connector_platform_oauth_redirect",)


def test_client_add_custom_and_validation(env, capsys):
    args = ["client", "add", "--name", "Inspector", "--redirect-uri"]
    assert cli.main([*args, "https://inspector.example/cb"]) == 0
    capsys.readouterr()
    for bad in ("http://localhost:6274/cb", "https://x.example/cb#frag", "javascript:alert(1)"):
        assert cli.main([*args, bad]) == 2, bad
        assert "https URLs" in capsys.readouterr().err
    assert cli.main(["client", "add", "--name", "No redirect"]) == 2
    assert cli.main(["client", "add", "--redirect-uri", "https://x.example/cb"]) == 2
    assert len(Store(env).list_clients()) == 1


def test_client_list_and_disable(env, capsys):
    cli.main(["client", "add", "--preset", "claude"])
    client_id = capsys.readouterr().out.split("client_id:")[1].split()[0]
    assert cli.main(["client", "list"]) == 0
    assert f"{client_id}\tenabled\tClaude" in capsys.readouterr().out
    assert cli.main(["client", "disable", client_id]) == 0
    capsys.readouterr()
    assert cli.main(["client", "list"]) == 0
    assert f"{client_id}\tdisabled" in capsys.readouterr().out
    assert cli.main(["client", "disable", client_id]) == 2


def test_grant_list_revoke_and_revoke_all(env, capsys):
    store = Store(env)
    a = store.create_grant("c1", "wp:read", f"{BASE}/mcp")
    b = store.create_grant("c1", "wp:read wp:write", f"{BASE}/mcp")
    assert cli.main(["grant", "list"]) == 0
    out = capsys.readouterr().out
    assert a.grant_id in out and b.grant_id in out and "active" in out

    assert cli.main(["grant", "revoke", a.grant_id]) == 0
    assert cli.main(["grant", "revoke", a.grant_id]) == 2
    capsys.readouterr()
    assert cli.main(["grant", "list"]) == 0
    assert a.grant_id not in capsys.readouterr().out
    assert cli.main(["grant", "list", "--all"]) == 0
    assert "revoked (revoked by the owner)" in capsys.readouterr().out

    assert cli.main(["revoke-all"]) == 0
    assert "1 connection(s) revoked" in capsys.readouterr().out
    assert Store(env).list_grants() == []


def test_cli_token_is_read_only_by_default_and_revocable(env, capsys):
    assert cli.main(["token"]) == 0
    token = capsys.readouterr().out.strip()
    store = Store(env)
    app = create_app(Settings.from_env(), store=store)
    with TestClient(app, base_url=BASE) as client:
        result = rpc_result(
            rpc(client, token, "tools/call", {"name": "get_connection_info", "arguments": {}})
        )
        info = json.loads(result["content"][0]["text"])
        assert info["scopes"] == ["wp:read"] and info["client"] == "wpmcp token (CLI)"
        assert cli.main(["revoke-all"]) == 0
        assert rpc(client, token, "tools/list").status_code == 401


def test_cli_token_write_json_and_ttl_limits(env, capsys):
    assert cli.main(["token", "--write", "--json", "--ttl", "60"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["expires_in"] == 60 and body["token_type"] == "Bearer"
    grants = Store(env).list_grants()
    assert grants[0].scope == "wp:read wp:write" and grants[0].client_id == "cli"
    assert cli.main(["token", "--ttl", "3601"]) == 2
    assert cli.main(["token", "--ttl", "0"]) == 2


def test_configuration_errors_are_reported(env, monkeypatch, capsys):
    monkeypatch.setenv("WPMCP_PUBLIC_URL", "http://not-https.example")
    assert cli.main(["token"]) == 2
    assert "https" in capsys.readouterr().err


def test_serve_never_trusts_proxy_headers(env, monkeypatch):
    """uvicorn's proxy_headers would take the first, client-controlled X-Forwarded-For hop."""
    import uvicorn

    calls = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.update(kwargs))
    assert cli.main(["serve"]) == 0
    assert calls["proxy_headers"] is False
    assert calls["host"] == "127.0.0.1" and calls["port"] == 8080
    assert calls["server_header"] is False

    monkeypatch.setenv("WPMCP_UDS", "/run/wpmcp/wpmcp.sock")
    calls.clear()
    assert cli.main(["serve"]) == 0
    assert calls["uds"] == "/run/wpmcp/wpmcp.sock" and "host" not in calls


def test_media_worker_answers_one_request_on_stdio(monkeypatch):
    import io
    import json
    import sys

    from PIL import Image

    image = io.BytesIO()
    Image.new("RGB", (4, 4)).save(image, "PNG")
    data = image.getvalue()
    request = json.dumps({"op": "sanitize", "length": len(data)}).encode() + b"\n" + data

    class Stream:
        def __init__(self, buffer):
            self.buffer = buffer

    stdout = io.BytesIO()
    monkeypatch.setattr(sys, "stdin", Stream(io.BytesIO(request)))
    monkeypatch.setattr(sys, "stdout", Stream(stdout))
    assert cli.main(["media-worker"]) == 0
    assert json.loads(stdout.getvalue().split(b"\n", 1)[0])["ok"] is True
