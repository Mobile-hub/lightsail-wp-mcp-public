"""OAuth 2.1 authorization server for one owner and a fixed set of clients.

Implements:
- RFC 9728 protected resource metadata, RFC 8414 authorization server metadata
- Authorization code + PKCE (S256 only) for pre-registered confidential clients.
  There is no dynamic registration: clients are created with ``wpmcp client add``.
- Consent by the owner: password + TOTP, with a persistent lockout on failures, and an
  explicit choice between read-only and read-write access
- Refresh token rotation with reuse detection, grants revocable one by one
- RFC 9207 ``iss`` in authorization responses, RFC 8707 resource/audience binding

Rewritten from editorial.workflow ``mcp_oauth_gateway/oauth.py`` (see PROVENANCE.md).
"""

from __future__ import annotations

import base64
import hmac
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote_plus, urlencode, urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ..audit import audit
from ..config import RESOURCE_PATH, SCOPE_READ, SCOPE_WRITE, SCOPES, Settings
from ..net import client_ip
from .owner import match_totp, verify_password
from .pages import consent_page, error_page
from .store import Client, Grant, Store
from .tokens import Signer, TokenError, pkce_s256

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
# RFC 7636 §4.1: 43-128 unreserved characters.
CODE_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
CODE_CHALLENGE = re.compile(r"^[A-Za-z0-9\-_]{43}$")

# Failed consent attempts: per client address, and overall (a distributed guesser).
IP_FAILURE_LIMIT, IP_FAILURE_WINDOW = 5, 600
GLOBAL_FAILURE_LIMIT, GLOBAL_FAILURE_WINDOW = 30, 3600


@dataclass(frozen=True)
class Principal:
    """The authenticated caller of an MCP request: one approved connection."""

    client_id: str
    client_name: str
    grant_id: str
    scopes: frozenset[str]

    def can(self, scope: str) -> bool:
        # Write access includes reading.
        return scope in self.scopes or (scope == SCOPE_READ and SCOPE_WRITE in self.scopes)


def same_resource(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.rstrip("/") == b.rstrip("/")


def oauth_error(error: str, description: str, status: int = 400, **headers: str) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers={**NO_STORE, **headers},
    )


def order_scopes(scopes: set[str] | frozenset[str]) -> str:
    return " ".join(s for s in SCOPES if s in scopes)


