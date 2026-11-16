import os
import stat

import pytest

from wpmcp.auth.store import CLI_CLIENT_ID, Store

RESOURCE = "https://mcp.example.test/mcp"


@pytest.fixture
def db() -> Store:
    return Store(":memory:")


def _code(db: Store, client_id: str = "c1", now: float = 1000, ttl: int = 120) -> str:
    return db.create_code(
        client_id=client_id,
        redirect_uri="https://claude.ai/cb",
        code_challenge="c" * 43,
        scope="wp:read",
        resource=RESOURCE,
        ttl=ttl,
        now=now,
    )


def test_database_file_is_private_and_keeps_its_id(tmp_path):
    path = str(tmp_path / "state.sqlite")
    first = Store(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    db_id = first.db_id
    first.close()
    assert Store(path).db_id == db_id
    assert Store(str(tmp_path / "other.sqlite")).db_id != db_id


def test_owner_roundtrip_and_replace(db):
    assert db.get_owner() is None
    db.set_owner("hash1", "SECRET1", now=1)
    assert db.accept_totp_step(100)
    db.set_owner("hash2", "SECRET2", now=2)
    owner = db.get_owner()
    assert (owner.password_hash, owner.totp_secret, owner.totp_last_step) == ("hash2", "SECRET2", 0)


def test_totp_step_is_accepted_once_and_only_forward(db):
    db.set_owner("h", "S")
    assert db.accept_totp_step(100)
    assert not db.accept_totp_step(100)
    assert not db.accept_totp_step(99)
    assert db.accept_totp_step(101)


def test_totp_step_without_owner(db):
    assert not db.accept_totp_step(100)


def test_client_secret_is_stored_hashed(db):
    client, secret = db.add_client("Claude", ["https://claude.ai/cb"], now=5)
    assert client.enabled and client.redirect_uris == ("https://claude.ai/cb",)
    assert db.verify_client_secret(client.client_id, secret)
    assert not db.verify_client_secret(client.client_id, secret + "x")
    assert not db.verify_client_secret(client.client_id, None)
    assert not db.verify_client_secret("unknown", secret)
    stored = db._db.execute("SELECT secret_hash FROM clients").fetchone()[0]
    assert secret not in stored and len(stored) == 64


def test_get_and_list_clients(db):
    assert db.get_client(None) is None and db.get_client("nope") is None
    a, _ = db.add_client("A", ["https://a.example/cb"], now=1)
    b, _ = db.add_client("B", ["https://b.example/cb"], now=2)
    assert [c.client_id for c in db.list_clients()] == [a.client_id, b.client_id]
    assert db.get_client(a.client_id).name == "A"


def test_disable_client_revokes_its_grants(db):
    client, _ = db.add_client("A", ["https://a.example/cb"])
    other, _ = db.add_client("B", ["https://b.example/cb"])
    grant = db.create_grant(client.client_id, "wp:read", RESOURCE)
    kept = db.create_grant(other.client_id, "wp:read", RESOURCE)
    assert db.disable_client(client.client_id, now=50)
    assert not db.disable_client(client.client_id)
    assert not db.get_client(client.client_id).enabled
    revoked = db.get_grant(grant.grant_id)
    assert not revoked.active and revoked.revoked_reason == "client disabled"
    assert db.get_grant(kept.grant_id).active


def test_cli_client_is_created_once(db):
    assert db.ensure_cli_client().client_id == CLI_CLIENT_ID
    assert db.ensure_cli_client().client_id == CLI_CLIENT_ID
    assert len(db.list_clients()) == 1


def test_code_is_stored_hashed_and_redeemed_once(db):
    code = _code(db)
    assert code not in str(db._db.execute("SELECT code_hash FROM codes").fetchall()[0][0])
    found = db.get_code(code)
    assert found.used_at is None and found.expires_at == 1120 and found.grant_id is None
    assert db.get_code("other") is None

    grant = db.redeem_code(code, now=1010)
    assert grant.client_id == "c1" and grant.scope == "wp:read" and grant.resource == RESOURCE
    used = db.get_code(code)
    assert used.used_at == 1010 and used.grant_id == grant.grant_id
    assert db.redeem_code(code, now=1011) is None
    assert db.redeem_code("unknown") is None
    assert len(db.list_grants()) == 1


def test_grants_list_revoke(db):
    assert db.get_grant(None) is None and db.get_grant("nope") is None
    a = db.create_grant("c1", "wp:read", RESOURCE, now=1)
    b = db.create_grant("c1", "wp:read wp:write", RESOURCE, now=2)
    assert db.revoke_grant(a.grant_id, "because", now=9)
    assert not db.revoke_grant(a.grant_id, "again")
    assert not db.revoke_grant("nope", "x")
    assert [g.grant_id for g in db.list_grants()] == [b.grant_id]
    everything = db.list_grants(include_revoked=True)
    assert [g.grant_id for g in everything] == [a.grant_id, b.grant_id]
    assert everything[0].revoked_at == 9 and everything[0].revoked_reason == "because"
    assert db.revoke_all("stop") == 1
    assert db.revoke_all("stop") == 0


def _rotate(db, token, now, client_id="c1", grace=60):
    return db.rotate_refresh(token, client_id=client_id, ttl=1000, grace=grace, now=now)


def test_refresh_rotation(db):
    grant = db.create_grant("c1", "wp:read", RESOURCE, now=1)
    first = db.issue_refresh(grant.grant_id, ttl=1000, now=100)
    assert first not in str(db._db.execute("SELECT token_hash FROM refresh_tokens").fetchall())
    assert db.peek_refresh(first, now=101).grant_id == grant.grant_id

    status, rotated_grant, second = _rotate(db, first, now=200)
    assert status == "ok" and rotated_grant.grant_id == grant.grant_id and second != first
    assert db.get_grant(grant.grant_id).last_used_at == 200
    assert _rotate(db, second, now=300)[0] == "ok"


def test_refresh_reuse_within_grace_is_ok_and_after_it_revokes(db):
    grant = db.create_grant("c1", "wp:read", RESOURCE, now=1)
    first = db.issue_refresh(grant.grant_id, ttl=1000, now=100)
    _status, _grant, second = _rotate(db, first, now=200)
    assert _rotate(db, first, now=260)[0] == "ok"  # retry inside the grace window
    status, reused, token = _rotate(db, first, now=261)
    assert status == "reused" and reused.grant_id == grant.grant_id and token is None
    assert not db.get_grant(grant.grant_id).active
    assert db.get_grant(grant.grant_id).revoked_reason == "refresh token reuse"
    assert _rotate(db, second, now=262) == ("invalid", None, None)
    assert db.peek_refresh(second, now=262) is None


def test_refresh_invalid_cases(db):
    grant = db.create_grant("c1", "wp:read", RESOURCE, now=1)
    token = db.issue_refresh(grant.grant_id, ttl=100, now=100)
    assert _rotate(db, "unknown", now=150) == ("invalid", None, None)
    assert _rotate(db, token, now=150, client_id="other") == ("invalid", None, None)
    assert _rotate(db, token, now=200) == ("invalid", None, None)  # expired
    assert db.peek_refresh(token, now=200) is None
    assert db.peek_refresh("unknown") is None
    # The wrong client and the expiry check consumed nothing.
    assert _rotate(db, token, now=150)[0] == "ok"

    orphan = db.issue_refresh("missing-grant", ttl=100, now=100)
    assert _rotate(db, orphan, now=150) == ("invalid", None, None)


def test_failures_are_counted_per_key_and_window(db):
    for at in (100, 200, 300):
        db.record_failure("ip:1.2.3.4", now=at)
    db.record_failure("ip:5.6.7.8", now=300)
    assert db.failures_since("ip:1.2.3.4", 150) == 2
    assert db.failures_since("ip:1.2.3.4", 300) == 0
    assert db.failures_since("ip:9.9.9.9", 0) == 0
    assert db.failures_total_since(150) == 3


def test_prune(db):
    grant = db.create_grant("c1", "wp:read", RESOURCE, now=1)
    old_code = _code(db, now=0, ttl=10)
    new_code = _code(db, now=899_990, ttl=120)
    old_refresh = db.issue_refresh(grant.grant_id, ttl=10, now=0)
    new_refresh = db.issue_refresh(grant.grant_id, ttl=1_000_000, now=0)
    db.record_failure("k", now=0)
    db.record_failure("k", now=900_000)
    db.prune(now=900_000)
    assert db.get_code(old_code) is None and db.get_code(new_code) is not None
    assert db.peek_refresh(old_refresh, now=5) is None
    assert db.peek_refresh(new_refresh, now=5) is not None
    assert db.failures_total_since(-1) == 1


def test_transaction_rolls_back_on_error(db):
    db.add_client("A", ["https://a.example/cb"], client_id="dup")
    with pytest.raises(Exception, match="UNIQUE"):
        db.add_client("B", ["https://b.example/cb"], client_id="dup")
    assert [c.name for c in db.list_clients()] == ["A"]
    # The connection is usable afterwards (no transaction left open).
    db.add_client("C", ["https://c.example/cb"])


def test_grant_ids_never_look_like_cli_options(monkeypatch):
    from wpmcp.auth import store as store_module

    values = iter(["-starts-with-dash", "fine"])
    monkeypatch.setattr(store_module.secrets, "token_urlsafe", lambda _n: next(values))
    assert store_module._grant_id() == "fine"
