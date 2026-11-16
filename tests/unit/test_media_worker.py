from __future__ import annotations

import asyncio
import io
import json
import os
import tempfile
import threading

import httpx
import pytest
from PIL import Image

from tests.conftest import make_settings
from wpmcp.media import worker
from wpmcp.media.images import ImageError
from wpmcp.media.worker import MediaProcessor, serve


def png(width=12, height=8) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "orange").save(out, "PNG")
    return out.getvalue()


def ask(request: bytes, **kwargs) -> tuple[dict, bytes]:
    stdout = io.BytesIO()
    assert serve(io.BytesIO(request), stdout, **kwargs) == 0
    header, _, body = stdout.getvalue().partition(b"\n")
    return json.loads(header), body


def sanitize_request(data: bytes, length: int | None = None) -> bytes:
    header = {"op": "sanitize", "length": len(data) if length is None else length}
    return json.dumps(header).encode() + b"\n" + data


def test_worker_sanitizes_an_image():
    header, body = ask(sanitize_request(png()))
    assert header == {"ok": True, "format": "PNG", "width": 12, "height": 8, "length": len(body)}
    assert Image.open(io.BytesIO(body)).size == (12, 8)


@pytest.mark.parametrize(
    ("request_bytes", "message"),
    [
        (b'{"op": "sanitize", "length": 99}\nshort', "incomplete"),
        (b'{"op": "sanitize", "length": 0}\n', "too large or empty"),
        (b'{"op": "sanitize", "length": 999999999}\n', "too large or empty"),
        (b'{"op": "sanitize", "length": "5"}\n', "too large or empty"),
        (b'{"op": "delete_everything"}\n', "unknown operation"),
        (b'{"op": "fetch", "url": 5}\n', "bad download_url"),
        (b"not json\n", "bad request"),
        (b"[1, 2]\n", "bad request"),
        (b'{"op": "sanitize"', "bad request"),
        (b"x" * 9000 + b"\n", "bad request"),
        (sanitize_request(b"GIF89a....."), "only PNG, JPEG or WebP"),
    ],
)
def test_worker_refuses_bad_requests(request_bytes, message):
    header, body = ask(request_bytes)
    assert header["ok"] is False and message in header["error"]
    assert body == b""


def test_worker_fetches_from_allowed_hosts_only():
    async def public(_host):
        return ["20.60.1.1"]

    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=png()))
    good = b'{"op": "fetch", "url": "https://files.oaiusercontent.com/f"}\n'
    header, _ = ask(good, transport=transport, resolver=public)
    assert header["ok"] is True and header["format"] == "PNG"
    bad = b'{"op": "fetch", "url": "https://169.254.169.254/latest/meta-data"}\n'
    header, _ = ask(bad, transport=transport, resolver=public)
    assert header["ok"] is False and "not allowed" in header["error"]


def test_worker_hides_unexpected_errors(monkeypatch):
    def boom(_data):
        raise RuntimeError("internal detail")

    monkeypatch.setattr(worker, "sanitize", boom)
    header, _ = ask(sanitize_request(png()))
    assert header == {"ok": False, "error": worker.GENERIC_ERROR}


# --- the service side, against a real Unix socket (as systemd provides it)


@pytest.fixture
def socket_path():
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "media.sock")
    yield path
    if os.path.exists(path):
        os.unlink(path)
    os.rmdir(directory)


def run_with_worker(path, coro_factory, handler):
    """Serve each connection with ``handler`` (one per request, like Accept=yes), run the client."""

    async def main():
        server = await asyncio.start_unix_server(handler, path=path)
        async with server:
            return await coro_factory()

    return asyncio.run(main())


async def real_worker(reader, writer):
    """What systemd runs: the worker reading the request from the connection."""
    line = await reader.readuntil(b"\n")
    length = json.loads(line).get("length", 0)
    body = await reader.readexactly(length) if length else b""
    stdout = io.BytesIO()
    await asyncio.to_thread(serve, io.BytesIO(line + body), stdout)
    writer.write(stdout.getvalue())
    await writer.drain()
    writer.close()


def processor(path) -> MediaProcessor:
    return MediaProcessor(make_settings(media_socket=path))


def test_processor_round_trip_through_the_socket(socket_path):
    media = processor(socket_path)
    clean = run_with_worker(socket_path, lambda: media.sanitize(png(30, 20)), real_worker)
    assert (clean.format, clean.width, clean.height) == ("PNG", 30, 20)


def test_processor_reports_the_worker_error(socket_path):
    media = processor(socket_path)

    async def handler(reader, writer):
        await reader.readuntil(b"\n")
        writer.write(b'{"ok": false, "error": "image has too many pixels"}\n')
        await writer.drain()
        writer.close()

    with pytest.raises(ImageError, match="too many pixels"):
        run_with_worker(
            socket_path, lambda: media.fetch("https://files.oaiusercontent.com/f"), handler
        )


@pytest.mark.parametrize(
    "answer",
    [
        b'{"ok": true, "format": "GIF", "width": 1, "height": 1, "length": 3}\nabc',
        b'{"ok": true, "format": "PNG", "width": 1, "height": 1, "length": 0}\n',
        b'{"ok": true, "format": "PNG", "width": 1, "height": 1, "length": 10}\nabc',
        b"garbage without newline",
    ],
)
def test_processor_rejects_malformed_answers(socket_path, answer):
    media = processor(socket_path)

    async def handler(reader, writer):
        await reader.readuntil(b"\n")
        writer.write(answer)
        await writer.drain()
        writer.close()

    with pytest.raises(ImageError, match="could not be processed"):
        run_with_worker(socket_path, lambda: media.sanitize(png()), handler)


def test_processor_without_a_worker_fails_closed(socket_path):
    with pytest.raises(ImageError, match="could not be processed"):
        asyncio.run(processor(socket_path).sanitize(png()))


def test_processor_checks_size_before_sending(socket_path):
    media = MediaProcessor(make_settings(media_socket=socket_path, media_max_bytes=2048))
    with pytest.raises(ImageError, match="empty"):
        asyncio.run(media.sanitize(b""))
    with pytest.raises(ImageError, match="too large"):
        asyncio.run(media.sanitize(b"x" * 4096))


def test_processor_in_process_mode():
    async def public(_host):
        return ["20.60.1.1"]

    transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=png(5, 5)))
    media = MediaProcessor(make_settings(), transport=transport, resolver=public)
    assert asyncio.run(media.sanitize(png())).format == "PNG"
    fetched = asyncio.run(media.fetch("https://files.oaiusercontent.com/f"))
    assert (fetched.width, fetched.height) == (5, 5)


def test_serve_works_on_real_pipes():
    """As under systemd: the connection is a file descriptor, read with buffered I/O."""
    read_fd, write_fd = os.pipe()
    out_read, out_write = os.pipe()
    request = sanitize_request(png())

    def feed():
        with os.fdopen(write_fd, "wb") as pipe:
            pipe.write(request)

    thread = threading.Thread(target=feed)
    thread.start()
    with os.fdopen(read_fd, "rb") as stdin, os.fdopen(out_write, "wb") as stdout:
        serve(stdin, stdout)
    thread.join()
    with os.fdopen(out_read, "rb") as result:
        assert json.loads(result.readline())["ok"] is True
