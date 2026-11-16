from __future__ import annotations

import io
import zlib

import pytest
from PIL import Image, ImageCms

from wpmcp.media.images import ImageError, sanitize, slugify, sniff

GPS_IFD = 0x8825
ORIENTATION = 0x0112


def encode(image: Image.Image, fmt: str, **options) -> bytes:
    out = io.BytesIO()
    image.save(out, fmt, **options)
    return out.getvalue()


def photo_with_gps(orientation: int = 1) -> bytes:
    """A 'phone photo': EXIF with a GPS position, camera model and an orientation."""
    image = Image.new("RGB", (40, 20), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0110] = "Phone 12"  # camera model
    exif[ORIENTATION] = orientation
    exif.get_ifd(GPS_IFD).update({1: "N", 2: (40.0, 25.0, 0.0), 3: "W", 4: (3.0, 42.0, 0.0)})
    return encode(image, "JPEG", exif=exif)


def opened(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def test_jpeg_loses_its_metadata_and_gps_position():
    clean = sanitize(photo_with_gps())
    out = opened(clean.data)
    assert clean.format == "JPEG" and clean.mime == "image/jpeg" and clean.ext == ".jpg"
    assert dict(out.getexif()) == {}
    assert b"Phone 12" not in clean.data
    assert (clean.width, clean.height) == (40, 20)


def test_orientation_is_applied_to_the_pixels_then_dropped():
    clean = sanitize(photo_with_gps(orientation=6))  # rotate 90°: portrait
    assert (clean.width, clean.height) == (20, 40)
    assert ORIENTATION not in opened(clean.data).getexif()


def test_png_text_chunks_are_removed_and_transparency_kept():
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "secret note")
    image = Image.new("RGBA", (10, 10), (0, 0, 255, 128))
    clean = sanitize(encode(image, "PNG", pnginfo=info))
    out = opened(clean.data)
    assert b"secret note" not in clean.data
    assert out.mode == "RGBA" and out.getpixel((0, 0))[3] == 128


def test_palette_png_with_transparency_becomes_rgba():
    image = Image.new("P", (8, 8), 0)
    image.putpalette([255, 0, 0] * 256)
    clean = sanitize(encode(image, "PNG", transparency=0))
    assert opened(clean.data).mode == "RGBA"


def test_webp_stays_webp():
    clean = sanitize(encode(Image.new("RGB", (16, 16), "green"), "WEBP"))
    assert clean.format == "WEBP" and clean.mime == "image/webp"
    assert opened(clean.data).format == "WEBP"


def test_cmyk_jpeg_becomes_rgb():
    clean = sanitize(encode(Image.new("CMYK", (8, 8), (0, 255, 255, 0)), "JPEG"))
    assert opened(clean.data).mode == "RGB"


def test_colour_profile_is_kept():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    data = encode(Image.new("RGB", (8, 8), "white"), "JPEG", icc_profile=profile)
    assert opened(sanitize(data).data).info.get("icc_profile") == profile


def test_large_images_are_scaled_down():
    clean = sanitize(encode(Image.new("RGB", (3000, 1000), "white"), "PNG"))
    assert (clean.width, clean.height) == (2560, 853)


def test_appended_payload_does_not_survive():
    payload = b"PK\x03\x04" + b"<?php system($_GET['c']); ?>" * 10
    clean = sanitize(encode(Image.new("RGB", (8, 8), "white"), "JPEG") + payload)
    assert b"<?php" not in clean.data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"", "empty"),
        (b"GIF89a" + b"\0" * 20, "only PNG, JPEG or WebP"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "only PNG, JPEG or WebP"),
        (b"\xff\xd8\xff" + b"not really a jpeg" * 4, "not a valid image"),
        (b"\x89PNG\r\n\x1a\n" + b"\0" * 30, "not a valid image"),
    ],
)
def test_rejects_what_is_not_a_valid_image(data, message):
    with pytest.raises(ImageError, match=message):
        sanitize(data)


def test_rejects_files_over_the_size_cap():
    with pytest.raises(ImageError, match="too large"):
        sanitize(encode(Image.new("RGB", (8, 8)), "PNG"), max_bytes=10)


def bomb_png(width: int, height: int) -> bytes:
    """A tiny file whose header claims a huge image (a decompression bomb)."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body).to_bytes(4, "big")
        return len(body).to_bytes(4, "big") + kind + body + crc

    header = width.to_bytes(4, "big") + height.to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    pixels = zlib.compress(b"\0" * 64)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")
    )


def test_rejects_decompression_bombs_from_the_header():
    with pytest.raises(ImageError, match="too many pixels"):
        sanitize(bomb_png(50_000, 50_000))


def test_rejects_images_over_the_pixel_cap():
    with pytest.raises(ImageError, match="too many pixels"):
        sanitize(encode(Image.new("RGB", (100, 100)), "PNG"), max_pixels=5000)


def test_rejects_animated_images():
    frames = [Image.new("RGB", (8, 8), c) for c in ("red", "blue")]
    out = io.BytesIO()
    frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:])
    with pytest.raises(ImageError, match="animated"):
        sanitize(out.getvalue())


def test_png_header_with_webp_content_is_rejected():
    webp = encode(Image.new("RGB", (8, 8)), "WEBP")
    with pytest.raises(ImageError):
        sanitize(b"\x89PNG\r\n\x1a\n" + webp)


def test_sniff_and_slugify():
    assert sniff(b"RIFF\0\0\0\0WEBPVP8 ") == "WEBP"
    assert slugify("Ñandú en la nieve.JPG") == "nandu-en-la-nieve"
    assert slugify("???") == "image"
    assert len(slugify("x" * 200)) == 60