class OAuthServer:
    def __init__(
        self, settings: Settings, store: Store, clock: Callable[[], float] = time.time
    ) -> None:
        self.settings = settings
        self.store = store
        self.signer = Signer(settings.secret)
        self.now = clock

    # ------------------------------------------------------------------ metadata
    @property
    def resource_metadata_url(self) -> str:
        return f"{self.settings.issuer}/.well-known/oauth-protected-resource{RESOURCE_PATH}"

    async def protected_resource_metadata(self, _request: Request) -> Response:
        return JSONResponse(
            {
                "resource": self.settings.resource,
                "authorization_servers": [self.settings.issuer],
                "bearer_methods_supported": ["header"],
                "scopes_supported": list(SCOPES),
                "resource_name": self.settings.site_name,
            }
        )

    async def authorization_server_metadata(self, _request: Request) -> Response:
        issuer = self.settings.issuer
        return JSONResponse(
            {
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": f"{issuer}/token",
                "scopes_supported": list(SCOPES),
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"],
                "authorization_response_iss_parameter_supported": True,
                "token_endpoint_auth_methods_supported": [
                    "client_secret_post",
                    "client_secret_basic",
                ],
            }
        )

    # ------------------------------------------------------------- authorization
    def _enabled_client(self, client_id: str | None) -> Client | None:
        client = self.store.get_client(client_id)
        return client if client and client.enabled else None

    async def authorize(self, request: Request) -> Response:
        if request.method == "POST":
            return await self._authorize_decision(request)

        q = request.query_params
        client = self._enabled_client(q.get("client_id"))
        if client is None:
            return HTMLResponse(error_page("Unknown client_id."), 400, headers=NO_STORE)
        redirect_uri = q.get("redirect_uri") or (
            client.redirect_uris[0] if len(client.redirect_uris) == 1 else ""
        )
        # Exact match only: an unknown redirect URI is never redirected to.
        if redirect_uri not in client.redirect_uris:
            return HTMLResponse(
                error_page("redirect_uri does not match the registration."), 400, headers=NO_STORE
            )

        state = q.get("state")
        issuer = self.settings.issuer

        def fail(error: str, description: str) -> Response:
            return _redirect(
                redirect_uri, error=error, error_description=description, state=state, iss=issuer
            )

        if q.get("response_type") != "code":
            return fail("unsupported_response_type", "only response_type=code is supported")
        challenge = q.get("code_challenge") or ""
        if q.get("code_challenge_method", "plain") != "S256" or not CODE_CHALLENGE.match(challenge):
            return fail("invalid_request", "PKCE with code_challenge_method=S256 is required")
        requested = set((q.get("scope") or " ".join(SCOPES)).split())
        unknown = requested - set(SCOPES)
        if unknown:
            return fail("invalid_scope", f"unknown scope(s): {' '.join(sorted(unknown))}")
        resource = q.get("resource")
        if resource and not same_resource(resource, self.settings.resource):
            return fail("invalid_target", "resource is not served by this authorization server")

        request_token = self.signer.issue(
            "authz_request",
            self.settings.consent_ttl,
            now=self.now(),
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=challenge,
            scope=order_scopes(requested),
        )
        return self._consent(client, redirect_uri, request_token, requested)

    def _consent(
        self,
        client: Client,
        redirect_uri: str,
        request_token: str,
        requested: set[str],
        error: str | None = None,
        status: int = 200,
    ) -> Response:
        return HTMLResponse(
            consent_page(
                site_name=self.settings.site_name,
                client_name=client.name,
                redirect_uri=redirect_uri,
                request_token=request_token,
                can_write=SCOPE_WRITE in requested,
                error=error,
            ),
            status_code=status,
            headers=NO_STORE,
        )

    async def _authorize_decision(self, request: Request) -> Response:
        form = await request.form()
        request_token = str(form.get("request", ""))
        now = self.now()
        try:
            req = self.signer.decode(request_token, typ="authz_request", now=now)
        except TokenError:
            return HTMLResponse(
                error_page("Authorization request expired, start again."), 400, headers=NO_STORE
            )
        client = self._enabled_client(req["client_id"])
        if client is None:
            return HTMLResponse(error_page("Unknown client_id."), 400, headers=NO_STORE)
        redirect_uri, state = req["redirect_uri"], req.get("state")
        requested = set(req["scope"].split())
        ip = client_ip(request, self.settings) or "unknown"

        if form.get("decision") != "approve":
            audit("consent_denied", client=client.client_id, ip=ip)
            return _redirect(
                redirect_uri,
                error="access_denied",
                error_description="the owner denied access",
                state=state,
                iss=self.settings.issuer,
            )

        def again(message: str, status: int) -> Response:
            return self._consent(client, redirect_uri, request_token, requested, message, status)

        access = form.get("access")
        if access not in ("read", "write"):
            return again("Choose read only or read and write.", 400)

        ip_failures = self.store.failures_since(f"ip:{ip}", now - IP_FAILURE_WINDOW)
        all_failures = self.store.failures_total_since(now - GLOBAL_FAILURE_WINDOW)
        if ip_failures >= IP_FAILURE_LIMIT or all_failures >= GLOBAL_FAILURE_LIMIT:
            audit("consent_locked", level=logging.WARNING, client=client.client_id, ip=ip)
            return HTMLResponse(
                error_page("Too many failed attempts. Try again later."), 429, headers=NO_STORE
            )

        owner = self.store.get_owner()
        if owner is None:
            return HTMLResponse(
                error_page("The owner is not set up yet (run: wpmcp owner set)."),
                503,
                headers=NO_STORE,
            )
        # Both factors are always evaluated, and the answer never says which one failed.
        password_ok = await run_in_threadpool(
            verify_password, str(form.get("password", "")), owner.password_hash
        )
        step = match_totp(owner.totp_secret, str(form.get("totp", "")), now)
        # A code is accepted once: its time step is recorded only when everything matched.
        if not (password_ok and step is not None and self.store.accept_totp_step(step)):
            self.store.record_failure(f"ip:{ip}", now)
            audit("consent_failed", level=logging.WARNING, client=client.client_id, ip=ip)
            return again("Wrong password or code.", 401)

        allowed = set(SCOPES) if access == "write" else {SCOPE_READ}
        granted = (requested & allowed) or {SCOPE_READ}
        scope = order_scopes(granted)
        code = self.store.create_code(
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            code_challenge=req["code_challenge"],
            scope=scope,
            resource=self.settings.resource,
            ttl=self.settings.code_ttl,
            now=now,
        )
        audit("consent_approved", client=client.client_id, scope=scope, ip=ip)
        return _redirect(redirect_uri, code=code, state=state, iss=self.settings.issuer)

    # -------------------------------------------------------------------- token
    async def token(self, request: Request) -> Response:
        form = await request.form()
        client_id, client_secret = form.get("client_id"), form.get("client_secret")
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            try:
                raw = base64.b64decode(auth[6:], validate=True).decode()
                basic_id, _, basic_secret = raw.partition(":")
            except (ValueError, UnicodeDecodeError):
                return oauth_error("invalid_client", "malformed basic auth", 401)
            client_id, client_secret = unquote_plus(basic_id), unquote_plus(basic_secret)

        client = self._enabled_client(str(client_id) if client_id else None)
        secret = str(client_secret) if client_secret else None
        if client is None or not self.store.verify_client_secret(client.client_id, secret):
            ip = client_ip(request, self.settings) or "unknown"
            audit("token_bad_client", level=logging.WARNING, ip=ip)
            return oauth_error("invalid_client", "unknown client or bad client credentials", 401)

        self.store.prune(self.now())
        grant = form.get("grant_type")
        if grant == "authorization_code":
            return self._grant_code(form, client)
        if grant == "refresh_token":
            return self._grant_refresh(form, client)
        return oauth_error("unsupported_grant_type", f"grant_type {grant!r} is not supported")

    def _grant_code(self, form, client: Client) -> Response:
        now = self.now()
        value = str(form.get("code", ""))
        code = self.store.get_code(value) if value else None
        if code is None:
            return oauth_error("invalid_grant", "invalid authorization code")
        if code.used_at is not None:
            # A second use means the code leaked: what it produced is revoked.
            if code.grant_id and self.store.revoke_grant(
                code.grant_id, "authorization code replay", now
            ):
                audit(
                    "grant_revoked",
                    level=logging.WARNING,
                    grant=code.grant_id,
                    reason="authorization code replay",
                )
            return oauth_error("invalid_grant", "authorization code already used")
        if code.expires_at <= now:
            return oauth_error("invalid_grant", "authorization code expired")
        if code.client_id != client.client_id:
            return oauth_error("invalid_grant", "code was issued to another client")
        if form.get("redirect_uri") and form.get("redirect_uri") != code.redirect_uri:
            return oauth_error("invalid_grant", "redirect_uri mismatch")
        resource = form.get("resource")
        if resource and not same_resource(str(resource), code.resource):
            return oauth_error("invalid_target", "resource does not match the authorization")
        verifier = str(form.get("code_verifier", ""))
        if not CODE_VERIFIER.match(verifier):
            return oauth_error("invalid_grant", "code_verifier must be 43-128 unreserved chars")
        if not hmac.compare_digest(pkce_s256(verifier), code.code_challenge):
            return oauth_error("invalid_grant", "PKCE verification failed")
        grant = self.store.redeem_code(value, now)
        if grant is None:
            return oauth_error("invalid_grant", "authorization code already used")
        refresh = self.store.issue_refresh(grant.grant_id, self.settings.refresh_token_ttl, now)
        audit("grant_created", grant=grant.grant_id, client=client.client_id, scope=grant.scope)
        return self._token_response(grant, grant.scope, refresh)

    def _grant_refresh(self, form, client: Client) -> Response:
        now = self.now()
        value = str(form.get("refresh_token", ""))
        current = self.store.peek_refresh(value, now) if value else None
        if current is None or current.client_id != client.client_id:
            return oauth_error("invalid_grant", "invalid refresh token")
        scope = str(form.get("scope") or current.scope)
        if not set(scope.split()) <= set(current.scope.split()):
            return oauth_error("invalid_scope", "cannot widen scope on refresh")
        resource = form.get("resource")
        if resource and not same_resource(str(resource), current.resource):
            return oauth_error("invalid_target", "resource does not match the authorization")
        status, grant, refresh = self.store.rotate_refresh(
            value,
            client_id=client.client_id,
            ttl=self.settings.refresh_token_ttl,
            grace=self.settings.refresh_grace,
            now=now,
        )
        if status == "reused" and grant is not None:
            audit(
                "grant_revoked",
                level=logging.WARNING,
                grant=grant.grant_id,
                reason="refresh token reuse",
            )
        if status != "ok" or grant is None or refresh is None:
            return oauth_error("invalid_grant", "invalid refresh token")
        return self._token_response(grant, order_scopes(set(scope.split())), refresh)

    def _token_response(self, grant: Grant, scope: str, refresh: str) -> Response:
        return JSONResponse(
            {
                "access_token": self.mint_access_token(grant, scope=scope),
                "token_type": "Bearer",
                "expires_in": self.settings.access_token_ttl,
                "refresh_token": refresh,
                "scope": scope,
            },
            headers=NO_STORE,
        )

    def mint_access_token(
        self, grant: Grant, *, scope: str | None = None, ttl: int | None = None
    ) -> str:
        return self.signer.issue(
            "access",
            ttl if ttl is not None else self.settings.access_token_ttl,
            now=self.now(),
            sub="owner",
            client_id=grant.client_id,
            scope=scope or grant.scope,
            iss=self.settings.issuer,
            aud=grant.resource,
            gid=grant.grant_id,
            dbid=self.store.db_id,
        )

    def verify_access_token(self, token: str) -> Principal:
        """Signature, type, expiry, issuer, audience, database, and a live grant and client."""
        claims = self.signer.decode(token, typ="access", now=self.now())
        if claims.get("iss") != self.settings.issuer:
            raise TokenError("token issuer does not match")
        if not same_resource(claims.get("aud"), self.settings.resource):
            raise TokenError("token audience does not match this resource")
        if claims.get("dbid") != self.store.db_id:
            raise TokenError("token was issued for another database")
        grant = self.store.get_grant(claims.get("gid"))
        if grant is None or not grant.active:
            raise TokenError("grant revoked")
        client = self.store.get_client(grant.client_id)
        if client is None or not client.enabled:
            raise TokenError("client disabled")
        # Never more than the grant allows, even if a token claimed otherwise.
        scopes = set(str(claims.get("scope", "")).split()) & set(grant.scope.split())
        return Principal(client.client_id, client.name, grant.grant_id, frozenset(scopes))


def _redirect(uri: str, **params: str | None) -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    separator = "&" if urlsplit(uri).query else "?"
    return RedirectResponse(f"{uri}{separator}{query}", status_code=302, headers=NO_STORE)
