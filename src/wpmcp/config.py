"""Runtime settings, read from environment variables and credential files (see .env.example)."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# The single protected resource. Its public URL is the token audience.
RESOURCE_PATH = "/mcp"

SCOPE_READ = "wp:read"
SCOPE_WRITE = "wp:write"
SCOPES = (SCOPE_READ, SCOPE_WRITE)

CIDR_MODES = ("off", "monitor", "enforce")
SECRET_CREDENTIAL = "wpmcp-secret"  # noqa: S105 - the name of a credential file, not a secret
WP_PASSWORD_CREDENTIAL = "wp-app-password"  # noqa: S105 - a credential file name

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    return int(value)


def _list(value: str | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Comma/whitespace separated list; unset = default, "none" or "-" = empty."""
    if value is None:
        return default
    if value.strip().lower() in {"none", "-"}:
        return ()
    items = tuple(v.strip() for v in value.replace("\n", ",").split(",") if v.strip())
    return items or default


def _networks(values: tuple[str, ...]) -> tuple[Network, ...]:
    return tuple(ipaddress.ip_network(v, strict=False) for v in values)


def _load_credential(env: dict[str, str], name: str, credential: str) -> str:
    """``{name}_FILE``, else the systemd credential, else ``{name}`` itself (development)."""
    path = env.get(f"{name}_FILE", "")
    if not path and env.get("CREDENTIALS_DIRECTORY"):
        candidate = Path(env["CREDENTIALS_DIRECTORY"]) / credential
        if candidate.is_file():
            path = str(candidate)
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return env.get(name, "")


def load_secret(env: dict[str, str]) -> str:
    """Signing key: WPMCP_SECRET_FILE, else the systemd credential, else WPMCP_SECRET (dev)."""
    return _load_credential(env, "WPMCP_SECRET", SECRET_CREDENTIAL)


