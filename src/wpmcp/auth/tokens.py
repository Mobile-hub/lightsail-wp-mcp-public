"""Stateless signed tokens (compact JWS, HS256) built on the standard library.

Every artefact the gateway hands out — client ids, authorization codes, access and
refresh tokens — is a signed token with a ``typ`` claim, so the gateway needs no
database. Rotating ``GATEWAY_SECRET`` revokes everything at once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

_HEADER = {"alg": "HS256", "typ": "JWT"}


class TokenError(Exception):
    """Raised when a token is malformed, forged, expired or of the wrong type."""


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding)
    except (ValueError, TypeError) as exc:
        raise TokenError("invalid base64url") from exc


def _json(obj: Any) -> bytes:
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()


class Signer:
    def __init__(self, secret: str | bytes) -> None:
        if isinstance(secret, str):
            secret = secret.encode()
        self._key = secret

    def _sign(self, signing_input: bytes) -> str:
        return b64url_encode(hmac.new(self._key, signing_input, hashlib.sha256).digest())

    def encode(self, claims: dict[str, Any]) -> str:
        signing_input = f"{b64url_encode(_json(_HEADER))}.{b64url_encode(_json(claims))}"
        return f"{signing_input}.{self._sign(signing_input.encode())}"

    def decode(self, token: str, *, typ: str | None = None, now: float | None = None) -> dict:
        parts = token.split(".") if isinstance(token, str) else []
        if len(parts) != 3:
            raise TokenError("malformed token")
        header_b64, payload_b64, signature = parts
        expected = self._sign(f"{header_b64}.{payload_b64}".encode())
        if not hmac.compare_digest(expected, signature):
            raise TokenError("bad signature")
        try:
            header = json.loads(b64url_decode(header_b64))
            claims = json.loads(b64url_decode(payload_b64))
        except ValueError as exc:
            raise TokenError("malformed token") from exc
        if header.get("alg") != "HS256" or not isinstance(claims, dict):
            raise TokenError("unsupported token")
        exp = claims.get("exp")
        if exp is not None and (now if now is not None else time.time()) >= exp:
            raise TokenError("token expired")
        if typ is not None and claims.get("typ") != typ:
            raise TokenError(f"expected a {typ} token")
        return claims

    def issue(self, typ: str, ttl: int | None, *, now: float | None = None, **claims: Any) -> str:
        issued_at = int(now if now is not None else time.time())
        body = {k: v for k, v in claims.items() if v is not None}
        body.update(typ=typ, iat=issued_at, jti=secrets.token_urlsafe(12))
        if ttl is not None:
            body["exp"] = issued_at + ttl
        return self.encode(body)

    def derive(self, label: str, value: str) -> str:
        """Deterministic secret bound to ``value`` (used for confidential client secrets)."""
        return b64url_encode(
            hmac.new(self._key, f"{label}:{value}".encode(), hashlib.sha256).digest()
        )


def pkce_s256(verifier: str) -> str:
    return b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())
