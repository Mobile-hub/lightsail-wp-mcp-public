"""Image checks and re-encoding.

What reaches WordPress is always a new file written by Pillow from the decoded pixels:
nothing of the original survives except the pixels and the colour profile. That drops
metadata (camera details, the GPS position of phone photos), appended payloads and
polyglot files in one step. Before anything is decoded: magic bytes, a size cap, a
pixel cap read from the header (decompression bombs) and a decoder restricted to the
sniffed format. Large images are scaled down to ``MAX_EDGE``, as WordPress itself would
(big_image_size_threshold), but without keeping the full-size original.

Adapted from editorial.workflow ``editorial_assets/images.py`` (see PROVENANCE.md): the
same sniffing and pixel cap, with re-encoding instead of keeping the original bytes.

Pillow is imported lazily: in production only the image worker decodes images.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
import warnings
from dataclasses import dataclass

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_EDGE = 2560
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


class ImageError(Exception):
    """Rejected image; the message is safe to show to the client."""


@dataclass(frozen=True)
class CleanImage:
    data: bytes
    format: str
    width: int
    height: int

    @property
    def mime(self) -> str:
        return FORMATS[self.format][0]

    @property
    def ext(self) -> str:
        return FORMATS[self.format][1]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def sniff(data: bytes) -> str:
    if data.startswith(PNG_MAGIC):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    raise ImageError("unsupported file: only PNG, JPEG or WebP images are accepted")


def sanitize(
    data: bytes,
    *,
    max_bytes: int = MAX_BYTES,
    max_pixels: int = MAX_PIXELS,
    max_edge: int = MAX_EDGE,
) -> CleanImage:
    """Validate ``data`` and return a freshly encoded copy in the same format."""
    if not data:
        raise ImageError("empty file")
    if len(data) > max_bytes:
        raise ImageError(f"image too large ({len(data)} bytes, max {max_bytes})")
    fmt = sniff(data)

    from PIL import Image, ImageOps

    # Pillow only raises at 2x MAX_IMAGE_PIXELS (and warns at 1x): the explicit check on the
    # header below is what refuses anything above the cap before a pixel is decoded.
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=[fmt]) as source:
                width, height = source.size  # from the header, nothing decoded yet
                if width * height > max_pixels:
                    raise ImageError("image has too many pixels")
                if getattr(source, "n_frames", 1) > 1:
                    raise ImageError("animated images are not accepted")
                source.load()
                icc = source.info.get("icc_profile")
                image = ImageOps.exif_transpose(source)  # apply, then drop, the orientation
                image, keep_icc = _normalise_mode(image, fmt)
                image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                options = {"icc_profile": icc} if icc and keep_icc else {}
                if fmt == "JPEG":
                    image.save(out, "JPEG", quality=88, optimize=True, progressive=True, **options)
                elif fmt == "PNG":
                    image.save(out, "PNG", optimize=True, **options)
                else:
                    image.save(out, "WEBP", quality=85, method=4, **options)
                width, height = image.size
    except ImageError:
        raise
    except Image.DecompressionBombError as exc:
        raise ImageError("image has too many pixels") from exc
    except Exception as exc:  # noqa: BLE001 - any decoder error means "not a valid image"
        raise ImageError(f"not a valid image ({exc.__class__.__name__})") from exc
    return CleanImage(out.getvalue(), fmt, width, height)


_MODES = {"JPEG": ("L", "RGB"), "PNG": ("L", "LA", "RGB", "RGBA"), "WEBP": ("RGB", "RGBA")}


def _normalise_mode(image, fmt: str):
    """A mode the encoder writes; returns (image, whether the colour profile still applies)."""
    if image.mode in _MODES[fmt]:
        return image, True
    alpha = fmt != "JPEG" and (image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info)
    # The profile describes the original colour space: keep it only if that was RGB or grey.
    keep_icc = image.mode in ("1", "L", "LA", "P", "PA", "RGB", "RGBA")
    return image.convert("RGBA" if alpha else "RGB"), keep_icc


def slugify(text: str, fallback: str = "image") -> str:
    """File name stem: ASCII, lower case, dashes, at most 60 characters."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"\.(png|jpe?g|webp|gif|heic)$", "", text.strip().lower())
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:60].strip("-") or fallback