def load_wp_password(env: dict[str, str]) -> str:
    """WordPress Application Password: WPMCP_WP_PASSWORD_FILE, credential, or env (dev)."""
    return _load_credential(env, "WPMCP_WP_PASSWORD", WP_PASSWORD_CREDENTIAL)


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    secret: str = field(repr=False)
    public_url: str
    db_path: str
    site_name: str = "WordPress"
    access_token_ttl: int = 900
    refresh_token_ttl: int = 14 * 24 * 3600
    # A rotated refresh token is still accepted this long, so a retried or concurrent
    # refresh from the client's backend does not look like theft and revoke the grant.
    refresh_grace: int = 60
    code_ttl: int = 120
    consent_ttl: int = 600
    host: str = "127.0.0.1"
    port: int = 8080
    uds: str = ""
    # Peers whose X-Forwarded-For is trusted (the local reverse proxy).
    trusted_proxy_cidrs: tuple[Network, ...] = field(
        default_factory=lambda: _networks(("127.0.0.0/8", "::1/128"))
    )
    # Behind a Unix socket there is no peer address: only the reverse proxy can connect
    # (socket permissions), so its forwarded headers are trusted.
    trust_unix_peer: bool = False
    # Egress ranges of the MCP clients (Anthropic, OpenAI), one CIDR per line.
    cidr_file: str = ""
    cidr_mode: str = "off"
    max_body_bytes: int = 4 * 1024 * 1024
    # Local development only: allows an http:// PUBLIC_URL on loopback.
    insecure_dev: bool = False
    # WordPress: site URL, integration user and its Application Password. Without a URL the
    # server runs with the diagnostic tool only.
    wp_url: str = ""
    wp_user: str = "wpmcp"
    wp_password: str = field(default="", repr=False)
    # Connect to this address instead of resolving the site's host name (TLS and the Host
    # header still use the name): in production, 127.0.0.1, so traffic never leaves the box.
    wp_connect_ip: str = ""
    wp_timeout: float = 30.0
    # Images: decoded, re-encoded and downloaded by a separate sandboxed worker reached on
    # this Unix socket (systemd socket activation, one process per image). Empty: in this
    # process (development only; production has no internet access and keeps no decoder).
    media_socket: str = ""
    media_max_bytes: int = 10 * 1024 * 1024
    # Single-use upload links (request_image_upload), seconds.
    upload_ttl: int = 900
    # Write tool calls allowed per connection and hour (a runaway loop stops here).
    write_budget: int = 120
    # Of those, calls that change what visitors see (publish, live edits, trash, menus).
    live_budget: int = 30
    # Posts and pages no tool may unpublish, trash or change once live (the bridge also
    # protects the front, posts and privacy pages and their translations by itself).
    protected_ids: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            raise ValueError("the signing secret must be at least 32 characters (run bin/setup)")
        if not self.db_path:
            raise ValueError("WPMCP_DB is required")
        parts = urlsplit(self.public_url)
        if not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError("WPMCP_PUBLIC_URL must be an origin such as https://mcp.example.org")
        if self.public_url.endswith("/"):
            raise ValueError("WPMCP_PUBLIC_URL must not end with a slash")
        if parts.scheme != "https" and not (self.insecure_dev and parts.scheme == "http"):
            raise ValueError("WPMCP_PUBLIC_URL must use https")
        if self.cidr_mode not in CIDR_MODES:
            raise ValueError(f"WPMCP_CIDR_MODE must be one of {', '.join(CIDR_MODES)}")
        if self.cidr_mode != "off" and not self.cidr_file:
            raise ValueError("WPMCP_CIDR_FILE is required unless WPMCP_CIDR_MODE=off")
        if self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("WPMCP_HOST must be a loopback address (the reverse proxy is public)")
        if self.wp_url:
            self._check_wp()
        if self.media_socket and not self.media_socket.startswith("/"):
            raise ValueError("WPMCP_MEDIA_SOCKET must be an absolute path")
        if self.write_budget < 1 or self.upload_ttl < 60 or self.media_max_bytes < 1024:
            raise ValueError(
                "WPMCP_WRITE_BUDGET, WPMCP_UPLOAD_TTL or WPMCP_MEDIA_MAX_BYTES too low"
            )
        if self.live_budget < 0:
            raise ValueError("WPMCP_LIVE_BUDGET cannot be negative (0 turns live changes off)")

    def _check_wp(self) -> None:
        parts = urlsplit(self.wp_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("WPMCP_WP_URL must be an http(s) URL such as https://example.org")
        if parts.query or parts.fragment or self.wp_url.endswith("/"):
            raise ValueError("WPMCP_WP_URL must have no query, fragment or trailing slash")
        # Plain http only when the connection cannot leave the machine.
        local = _is_loopback_host(parts.hostname) or bool(self.wp_connect_ip)
        if parts.scheme == "http" and not local:
            raise ValueError("WPMCP_WP_URL may use http only on loopback")
        if not self.wp_user or not self.wp_password:
            raise ValueError("WPMCP_WP_USER and the Application Password are required")
        if self.wp_connect_ip:
            ip = ipaddress.ip_address(self.wp_connect_ip)
            if not ip.is_loopback:
                raise ValueError("WPMCP_WP_CONNECT_IP must be a loopback address")

    @property
    def issuer(self) -> str:
        return self.public_url

    @property
    def resource(self) -> str:
        return f"{self.public_url}{RESOURCE_PATH}"

    @property
    def public_host(self) -> str:
        return urlsplit(self.public_url).netloc

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        trusted = _list(env.get("WPMCP_TRUSTED_PROXIES"))
        extra = {"trusted_proxy_cidrs": _networks(trusted)} if trusted else {}
        uds = env.get("WPMCP_UDS", "")
        return cls(
            secret=load_secret(env),
            public_url=env.get("WPMCP_PUBLIC_URL", "").rstrip("/"),
            db_path=env.get("WPMCP_DB", ""),
            site_name=env.get("WPMCP_SITE_NAME", cls.site_name),
            access_token_ttl=_int(env.get("WPMCP_ACCESS_TOKEN_TTL"), cls.access_token_ttl),
            refresh_token_ttl=_int(env.get("WPMCP_REFRESH_TOKEN_TTL"), cls.refresh_token_ttl),
            refresh_grace=_int(env.get("WPMCP_REFRESH_GRACE"), cls.refresh_grace),
            code_ttl=_int(env.get("WPMCP_AUTH_CODE_TTL"), cls.code_ttl),
            host=env.get("WPMCP_HOST", cls.host),
            port=_int(env.get("WPMCP_PORT"), cls.port),
            uds=uds,
            trust_unix_peer=_bool(env.get("WPMCP_TRUST_UNIX_PEER"), default=bool(uds)),
            cidr_file=env.get("WPMCP_CIDR_FILE", ""),
            cidr_mode=env.get("WPMCP_CIDR_MODE", cls.cidr_mode).strip().lower(),
            max_body_bytes=_int(env.get("WPMCP_MAX_BODY_BYTES"), cls.max_body_bytes),
            insecure_dev=_bool(env.get("WPMCP_INSECURE_DEV")),
            wp_url=env.get("WPMCP_WP_URL", "").strip(),
            wp_user=env.get("WPMCP_WP_USER", cls.wp_user),
            wp_password=load_wp_password(env),
            wp_connect_ip=env.get("WPMCP_WP_CONNECT_IP", "").strip(),
            wp_timeout=float(env.get("WPMCP_WP_TIMEOUT") or cls.wp_timeout),
            media_socket=env.get("WPMCP_MEDIA_SOCKET", "").strip(),
            media_max_bytes=_int(env.get("WPMCP_MEDIA_MAX_BYTES"), cls.media_max_bytes),
            upload_ttl=_int(env.get("WPMCP_UPLOAD_TTL"), cls.upload_ttl),
            write_budget=_int(env.get("WPMCP_WRITE_BUDGET"), cls.write_budget),
            live_budget=_int(env.get("WPMCP_LIVE_BUDGET"), cls.live_budget),
            protected_ids=frozenset(int(v) for v in _list(env.get("WPMCP_PROTECTED_IDS"))),
            **extra,
        )
