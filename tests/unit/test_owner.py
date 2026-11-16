from urllib.parse import parse_qs, urlsplit

import pytest

from wpmcp.auth.owner import (
    hash_password,
    match_totp,
    new_totp_secret,
    provisioning_uri,
    totp_at,
    verify_password,
)

# RFC 6238 Appendix B: the ASCII secret "12345678901234567890" in base32.
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_password_roundtrip():
    stored = hash_password("correct horse battery staple", n=2**4, r=8, p=1)
    assert stored.startswith("scrypt$16$8$1$")
    assert verify_password("correct horse battery staple", stored)
    assert not verify_password("correct horse battery stapl", stored)
    assert not verify_password("", stored)


def test_password_hashes_are_salted():
    assert hash_password("same", n=2**4) != hash_password("same", n=2**4)


def test_default_cost_profile_works():
    stored = hash_password("a long enough password")
    assert stored.startswith("scrypt$32768$8$3$")
    assert verify_password("a long enough password", stored)


@pytest.mark.parametrize(
    "stored",
    ["", "plain", "md5$1$2$3$4$5", "scrypt$x$8$1$AAAA$AAAA", "scrypt$16$8$1$!!$!!", "a$b$c"],
)
def test_malformed_hash_never_verifies(stored):
    assert not verify_password("anything", stored)


@pytest.mark.parametrize(
    "timestamp,code",
    [(59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037")],
)
def test_totp_rfc6238_vectors(timestamp, code):
    assert totp_at(RFC_SECRET, timestamp // 30) == code
    assert match_totp(RFC_SECRET, code, timestamp) == timestamp // 30


def test_totp_window_is_one_step_each_way():
    now = 1_800_000_000
    step = now // 30
    assert match_totp(RFC_SECRET, totp_at(RFC_SECRET, step - 1), now) == step - 1
    assert match_totp(RFC_SECRET, totp_at(RFC_SECRET, step + 1), now) == step + 1
    assert match_totp(RFC_SECRET, totp_at(RFC_SECRET, step - 2), now) is None
    assert match_totp(RFC_SECRET, totp_at(RFC_SECRET, step + 2), now) is None


def test_totp_input_is_normalised_and_validated():
    now = 1_800_000_000
    code = totp_at(RFC_SECRET, now // 30)
    assert match_totp(RFC_SECRET, f" {code[:3]} {code[3:]} ", now) == now // 30
    for bad in ("", "12345", "1234567", "abcdef", "12 34"):
        assert match_totp(RFC_SECRET, bad, now) is None


def test_new_secret_is_random_base32():
    a, b = new_totp_secret(), new_totp_secret()
    assert a != b and len(a) == 32
    assert len(totp_at(a, 1)) == 6


def test_provisioning_uri():
    uri = provisioning_uri("ABC", "owner", "Example Books & Co")
    parts = urlsplit(uri)
    query = parse_qs(parts.query)
    assert parts.scheme == "otpauth" and parts.netloc == "totp"
    assert parts.path == "/Example%20Books%20%26%20Co%3Aowner"
    assert query["secret"] == ["ABC"] and query["issuer"] == ["Example Books & Co"]
    assert query["digits"] == ["6"] and query["period"] == ["30"]
