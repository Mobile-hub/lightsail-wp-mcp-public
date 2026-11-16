"""Owner credentials: scrypt password hash and TOTP second factor (RFC 6238), stdlib only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

# OWASP scrypt profile (N=2^15, r=8, p=3). Verification runs in a worker thread.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 3
_SCRYPT_LEN = 32
MIN_PASSWORD_LENGTH = 12

TOTP_PERIOD = 30
TOTP_DIGITS = 6


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=_SCRYPT_LEN,
        maxmem=256 * r * (n + p + 2),
    )


def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, n, r, p)
    encode = base64.b64encode
    return f"scrypt${n}${r}${p}${encode(salt).decode()}${encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def totp_at(secret: str, step: int) -> str:
    """The code for one 30-second time step (HOTP with SHA-1, as authenticator apps expect)."""
    key = base64.b32decode(secret, casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def match_totp(secret: str, code: str, now: float, window: int = 1) -> int | None:
    """The time step ``code`` is valid for (current step ± ``window``), or None."""
    code = "".join(code.split())
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    current = int(now) // TOTP_PERIOD
    matched = None
    for delta in range(-window, window + 1):
        step = current + delta
        if step > 0 and hmac.compare_digest(totp_at(secret, step), code):
            matched = step
    return matched


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    """otpauth:// URI to enrol the secret in an authenticator app."""
    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_PERIOD}"
    )
