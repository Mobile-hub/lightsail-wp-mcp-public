"""SQLite state of the authorization server.

Holds what must survive a restart or be revocable one by one: the owner credentials,
the pre-registered clients, authorization codes, grants (one per approved connection),
refresh tokens and failed consent attempts. Secrets are stored hashed: codes, refresh
tokens and client secrets are random 256-bit values, so a plain SHA-256 is enough.

Every access token carries the database id (``db_id``). If this file is lost or
replaced, all outstanding tokens stop verifying instead of being silently un-revoked.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS owner (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  password_hash TEXT NOT NULL,
  totp_secret TEXT NOT NULL,
  totp_last_step INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS clients (
  client_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  secret_hash TEXT NOT NULL,
  redirect_uris TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  disabled_at INTEGER
);
CREATE TABLE IF NOT EXISTS grants (
  grant_id TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  resource TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  last_used_at INTEGER,
  revoked_at INTEGER,
  revoked_reason TEXT
);
CREATE TABLE IF NOT EXISTS codes (
  code_hash TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  redirect_uri TEXT NOT NULL,
  code_challenge TEXT NOT NULL,
  scope TEXT NOT NULL,
  resource TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at INTEGER,
  grant_id TEXT
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
  token_hash TEXT PRIMARY KEY,
  grant_id TEXT NOT NULL,
  issued_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  rotated_at INTEGER
);
CREATE TABLE IF NOT EXISTS failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT NOT NULL,
  at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS upload_links (
  token_hash TEXT PRIMARY KEY,
  grant_id TEXT NOT NULL,
  params TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  claimed_at INTEGER,
  used_at INTEGER,
  media_id INTEGER
);
CREATE INDEX IF NOT EXISTS failures_key_at ON failures (key, at);
CREATE INDEX IF NOT EXISTS refresh_grant ON refresh_tokens (grant_id);
"""

# Client used by ``wpmcp token`` (no browser flow, no secret anyone knows).
CLI_CLIENT_ID = "cli"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Owner:
    password_hash: str
    totp_secret: str
    totp_last_step: int


@dataclass(frozen=True)
class Client:
    client_id: str
    name: str
    redirect_uris: tuple[str, ...]
    created_at: int
    disabled_at: int | None

    @property
    def enabled(self) -> bool:
        return self.disabled_at is None


@dataclass(frozen=True)
class Grant:
    grant_id: str
    client_id: str
    scope: str
    resource: str
    created_at: int
    last_used_at: int | None
    revoked_at: int | None
    revoked_reason: str | None

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True)
class UploadLink:
    grant_id: str
    params: dict
    expires_at: int


