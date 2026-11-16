import pytest

from wpmcp.auth.tokens import Signer, TokenError, b64url_decode, b64url_encode, pkce_s256


def test_roundtrip_and_typ():
    signer = Signer("k" * 40)
    token = signer.issue("access", 60, sub="owner")
    claims = signer.decode(token, typ="access")
    assert claims["sub"] == "owner" and claims["typ"] == "access"
    with pytest.raises(TokenError, match="expected a refresh"):
        signer.decode(token, typ="refresh")


def test_forged_signature_rejected():
    token = Signer("a" * 40).issue("access", 60)
    with pytest.raises(TokenError, match="bad signature"):
        Signer("b" * 40).decode(token)


def test_tampered_payload_rejected():
    signer = Signer("a" * 40)
    header, _, sig = signer.issue("access", 60, scope="mcp").split(".")
    payload = b64url_encode(b'{"typ":"access","scope":"admin"}')
    with pytest.raises(TokenError):
        signer.decode(f"{header}.{payload}.{sig}")


def test_expiry():
    signer = Signer("a" * 40)
    token = signer.issue("code", 10, now=1000)
    assert signer.decode(token, now=1009)
    with pytest.raises(TokenError, match="expired"):
        signer.decode(token, now=1010)


def test_no_expiry_when_ttl_none():
    signer = Signer("a" * 40)
    assert "exp" not in signer.decode(signer.issue("client", None))


@pytest.mark.parametrize("bad", ["", "a.b", "a.b.c.d", "!!.??.**", None])
def test_malformed(bad):
    with pytest.raises(TokenError):
        Signer("a" * 40).decode(bad)


def test_none_alg_rejected():
    signer = Signer("a" * 40)
    header = b64url_encode(b'{"alg":"none"}')
    payload = b64url_encode(b'{"typ":"access"}')
    sig = signer._sign(f"{header}.{payload}".encode())
    with pytest.raises(TokenError, match="unsupported"):
        signer.decode(f"{header}.{payload}.{sig}")


def test_pkce_rfc7636_vector():
    # RFC 7636 Appendix B
    assert (
        pkce_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_b64url_roundtrip():
    assert b64url_decode(b64url_encode(b"\x00\xffhello")) == b"\x00\xffhello"


def test_derive_is_deterministic_and_keyed():
    assert Signer("a" * 40).derive("l", "v") == Signer("a" * 40).derive("l", "v")
    assert Signer("a" * 40).derive("l", "v") != Signer("b" * 40).derive("l", "v")
