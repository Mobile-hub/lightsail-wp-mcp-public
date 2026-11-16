"""ASGI application wiring: OAuth endpoints + the protected MCP endpoint, in one process."""

from __future__ import annotations

import contextlib
import logging

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from . import __version__
from .audit import audit
from .auth.bearer import McpGate
from .auth.cidrs import Allowlist
from .auth.oauth import OAuthServer
from .auth.ratelimit import SlidingWindow
from .auth.store import Store
from .config import RESOURCE_PATH, Settings
from .mcp_server import build_mcp
from .media.uploads import upload_routes
from .media.worker import MediaProcessor
from .net import client_ip
from .tools import Registry
from .wp.client import WordPressClient

# OAuth form bodies (/token and the consent POST) are tiny.
_OAUTH_MAX_BODY = 64 * 1024
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (
        b"content-security-policy",
        # No form-action: the consent form must be able to redirect to the client.
        b"default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
    ),
]


class GuardMiddleware:
    """Security headers on every response; address allowlist and request rates on OAuth paths.

    ``/token`` is called only by the clients' backends, so the allowlist applies to it.
    ``/authorize`` is opened by the owner's browser from anywhere: it is rate limited here
    and protected by the consent lockout in ``oauth.py``.
    """

    def __init__(self, app: ASGIApp, settings: Settings, allowlist: Allowlist) -> None:
        self.app = app
        self.settings = settings
        self.allowlist = allowlist
        self.limits = {
            "/token": SlidingWindow(limit=60, window=60.0),
            "/authorize": SlidingWindow(limit=30, window=60.0),
        }
        # Upload links are opened from the owner's browser or phone, from anywhere.
        self.upload_limit = SlidingWindow(limit=20, window=60.0)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if path.startswith("/upload/"):
            ip = client_ip(Request(scope), self.settings)
            if not self.upload_limit.allow(ip or "unknown"):
                denied = JSONResponse(
                    {"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60"}
                )
                await denied(scope, receive, send)
                return
        elif path in self.limits:
            request = Request(scope)
            ip = client_ip(request, self.settings)
            length = request.headers.get("content-length", "")
            denied = None
            if path == "/token" and not self.allowlist.permits(ip):
                audit("deny_ip", level=logging.WARNING, ip=ip or "unknown", path=path)
                denied = JSONResponse({"error": "forbidden"}, status_code=403)
            elif not self.limits[path].allow(ip or "unknown"):
                denied = JSONResponse(
                    {"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60"}
                )
            elif length.isdigit() and int(length) > _OAUTH_MAX_BODY:
                denied = JSONResponse({"error": "payload_too_large"}, status_code=413)
            if denied is not None:
                await denied(scope, receive, send)
                return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                extra = [(k, v) for k, v in _SECURITY_HEADERS if k not in names]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(
    settings: Settings | None = None,
    *,
    store: Store | None = None,
    registry: Registry | None = None,
    wp: WordPressClient | None = None,
    media: MediaProcessor | None = None,
    clock=None,
) -> Starlette:
    settings = settings or Settings.from_env()
    store = store or Store(settings.db_path)
    oauth = OAuthServer(settings, store, **({"clock": clock} if clock else {}))
    allowlist = Allowlist(settings.cidr_file, settings.cidr_mode)
    if wp is None and settings.wp_url and registry is None:
        wp = WordPressClient(settings)
    if wp is not None and media is None:
        media = MediaProcessor(settings)
    mcp = build_mcp(settings, registry, wp, store, media, **({"clock": clock} if clock else {}))
    gate = McpGate(mcp.streamable_http_app(), oauth=oauth, settings=settings, allowlist=allowlist)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        try:
            async with mcp.session_manager.run():
                yield
        finally:
            if wp is not None:
                await wp.aclose()

    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    async def root(_request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "service": "wpmcp",
                "mcp": RESOURCE_PATH,
                "metadata": "/.well-known/oauth-authorization-server",
            }
        )

    # Every body-carrying route has a max_body_size, enforced while the body streams in,
    # so a chunked request without Content-Length is cut off at the cap too.
    routes = [
        Route("/", root),
        Route("/healthz", healthz),
        Route("/.well-known/oauth-protected-resource", oauth.protected_resource_metadata),
        Route(
            f"/.well-known/oauth-protected-resource{RESOURCE_PATH}",
            oauth.protected_resource_metadata,
        ),
        Route("/.well-known/oauth-authorization-server", oauth.authorization_server_metadata),
        Route(
            f"/.well-known/oauth-authorization-server{RESOURCE_PATH}",
            oauth.authorization_server_metadata,
        ),
        Route(
            "/authorize",
            oauth.authorize,
            methods=["GET", "POST"],
            max_body_size=_OAUTH_MAX_BODY,
        ),
        Route("/token", oauth.token, methods=["POST"], max_body_size=_OAUTH_MAX_BODY),
        # An ASGI app as endpoint receives every method; the gate answers 405 itself.
        Route(RESOURCE_PATH, gate),
    ]
    if wp is not None and media is not None:
        routes += upload_routes(settings, store, wp, media, **({"clock": clock} if clock else {}))
    middleware = [Middleware(GuardMiddleware, settings=settings, allowlist=allowlist)]
    app = Starlette(routes=routes, middleware=middleware, lifespan=lifespan)
    app.state.oauth = oauth
    app.state.store = store
    app.state.settings = settings
    return app