@dataclass(frozen=True)
class Code:
    client_id: str
    redirect_uri: str
    code_challenge: str
    scope: str
    resource: str
    expires_at: int
    used_at: int | None
    grant_id: str | None


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        if path != ":memory:":
            # Create the file private before SQLite touches it (WAL/SHM files inherit the mode).
            os.close(os.open(path, os.O_RDWR | os.O_CREAT, 0o600))
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._db.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('db_id', ?)",
                (secrets.token_urlsafe(16),),
            )
            self.db_id: str = self._db.execute(
                "SELECT value FROM meta WHERE key = 'db_id'"
            ).fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextlib.contextmanager
    def _tx(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    # --------------------------------------------------------------------- owner
    def set_owner(self, password_hash: str, totp_secret: str, now: float | None = None) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO owner (id, password_hash, totp_secret, totp_last_step, updated_at) "
                "VALUES (1, ?, ?, 0, ?) ON CONFLICT (id) DO UPDATE SET "
                "password_hash = excluded.password_hash, totp_secret = excluded.totp_secret, "
                "totp_last_step = 0, updated_at = excluded.updated_at",
                (password_hash, totp_secret, int(now if now is not None else time.time())),
            )

    def get_owner(self) -> Owner | None:
        with self._lock:
            row = self._db.execute(
                "SELECT password_hash, totp_secret, totp_last_step FROM owner WHERE id = 1"
            ).fetchone()
        return Owner(*row) if row else None

    def accept_totp_step(self, step: int) -> bool:
        """Record a used TOTP time step; False if this step (or a later one) was already used."""
        with self._tx() as db:
            cur = db.execute(
                "UPDATE owner SET totp_last_step = ? WHERE id = 1 AND totp_last_step < ?",
                (step, step),
            )
            return cur.rowcount == 1

    # ------------------------------------------------------------------- clients
    def add_client(
        self,
        name: str,
        redirect_uris: list[str] | tuple[str, ...],
        *,
        client_id: str | None = None,
        now: float | None = None,
    ) -> tuple[Client, str]:
        """Register a confidential client. Returns it with its secret, shown only once."""
        client_id = client_id or f"wpmcp-{secrets.token_urlsafe(12)}"
        secret = secrets.token_urlsafe(32)
        created = int(now if now is not None else time.time())
        with self._tx() as db:
            db.execute(
                "INSERT INTO clients (client_id, name, secret_hash, redirect_uris, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (client_id, name, _hash(secret), json.dumps(list(redirect_uris)), created),
            )
        return Client(client_id, name, tuple(redirect_uris), created, None), secret

    def ensure_cli_client(self) -> Client:
        client = self.get_client(CLI_CLIENT_ID)
        if client is None:
            client, _secret = self.add_client("wpmcp token (CLI)", [], client_id=CLI_CLIENT_ID)
        return client

    def get_client(self, client_id: str | None) -> Client | None:
        if not client_id:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT client_id, name, redirect_uris, created_at, disabled_at "
                "FROM clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
        return self._client(row) if row else None

    def list_clients(self) -> list[Client]:
        with self._lock:
            rows = self._db.execute(
                "SELECT client_id, name, redirect_uris, created_at, disabled_at "
                "FROM clients ORDER BY created_at, client_id"
            ).fetchall()
        return [self._client(row) for row in rows]

    @staticmethod
    def _client(row: sqlite3.Row) -> Client:
        return Client(
            row["client_id"],
            row["name"],
            tuple(json.loads(row["redirect_uris"])),
            row["created_at"],
            row["disabled_at"],
        )

    def verify_client_secret(self, client_id: str, secret: str | None) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT secret_hash FROM clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        if row is None or not secret:
            return False
        return hmac.compare_digest(row["secret_hash"], _hash(secret))

    def disable_client(self, client_id: str, now: float | None = None) -> bool:
        """Disable a client and revoke its grants."""
        at = int(now if now is not None else time.time())
        with self._tx() as db:
            cur = db.execute(
                "UPDATE clients SET disabled_at = ? WHERE client_id = ? AND disabled_at IS NULL",
                (at, client_id),
            )
            db.execute(
                "UPDATE grants SET revoked_at = ?, revoked_reason = 'client disabled' "
                "WHERE client_id = ? AND revoked_at IS NULL",
                (at, client_id),
            )
            return cur.rowcount == 1

    # --------------------------------------------------------------------- codes
    def create_code(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        scope: str,
        resource: str,
        ttl: int,
        now: float | None = None,
    ) -> str:
        code = secrets.token_urlsafe(32)
        expires = int(now if now is not None else time.time()) + ttl
        with self._tx() as db:
            db.execute(
                "INSERT INTO codes (code_hash, client_id, redirect_uri, code_challenge, scope, "
                "resource, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_hash(code), client_id, redirect_uri, code_challenge, scope, resource, expires),
            )
        return code

    def get_code(self, code: str) -> Code | None:
        with self._lock:
            row = self._db.execute(
                "SELECT client_id, redirect_uri, code_challenge, scope, resource, expires_at, "
                "used_at, grant_id FROM codes WHERE code_hash = ?",
                (_hash(code),),
            ).fetchone()
        return Code(*row) if row else None

    def redeem_code(self, code: str, now: float | None = None) -> Grant | None:
        """Mark the code used and create its grant, atomically. None if it was already used."""
        at = int(now if now is not None else time.time())
        with self._tx() as db:
            row = db.execute(
                "SELECT client_id, scope, resource FROM codes "
                "WHERE code_hash = ? AND used_at IS NULL",
                (_hash(code),),
            ).fetchone()
            if row is None:
                return None
            grant_id = secrets.token_urlsafe(12)
            db.execute(
                "UPDATE codes SET used_at = ?, grant_id = ? WHERE code_hash = ?",
                (at, grant_id, _hash(code)),
            )
            db.execute(
                "INSERT INTO grants (grant_id, client_id, scope, resource, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (grant_id, row["client_id"], row["scope"], row["resource"], at),
            )
        return Grant(
            grant_id, row["client_id"], row["scope"], row["resource"], at, None, None, None
        )

    # -------------------------------------------------------------------- grants
    def create_grant(
        self, client_id: str, scope: str, resource: str, now: float | None = None
    ) -> Grant:
        at = int(now if now is not None else time.time())
        grant_id = secrets.token_urlsafe(12)
        with self._tx() as db:
            db.execute(
                "INSERT INTO grants (grant_id, client_id, scope, resource, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (grant_id, client_id, scope, resource, at),
            )
        return Grant(grant_id, client_id, scope, resource, at, None, None, None)

    def get_grant(self, grant_id: str | None) -> Grant | None:
        if not grant_id:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT grant_id, client_id, scope, resource, created_at, last_used_at, "
                "revoked_at, revoked_reason FROM grants WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
        return Grant(*row) if row else None

    def list_grants(self, include_revoked: bool = False) -> list[Grant]:
        where = "" if include_revoked else "WHERE revoked_at IS NULL"
        with self._lock:
            rows = self._db.execute(
                "SELECT grant_id, client_id, scope, resource, created_at, last_used_at, "  # noqa: S608
                f"revoked_at, revoked_reason FROM grants {where} ORDER BY created_at, grant_id"
            ).fetchall()
        return [Grant(*row) for row in rows]

    def revoke_grant(self, grant_id: str, reason: str, now: float | None = None) -> bool:
        at = int(now if now is not None else time.time())
        with self._tx() as db:
            cur = db.execute(
                "UPDATE grants SET revoked_at = ?, revoked_reason = ? "
                "WHERE grant_id = ? AND revoked_at IS NULL",
                (at, reason, grant_id),
            )
            return cur.rowcount == 1

    def revoke_all(self, reason: str, now: float | None = None) -> int:
        at = int(now if now is not None else time.time())
        with self._tx() as db:
            cur = db.execute(
                "UPDATE grants SET revoked_at = ?, revoked_reason = ? WHERE revoked_at IS NULL",
                (at, reason),
            )
            return cur.rowcount

    # ------------------------------------------------------------ refresh tokens
    def issue_refresh(self, grant_id: str, ttl: int, now: float | None = None) -> str:
        at = int(now if now is not None else time.time())
        token = secrets.token_urlsafe(32)
        with self._tx() as db:
            db.execute(
                "INSERT INTO refresh_tokens (token_hash, grant_id, issued_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (_hash(token), grant_id, at, at + ttl),
            )
        return token

    def peek_refresh(self, token: str, now: float | None = None) -> Grant | None:
        """The active grant behind an unexpired refresh token, without consuming it."""
        at = int(now if now is not None else time.time())
        with self._lock:
            row = self._db.execute(
                "SELECT grant_id FROM refresh_tokens WHERE token_hash = ? AND expires_at > ?",
                (_hash(token), at),
            ).fetchone()
        grant = self.get_grant(row["grant_id"]) if row else None
        return grant if grant and grant.active else None

    def rotate_refresh(
        self, token: str, *, client_id: str, ttl: int, grace: int, now: float | None = None
    ) -> tuple[str, Grant | None, str | None]:
        """Exchange a refresh token for a new one.

        Returns ``("ok", grant, new_token)``, ``("invalid", None, None)`` or
        ``("reused", grant, None)``. Presenting a token that was already rotated, after
        the grace window, means it leaked: the whole grant is revoked.
        """
        at = int(now if now is not None else time.time())
        with self._tx() as db:
            row = db.execute(
                "SELECT grant_id, expires_at, rotated_at FROM refresh_tokens WHERE token_hash = ?",
                (_hash(token),),
            ).fetchone()
            if row is None or row["expires_at"] <= at:
                return "invalid", None, None
            grant_row = db.execute(
                "SELECT grant_id, client_id, scope, resource, created_at, last_used_at, "
                "revoked_at, revoked_reason FROM grants WHERE grant_id = ?",
                (row["grant_id"],),
            ).fetchone()
            if grant_row is None:
                return "invalid", None, None
            grant = Grant(*grant_row)
            if not grant.active or grant.client_id != client_id:
                return "invalid", None, None
            if row["rotated_at"] is not None and at - row["rotated_at"] > grace:
                db.execute(
                    "UPDATE grants SET revoked_at = ?, revoked_reason = 'refresh token reuse' "
                    "WHERE grant_id = ?",
                    (at, grant.grant_id),
                )
                return "reused", grant, None
            if row["rotated_at"] is None:
                db.execute(
                    "UPDATE refresh_tokens SET rotated_at = ? WHERE token_hash = ?",
                    (at, _hash(token)),
                )
            new_token = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO refresh_tokens (token_hash, grant_id, issued_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (_hash(new_token), grant.grant_id, at, at + ttl),
            )
            db.execute(
                "UPDATE grants SET last_used_at = ? WHERE grant_id = ?", (at, grant.grant_id)
            )
        return "ok", grant, new_token

    # -------------------------------------------------------------- upload links
    # A claim older than this is treated as abandoned (a crashed or very slow upload).
    UPLOAD_CLAIM_TIMEOUT = 300

    def create_upload_link(
        self, grant_id: str, params: dict, ttl: int, now: float | None = None
    ) -> str:
        """A single-use upload token; only its hash is stored."""
        at = int(now if now is not None else time.time())
        token = secrets.token_urlsafe(32)
        with self._tx() as db:
            db.execute(
                "INSERT INTO upload_links (token_hash, grant_id, params, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (_hash(token), grant_id, json.dumps(params), at, at + ttl),
            )
        return token

    def get_upload_link(self, token: str, now: float | None = None) -> UploadLink | None:
        """The unexpired, unused link behind ``token`` whose grant is still active."""
        at = int(now if now is not None else time.time())
        with self._lock:
            row = self._db.execute(
                "SELECT grant_id, params, expires_at FROM upload_links "
                "WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
                (_hash(token), at),
            ).fetchone()
        if row is None:
            return None
        grant = self.get_grant(row["grant_id"])
        if grant is None or not grant.active:
            return None
        return UploadLink(row["grant_id"], json.loads(row["params"]), row["expires_at"])

    def claim_upload_link(self, token: str, now: float | None = None) -> bool:
        """Reserve the link for one upload in progress; False if used, expired or busy."""
        at = int(now if now is not None else time.time())
        with self._tx() as db:
            cur = db.execute(
                "UPDATE upload_links SET claimed_at = ? WHERE token_hash = ? AND used_at IS NULL "
                "AND expires_at > ? AND (claimed_at IS NULL OR claimed_at < ?)",
                (at, _hash(token), at, at - self.UPLOAD_CLAIM_TIMEOUT),
            )
            return cur.rowcount == 1

    def release_upload_link(self, token: str) -> None:
        """The upload failed (a bad file, say): the link may be used again."""
        with self._tx() as db:
            db.execute(
                "UPDATE upload_links SET claimed_at = NULL "
                "WHERE token_hash = ? AND used_at IS NULL",
                (_hash(token),),
            )

    def finish_upload_link(self, token: str, media_id: int, now: float | None = None) -> None:
        at = int(now if now is not None else time.time())
        with self._tx() as db:
            db.execute(
                "UPDATE upload_links SET used_at = ?, media_id = ? WHERE token_hash = ?",
                (at, media_id, _hash(token)),
            )

    # ------------------------------------------------------------------ failures
    def record_failure(self, key: str, now: float | None = None) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO failures (key, at) VALUES (?, ?)",
                (key, int(now if now is not None else time.time())),
            )

    def failures_since(self, key: str, since: float) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM failures WHERE key = ? AND at > ?", (key, int(since))
            ).fetchone()[0]

    def failures_total_since(self, since: float) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM failures WHERE at > ?", (int(since),)
            ).fetchone()[0]

    # --------------------------------------------------------------- maintenance
    def prune(self, now: float | None = None) -> None:
        """Drop rows that can no longer matter (expired codes and tokens, old failures)."""
        at = int(now if now is not None else time.time())
        with self._tx() as db:
            db.execute("DELETE FROM codes WHERE expires_at < ?", (at - 86400,))
            db.execute("DELETE FROM refresh_tokens WHERE expires_at < ?", (at,))
            db.execute("DELETE FROM failures WHERE at < ?", (at - 7 * 86400,))
            db.execute("DELETE FROM upload_links WHERE expires_at < ?", (at - 86400,))
