"""Putting images into the WordPress media library, and the single-use upload links.

Both ways in end in ``save_image``: a ChatGPT file (``import_image``) or a file the owner
uploads through a link from ``request_image_upload`` (any client, from a phone or a
browser). The link is a random token of which only a hash is stored; it works once,
expires (``WPMCP_UPLOAD_TTL``) and dies with the connection that asked for it.
"""

from __future__ import annotations

import time
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route

from ..audit import audit
from ..auth.pages import upload_done_page, upload_gone_page, upload_page
from ..auth.store import Store
from ..config import SCOPE_WRITE, Settings
from ..wp.client import WordPressClient, WordPressError
from .images import CleanImage, ImageError, slugify
from .worker import MediaProcessor

# Fields a link may carry to the upload (what request_image_upload accepted).
LINK_FIELDS = ("title", "alt", "caption", "attach_to")


async def save_image(
    wp: WordPressClient, image: CleanImage, params: dict[str, Any], name_hint: str = ""
) -> dict:
    """Upload a sanitized image; returns the media item as the bridge describes it."""
    stem = slugify(params.get("title") or name_hint)
    return await wp.upload(
        "/media",
        data=image.data,
        filename=f"{stem}{image.ext}",
        mime=image.mime,
        fields={key: params.get(key) for key in LINK_FIELDS},
    )


def upload_routes(
    settings: Settings,
    store: Store,
    wp: WordPressClient,
    media: MediaProcessor,
    clock=time.time,
) -> list[Route]:
    async def upload(request: Request) -> Response:
        token = request.path_params["token"]
        link = store.get_upload_link(token, now=clock())
        grant = store.get_grant(link.grant_id) if link else None
        if link is None or grant is None or SCOPE_WRITE not in grant.scope.split():
            return HTMLResponse(upload_gone_page(), status_code=410, headers=_NO_STORE)
        expires_in = max(0, int(link.expires_at - clock()))

        def form(error: str = "", status: int = 200) -> Response:
            page = upload_page(
                site_name=settings.site_name,
                params=link.params,
                expires_in=expires_in,
                error=error,
            )
            return HTMLResponse(page, status_code=status, headers=_NO_STORE)

        if request.method == "GET":
            return form()
        if not store.claim_upload_link(token, now=clock()):
            return HTMLResponse(upload_gone_page(), status_code=410, headers=_NO_STORE)
        try:
            data, filename = await _read_file(request, settings.media_max_bytes)
            image = await media.sanitize(data)
            item = await save_image(wp, image, link.params, filename)
        except (ImageError, WordPressError) as exc:
            store.release_upload_link(token)
            return form(str(exc), status=400)
        except Exception:
            store.release_upload_link(token)
            raise
        store.finish_upload_link(token, int(item["id"]), now=clock())
        audit(
            "wp_write",
            tool="upload_link",
            object=item["id"],
            client=grant.client_id,
            grant=grant.grant_id,
        )
        page = upload_done_page(site_name=settings.site_name, media=item)
        return HTMLResponse(page, headers=_NO_STORE)

    # The body is capped while it streams in (chunked requests too).
    return [
        Route(
            "/upload/{token}",
            upload,
            methods=["GET", "POST"],
            max_body_size=settings.media_max_bytes + 64 * 1024,
        )
    ]


_NO_STORE = {"Cache-Control": "no-store"}


async def _read_file(request: Request, max_bytes: int) -> tuple[bytes, str]:
    form = await request.form(max_files=1, max_fields=1, max_part_size=max_bytes + 1024)
    try:
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise ImageError("Choose an image file.")
        data = await upload.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ImageError(f"The image is too large (max {max_bytes // (1024 * 1024)} MB).")
        return data, upload.filename or ""
    finally:
        await form.close()
