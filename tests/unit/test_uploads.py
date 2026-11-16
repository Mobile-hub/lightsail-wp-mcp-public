from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image
from starlette.testclient import TestClient

from tests.conftest import BASE, access_token, make_settings
from tests.unit.test_write_tools import FakeWordPress
from wpmcp.app import create_app
from wpmcp.media.worker import MediaProcessor
from wpmcp.wp.client import WordPressClient


def jpeg_with_location() -> bytes:
    image = Image.new("RGB", (60, 40), "navy")
    exif = Image.Exif()
    exif.get_ifd(0x8825).update({1: "N", 2: (40.0, 25.0, 0.0)})
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


@pytest.fixture
def fake():
    wp = FakeWordPress()
    wp.responses["/media"] = httpx.Response(200, json={"id": 777, "width": 60, "height": 40})
    return wp


@pytest.fixture
def app(store, clock, fake):
    settings = make_settings(
        wp_url="https://books.example.test", wp_password="pw", media_max_bytes=64 * 1024
    )
    wp = WordPressClient(settings, transport=httpx.MockTransport(fake))
    return create_app(settings, store=store, clock=clock, wp=wp, media=MediaProcessor(settings))


@pytest.fixture
def client(app):
    with TestClient(app, base_url=BASE) as test_client:
        yield test_client


def new_link(app, clock, scope="wp:read wp:write", **params):
    _, grant = access_token(app, clock, scope=scope)
    store = app.state.store
    token = store.create_upload_link(grant.grant_id, params, ttl=900, now=clock())
    return f"/upload/{token}", grant


def send(client, url, data, name="photo.jpg"):
    return client.post(url, files={"file": (name, data, "image/jpeg")})


def test_the_form_is_shown_without_storing_anything(client, app, clock, fake):
    url, _ = new_link(app, clock, title="Capa PT")
    page = client.get(url)
    assert page.status_code == 200
    assert "Capa PT" in page.text and "enctype=multipart/form-data" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert fake.requests == []


def test_upload_reaches_wordpress_re_encoded(client, app, clock, fake):
    url, _ = new_link(app, clock, title="Capa PT", alt="Capa", attach_to=8)
    page = send(client, url, jpeg_with_location())
    assert page.status_code == 200, page.text
    assert "Image uploaded" in page.text and "777" in page.text
    upload = fake.requests[-1]
    assert upload.url.path.endswith("/media")
    assert b'filename="capa-pt.jpg"' in upload.content
    assert b"Exif" not in upload.content  # the location went with the metadata
    assert b'name="attach_to"\r\n\r\n8' in upload.content


def test_a_link_works_once(client, app, clock):
    url, _ = new_link(app, clock)
    assert send(client, url, jpeg_with_location()).status_code == 200
    assert client.get(url).status_code == 410
    assert send(client, url, jpeg_with_location()).status_code == 410


def test_a_rejected_file_leaves_the_link_usable(client, app, clock, fake):
    url, _ = new_link(app, clock)
    refused = send(client, url, b"GIF89a not an image", name="x.gif")
    assert refused.status_code == 400 and "only PNG, JPEG or WebP" in refused.text
    assert fake.requests == []
    assert send(client, url, jpeg_with_location()).status_code == 200


def test_wordpress_errors_are_shown_and_the_link_kept(client, app, clock, fake):
    fake.responses["/media"] = httpx.Response(
        400, json={"code": "wpmcp_upload_failed", "message": "Sorry, not allowed."}
    )
    url, _ = new_link(app, clock)
    page = send(client, url, jpeg_with_location())
    assert page.status_code == 400 and "Sorry, not allowed." in page.text
    assert client.get(url).status_code == 200


def test_missing_and_oversized_files(client, app, clock):
    url, _ = new_link(app, clock)
    assert client.post(url, data={"other": "x"}).status_code == 400
    assert send(client, url, b"\xff\xd8\xff" + b"0" * (70 * 1024)).status_code in (400, 413)


@pytest.mark.parametrize("case", ["unknown", "expired", "revoked", "read_only"])
def test_invalid_links_are_gone(client, app, clock, case):
    if case == "unknown":
        url = "/upload/not-a-real-token"
    else:
        url, grant = new_link(
            app, clock, scope="wp:read" if case == "read_only" else "wp:read wp:write"
        )
        if case == "expired":
            clock.advance(901)
        if case == "revoked":
            app.state.store.revoke_grant(grant.grant_id, "test")
    assert client.get(url).status_code == 410
    assert send(client, url, jpeg_with_location()).status_code == 410


def test_a_link_in_use_cannot_be_used_twice_at_once(app, clock):
    url, _ = new_link(app, clock)
    token = url.rsplit("/", 1)[1]
    store = app.state.store
    assert store.claim_upload_link(token, now=clock()) is True
    assert store.claim_upload_link(token, now=clock()) is False
    # An abandoned claim expires.
    assert store.claim_upload_link(token, now=clock() + store.UPLOAD_CLAIM_TIMEOUT + 1) is True


def test_upload_links_are_rate_limited(client):
    statuses = {client.get("/upload/nothing").status_code for _ in range(25)}
    assert statuses == {410, 429}


def test_upload_links_exist_only_with_wordpress(store, clock):
    app = create_app(make_settings(), store=store, clock=clock)
    with TestClient(app, base_url=BASE) as test_client:
        assert test_client.get("/upload/anything").status_code == 404


def test_expired_links_are_pruned(app, clock):
    url, _ = new_link(app, clock)
    store = app.state.store
    store.prune(now=clock() + 2 * 86400)
    assert store.get_upload_link(url.rsplit("/", 1)[1], now=clock()) is None
