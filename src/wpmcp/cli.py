"""Command line: ``wpmcp serve`` and the owner's administration commands.

Administration commands act directly on the SQLite database (``WPMCP_DB``), so they
work while the service is running and need no network access:

    wpmcp owner set              set the consent password and enrol a new TOTP secret
    wpmcp client add --preset claude|chatgpt
    wpmcp client list | disable <client_id>
    wpmcp grant list | revoke <grant_id>
    wpmcp revoke-all             kill switch: every connection must be approved again
    wpmcp token                  short-lived access token without the browser flow
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from urllib.parse import urlsplit

from .audit import audit
from .auth.owner import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    new_totp_secret,
    provisioning_uri,
)
from .auth.store import CLI_CLIENT_ID, Store
from .config import SCOPE_READ, SCOPES, Settings

# Redirect URIs of the hosted clients (docs/clients.md records where they come from).
PRESETS = {
    "claude": ("Claude", ["https://claude.ai/api/mcp/auth_callback"]),
    "chatgpt": ("ChatGPT", ["https://chatgpt.com/connector_platform_oauth_redirect"]),
}
MAX_CLI_TOKEN_TTL = 3600


_opened: list[Store] = []


def _store(path: str | None = None) -> Store:
    """Open the database; ``main`` closes it when the command ends."""
    path = path or os.environ.get("WPMCP_DB", "")
    if not path:
        raise ValueError("WPMCP_DB is required")
    store = Store(path)
    _opened.append(store)
    return store


def _when(timestamp: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(timestamp)) if timestamp else "-"


# ------------------------------------------------------------------------------ serve
def _serve(args: argparse.Namespace) -> int:
    import logging

    import uvicorn

    from .app import create_app

    settings = Settings.from_env()
    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # proxy_headers stays OFF: uvicorn would replace the client address with the FIRST
    # X-Forwarded-For hop, which the client itself controls (the proxy appends to the end).
    # The app reads the last hop from trusted proxies instead (net.py).
    target = (
        {"uds": settings.uds} if settings.uds else {"host": settings.host, "port": settings.port}
    )
    uvicorn.run(
        create_app(settings),
        proxy_headers=False,
        server_header=False,
        access_log=False,
        log_level=args.log_level,
        **target,
    )
    return 0


def _media_worker(_args: argparse.Namespace) -> int:
    """One image request on stdin/stdout (started per connection by wpmcp-media.socket)."""
    import logging

    from .media.worker import serve

    logging.basicConfig(level="INFO", stream=sys.stderr, format="%(levelname)s %(message)s")
    return serve(sys.stdin.buffer, sys.stdout.buffer)


# ------------------------------------------------------------------------------ owner
def _read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("New owner password: ")
    if getpass.getpass("Repeat it: ") != password:
        raise ValueError("the passwords do not match")
    return password


def _owner_set(args: argparse.Namespace) -> int:
    password = _read_password(args)
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"the password must be at least {MIN_PASSWORD_LENGTH} characters")
    secret = new_totp_secret()
    _store().set_owner(hash_password(password), secret)
    audit("owner_set")
    print("Owner password set. Add this account to your authenticator app, now:")
    print(f"  secret: {secret}")
    print(f"  uri:    {provisioning_uri(secret, args.account, args.issuer)}")
    print("It is not shown again. Running this command again replaces both factors.")
    return 0


# ---------------------------------------------------------------------------- clients
def _client_add(args: argparse.Namespace) -> int:
    name, redirects = args.name, list(args.redirect_uri or [])
    if args.preset:
        preset_name, preset_redirects = PRESETS[args.preset]
        name = name or preset_name
        redirects = redirects or list(preset_redirects)
    if not name or not redirects:
        raise ValueError("give --preset, or both --name and at least one --redirect-uri")
    for uri in redirects:
        parts = urlsplit(uri)
        if parts.scheme != "https" or not parts.hostname or parts.fragment:
            raise ValueError(f"redirect URIs must be https URLs without a fragment: {uri}")
    client, secret = _store().add_client(name, redirects)
    audit("client_added", client=client.client_id, name=name)
    print(f"Client registered: {name}")
    print(f"  client_id:     {client.client_id}")
    print(f"  client_secret: {secret}")
    for uri in redirects:
        print(f"  redirect_uri:  {uri}")
    print("The secret is not shown again. Paste both values into the connector settings.")
    return 0


def _client_list(_args: argparse.Namespace) -> int:
    for client in _store().list_clients():
        state = "disabled" if not client.enabled else "enabled"
        print(f"{client.client_id}\t{state}\t{client.name}\t{', '.join(client.redirect_uris)}")
    return 0


def _client_disable(args: argparse.Namespace) -> int:
    if not _store().disable_client(args.client_id):
        raise ValueError(f"no enabled client {args.client_id}")
    audit("client_disabled", client=args.client_id)
    print(f"Client {args.client_id} disabled and its connections revoked.")
    return 0


# ----------------------------------------------------------------------------- grants
def _grant_list(args: argparse.Namespace) -> int:
    for grant in _store().list_grants(include_revoked=args.all):
        state = f"revoked ({grant.revoked_reason})" if not grant.active else "active"
        print(
            f"{grant.grant_id}\t{grant.client_id}\t{grant.scope}\t"
            f"created {_when(grant.created_at)}\tlast refresh {_when(grant.last_used_at)}\t{state}"
        )
    return 0


def _grant_revoke(args: argparse.Namespace) -> int:
    if not _store().revoke_grant(args.grant_id, "revoked by the owner"):
        raise ValueError(f"no active grant {args.grant_id}")
    audit("grant_revoked", grant=args.grant_id, reason="revoked by the owner")
    print(f"Grant {args.grant_id} revoked.")
    return 0


def _revoke_all(_args: argparse.Namespace) -> int:
    count = _store().revoke_all("revoke-all")
    audit("revoke_all", grants=count)
    print(f"{count} connection(s) revoked. Each client must be approved again.")
    return 0


# ------------------------------------------------------------------------------ token
def _token(args: argparse.Namespace) -> int:
    from .auth.oauth import OAuthServer

    settings = Settings.from_env()
    if not 0 < args.ttl <= MAX_CLI_TOKEN_TTL:
        raise ValueError(f"--ttl must be between 1 and {MAX_CLI_TOKEN_TTL} seconds")
    store = _store(settings.db_path)
    store.ensure_cli_client()
    scope = " ".join(SCOPES) if args.write else SCOPE_READ
    grant = store.create_grant(CLI_CLIENT_ID, scope, settings.resource)
    token = OAuthServer(settings, store).mint_access_token(grant, ttl=args.ttl)
    audit("cli_token", grant=grant.grant_id, scope=scope, ttl=args.ttl)
    if args.json:
        print(json.dumps({"access_token": token, "token_type": "Bearer", "expires_in": args.ttl}))
    else:
        print(token)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wpmcp", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the server")
    serve.add_argument("--log-level", default="info")
    serve.set_defaults(func=_serve)
    sub.add_parser(
        "media-worker", help="process one image on stdin/stdout (systemd socket activation)"
    ).set_defaults(func=_media_worker)

    owner = sub.add_parser("owner", help="owner credentials").add_subparsers(
        dest="action", required=True
    )
    owner_set = owner.add_parser("set", help="set the consent password and a new TOTP secret")
    owner_set.add_argument("--password-stdin", action="store_true", help="read it from stdin")
    owner_set.add_argument("--account", default="owner", help="label in the authenticator app")
    owner_set.add_argument("--issuer", default="wpmcp", help="issuer in the authenticator app")
    owner_set.set_defaults(func=_owner_set)

    client = sub.add_parser("client", help="pre-registered OAuth clients").add_subparsers(
        dest="action", required=True
    )
    client_add = client.add_parser("add", help="register a client and print its secret once")
    client_add.add_argument("--preset", choices=sorted(PRESETS))
    client_add.add_argument("--name")
    client_add.add_argument("--redirect-uri", action="append")
    client_add.set_defaults(func=_client_add)
    client.add_parser("list", help="list clients").set_defaults(func=_client_list)
    client_disable = client.add_parser("disable", help="disable a client, revoke its grants")
    client_disable.add_argument("client_id")
    client_disable.set_defaults(func=_client_disable)

    grant = sub.add_parser("grant", help="approved connections").add_subparsers(
        dest="action", required=True
    )
    grant_list = grant.add_parser("list", help="list connections")
    grant_list.add_argument("--all", action="store_true", help="include revoked ones")
    grant_list.set_defaults(func=_grant_list)
    grant_revoke = grant.add_parser("revoke", help="revoke one connection")
    grant_revoke.add_argument("grant_id")
    grant_revoke.set_defaults(func=_grant_revoke)

    sub.add_parser("revoke-all", help="revoke every connection").set_defaults(func=_revoke_all)

    token = sub.add_parser("token", help="mint a short-lived access token (smoke tests)")
    token.add_argument("--ttl", type=int, default=600, help="lifetime in seconds (max 3600)")
    token.add_argument("--write", action="store_true", help="include wp:write (default: read)")
    token.add_argument("--json", action="store_true", help="print an OAuth token response")
    token.set_defaults(func=_token)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        while _opened:
            _opened.pop().close()


if __name__ == "__main__":
    sys.exit(main())
