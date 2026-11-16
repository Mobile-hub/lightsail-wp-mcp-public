# Architecture

## Shape

One Python process (`wpmcp serve`) on the WordPress instance, listening on loopback or a
Unix socket behind the existing Apache, on its own subdomain:

- `/.well-known/oauth-protected-resource[/mcp]`, `/.well-known/oauth-authorization-server[/mcp]`
- `/authorize` (consent screen), `/token`
- `/mcp` (Streamable HTTP, stateless, POST only), behind the bearer gate
- `/upload/{token}` (single-use image upload links from `request_image_upload`)

State is one SQLite file (`auth/store.py`). The service reaches WordPress over HTTPS on
loopback as a least-privilege user, only through the `wpmcp/v1` routes of the bridge
mu-plugin (`wordpress/mu-plugins/`), which add what core REST lacks: languages and
translation links (Polylang), SEO fields (All in One SEO), drafts-only writes, and a
filter (`wpmcp_bridge_extra_fields`) through which a site adds its own read-only fields.

Images take a separate path. The service never decodes them: it hands the bytes (or, for
ChatGPT files, the download URL) to the image worker over a Unix socket. systemd starts one
worker process per request (`wpmcp-media.socket`, `Accept=yes`), sandboxed with no
credentials and only public network access; it validates, downloads and re-encodes, and
returns a clean file that the service uploads through the bridge.

```
ChatGPT / Claude ──HTTPS──▶ Apache ──unix──▶ wpmcp serve ──https 127.0.0.1──▶ WordPress (bridge)
                                               │
                                               └──unix──▶ wpmcp media-worker (per image) ──▶ ChatGPT file host
```

## Request path for a tool call

1. `GuardMiddleware` adds security headers.
2. `McpGate` checks method, client address, rate, bearer token (signature, type, expiry,
   issuer, audience, database id, live grant, enabled client) and declared body size, then
   puts the `Principal` in the ASGI scope and strips `Authorization`, `Cookie` and `Origin`.
3. The MCP transport checks `Host` and parses the message.
4. `WpMcp.call_tool` checks the tool's scope against the principal, refuses unknown
   arguments, spends one unit of the connection's hourly write budget for write tools,
   writes the audit line, and runs the tool.
5. A write tool calls a bridge route; the bridge applies WordPress capabilities, the
   drafts-only rules and the version check, and answers with the saved item.

## Decisions

| Decision | Why | Alternative considered |
|---|---|---|
| Own service next to WordPress, not a WordPress plugin | OAuth keys and tokens stay out of the WordPress database and its backups; least privilege is enforced by WordPress capabilities, not by plugin code; the official MCP Adapter has no OAuth yet | Official MCP Adapter plus an OAuth plugin |
| Same machine | The WordPress credential can be restricted to loopback | Lambda or a second instance (documented fallback; needs the credential to work over the internet) |
| One process, not gateway + upstream | Both halves are ours; scopes cannot be forged between processes; saves memory on a 1 GB instance | The two-process layout of `editorial.workflow` |
| Pre-registered confidential clients | Removes the open registration endpoint | Client ID Metadata Documents (possible later, with pinned metadata) |
| SQLite state | Per-connection revocation, replay detection and a lockout that survives restarts | Stateless signed tokens only |
| Signed access tokens checked against the store | Stateless format, immediate revocation | Opaque tokens |
| `mcp` SDK 1.x (`>=1.30,<2`) | It is the line already working with ChatGPT and Claude in `editorial.workflow`; whether both clients speak the stateless 2026-07-28 revision was not verified | SDK 2.x; revisit in phase 3 with real clients |
| Own bearer gate instead of the SDK's auth middleware | Keeps the dependency on SDK internals to `FastMCP`, `list_tools` and `call_tool` | `TokenVerifier` + `AuthSettings` |
| systemd in production, compose only for local tests | 1 GB of RAM shared with Apache, PHP and MariaDB | Containers in production |
| Image worker started per request by systemd, not a resident process or a PHP download | Untrusted decoding and internet access stay out of the process that holds the WordPress credential; no idle memory; a fresh process per image | Egress for the main service (would let a compromise send the credential out); downloads in PHP with `wp_safe_remote_get` (weaker redirect and rebinding checks, and decoding in PHP) |
| Re-encode every image | Removes metadata (GPS) and any payload in one step; scaling to 2560 px is what WordPress would do anyway | Keep the original bytes after checks (as `editorial.workflow` does, for C2PA) |
| `version` fingerprint for edits | `post_modified` has one-second resolution and SEO changes do not touch it | `expected_modified_gmt` (the original plan) |
| Upload links stored hashed in SQLite, not signed tokens | Single use across restarts, revocable with the connection, nothing readable in Apache's access log | Signed tokens with an in-memory used list (`editorial.workflow`) |

## Limits

- Single process, single SQLite file: no horizontal scaling, by design.
- Rate limits other than the consent lockout are in memory and reset on restart.
- Stateless transport: no server-initiated messages, no resumable streams.
