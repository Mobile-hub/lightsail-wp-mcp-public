"""Resource-server gate in front of the MCP endpoint.

Order of checks: method, client address allowlist, request rate, bearer token (signature,
audience, live grant), declared body size. Only then does the request reach the MCP
server, with the authenticated ``Principal`` in the ASGI scope. Tools read it from there;
nothing downstream sees or needs the token.
"""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ..audit import audit
from ..config import Settings
from ..net import client_ip
from .cidrs import Allowlist
from .oauth import OAuthServer
from .ratelimit import SlidingWindow
from .tokens import TokenError

PRINCIPAL_KEY = "wpmcp.principal"
# Headers the MCP transport must not see: the credential, and Origin (its DNS-rebinding
# guard rejects foreign origins, which is meaningless once a bearer token is required).
_STRIPPED = {b"authorization", b"cookie", b"origin"}


class McpGate:
    def __init__(
        self,
        app: ASGIApp,
        *,
        oauth: OAuthServer,
        settings: Settings,
        allowlist: Allowlist,
        limiter: SlidingWindow | None = None,
    ) -> None:
        self.app = app
        self.oauth = oauth
        self.settings = settings
        self.allowlist = allowlist
        self.limiter = limiter or SlidingWindow(limit=300, window=60.0)

    def _unauthorized(self, error: str | None = None) -> Response:
        challenge = f'Bearer resource_metadata="{self.oauth.resource_metadata_url}"'
        if error:
            challenge += f', error="invalid_token", error_description="{error}"'
        return JSONResponse(
            {
                "error": "invalid_token" if error else "unauthorized",
                "error_description": error or "Bearer token required",
            },
            status_code=401,
            headers={"WWW-Authenticate": challenge},
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = self._check(scope)
        if response is not None:
            await response(scope, receive, send)
            return
        headers = [(k, v) for k, v in scope["headers"] if k.lower() not in _STRIPPED]
        await self.app({**scope, "headers": headers}, receive, send)

    def _check(self, scope: Scope) -> Response | None:
        request = Request(scope)
        # Stateless Streamable HTTP: one POST per message, no server-initiated stream.
        if request.method != "POST":
            return JSONResponse(
                {"error": "method_not_allowed"}, status_code=405, headers={"Allow": "POST"}
            )
        ip = client_ip(request, self.settings)
        if not self.allowlist.permits(ip):
            audit("deny_ip", level=logging.WARNING, ip=ip or "unknown", path=scope["path"])
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if not self.limiter.allow(ip or "unknown"):
            return JSONResponse(
                {"error": "rate_limited"}, status_code=429, headers={"Retry-After": "60"}
            )

        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return self._unauthorized()
        try:
            principal = self.oauth.verify_access_token(token.strip())
        except TokenError as exc:
            return self._unauthorized(str(exc))
        if not principal.scopes:
            return JSONResponse(
                {"error": "insufficient_scope"},
                status_code=403,
                headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
            )

        # Declared sizes are refused before reading anything; the MCP transport enforces
        # the same cap on bodies without a Content-Length.
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.settings.max_body_bytes:
            return JSONResponse({"error": "payload_too_large"}, status_code=413)

        scope[PRINCIPAL_KEY] = principal
        return None
