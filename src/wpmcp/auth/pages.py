"""Minimal HTML for the consent screen (no template engine needed).

Adapted from editorial.workflow ``mcp_oauth_gateway/pages.py`` (see PROVENANCE.md).
"""

from __future__ import annotations

from html import escape

_STYLE = """
:root{color-scheme:light dark;--bg:#f6f5f2;--fg:#1d1d1b;--card:#fff;--muted:#6b6a66;--accent:#b4462b}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--card:#22211e;--muted:#a3a19a}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;
background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;padding:16px}
main{background:var(--card);max-width:440px;width:100%;padding:28px;border-radius:12px;
box-shadow:0 1px 3px #0002}h1{font-size:1.25rem;margin:0 0 12px}p{margin:0 0 12px}
.muted{color:var(--muted);font-size:.9rem;word-break:break-all}
input[type=password],input[type=text]{width:100%;padding:10px;font:inherit;
border:1px solid var(--muted);border-radius:8px;background:transparent;color:inherit;margin:4px 0 16px}
fieldset{border:1px solid var(--muted);border-radius:8px;margin:0 0 16px;padding:10px 12px}
legend{padding:0 6px}.choice{display:block;margin:6px 0}.choice small{display:block;color:var(--muted);margin-left:24px}
.row{display:flex;gap:8px}button{flex:1;padding:10px;font:inherit;border-radius:8px;
border:1px solid var(--accent);cursor:pointer}
.approve{background:var(--accent);color:#fff}.deny{background:transparent;color:inherit}
.error{color:var(--accent)}
"""


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def consent_page(
    *,
    site_name: str,
    client_name: str,
    redirect_uri: str,
    request_token: str,
    can_write: bool,
    error: str | None = None,
) -> str:
    err = f"<p class=error>{escape(error)}</p>" if error else ""
    write_choice = (
        """
    <label class=choice><input type=radio name=access value=write required> Read and write
      <small>Can also create, edit, publish and trash content, and change menus.</small></label>"""
        if can_write
        else ""
    )
    body = f"""
<h1>Connect to {escape(site_name)}</h1>
<p><strong>{escape(client_name)}</strong> wants to operate this WordPress site.</p>
<p class=muted>Redirect: {escape(redirect_uri)}</p>
<p class=muted>If you did not start this connection yourself a moment ago, deny it.</p>
{err}
<form method=post action=/authorize>
  <input type=hidden name=request value="{escape(request_token)}">
  <fieldset><legend>Access</legend>
    <label class=choice><input type=radio name=access value=read required> Read only
      <small>Can list and read content. Cannot change anything.</small></label>{write_choice}
  </fieldset>
  <label for=password>Owner password</label>
  <input id=password name=password type=password autocomplete=current-password autofocus>
  <label for=totp>Authenticator code</label>
  <input id=totp name=totp type=text inputmode=numeric autocomplete=one-time-code maxlength=7>
  <div class=row>
    <button class=deny name=decision value=deny type=submit formnovalidate>Deny</button>
    <button class=approve name=decision value=approve type=submit>Approve</button>
  </div>
</form>"""
    return _page(f"Authorize · {site_name}", body)


def error_page(message: str) -> str:
    return _page("OAuth error", f"<h1>Cannot authorize</h1><p class=error>{escape(message)}</p>")


# ------------------------------------------------------------------ image upload links
def upload_page(*, site_name: str, params: dict, expires_in: int, error: str = "") -> str:
    """The form behind a single-use link from request_image_upload."""
    title = params.get("title") or ""
    details = (f"<p class=muted>Title: {escape(title)}</p>" if title else "") + (
        f"<p class=muted>Attached to item {int(params['attach_to'])}</p>"
        if params.get("attach_to")
        else ""
    )
    err = f"<p class=error>{escape(error)}</p>" if error else ""
    minutes = max(1, expires_in // 60)
    body = (
        f"<h1>Upload an image to {escape(site_name)}</h1>{err}{details}"
        "<form method=post enctype=multipart/form-data>"
        "<input type=file name=file accept='image/jpeg,image/png,image/webp' required>"
        "<div class=row style='margin-top:16px'>"
        "<button class=approve type=submit>Upload</button></div></form>"
        "<p class=muted style='margin-top:16px'>JPEG, PNG or WebP. The image is checked and "
        "re-encoded (camera data and location are removed). This link works once and expires "
        f"in {minutes} minutes.</p>"
    )
    return _page(f"Upload · {site_name}", body)


def upload_done_page(*, site_name: str, media: dict) -> str:
    size = (
        f" ({int(media['width'])}×{int(media['height'])})"
        if media.get("width") and media.get("height")
        else ""
    )
    body = (
        "<h1>Image uploaded</h1>"
        f"<p>Saved to the media library of {escape(site_name)} as image "
        f"<strong>{int(media['id'])}</strong>{size}.</p>"
        "<p>Go back to the conversation and say it is uploaded.</p>"
    )
    return _page("Image uploaded", body)


def upload_gone_page() -> str:
    return _page(
        "Link not valid",
        "<h1>This upload link is not valid</h1>"
        "<p>It was already used, it expired, or its connection was revoked. "
        "Ask in the conversation for a new one.</p>",
    )
