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
translation links (Polylang), SEO fields (All in One SEO or SEOPress, `seo.php`), drafts (`rest-write.php`), live
changes with their safety nets (`rest-live.php`: protected pages, revisions kept, trash
only, the change log and its hourly digest), menus (`rest-menus.php`: typed items, saved
versions), and a filter (`wpmcp_bridge_extra_fields`) through which a site adds its own
read-only fields. Each bridge operation is defined once and registered both as a route and
as an ability of the WordPress Abilities API (`abilities.php`, `wpmcp/*`, not public); the
route runs the ability, so its input schema, permission check and core lifecycle hooks
apply to every caller. Abilities of core or other plugins that the owner lists
(`site-abilities.php`) are run through two more routes and become tools of their own
(`tools/abilities.py`).

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
   arguments, spends one unit of the connection's hourly write budget for write tools
   (and of the lower live budget for tools that change what visitors see; a bulk call
   one unit per item, a dry run none, all or nothing), writes the audit line, and runs
   the tool.
5. A write tool calls a bridge route; the bridge applies WordPress capabilities, the
   status rules (drafts only, or live only), the protected pages and the version check,
   and answers with the saved item.

## Decisions

| Decision | Why | Alternative considered |
|---|---|---|
| Own service next to WordPress, not a WordPress plugin | OAuth keys and tokens stay out of the WordPress database and its backups; least privilege is enforced by WordPress capabilities, not by plugin code; the official MCP Adapter has no OAuth yet | Official MCP Adapter plus an OAuth plugin |
| Bridge operations as abilities, routes run them | One definition per operation; the standard schema validation, permission check and lifecycle hooks for every caller; WP-CLI, WordPress's AI client or an MCP Adapter server can reuse them; ready for a later move to the Adapter | Plain REST routes only (0.6 and earlier); the service calling `/wp-abilities/v1` (method mapping, permission run twice, abilities would have to be shown in REST) |
| MCP Adapter not used yet | Checked on 2026-11-19: 0.7.1 is pre-1.0 and has no OAuth (hosted ChatGPT and claude.ai cannot connect to it on their own; PR #437 has no scopes or second factor), no Origin check, sessions in user meta, no budgets. Revisit when it reaches 1.0 and OAuth with scopes is in core (targeted for 7.3): the service could then keep consent, gate and budgets in front of an Adapter server listing the `wpmcp/*` abilities | An Adapter custom server behind this service; the Adapter with an OAuth plugin (tokens in the WordPress database) |
| Site abilities listed on the WordPress side, each its own tool | The bridge enforces the list even if the service is compromised; one place to change it; each tool keeps a schema, a scope and budgets | A list in the service; every public ability; one tool that runs any listed ability by name (a generic tool) |
| Bulk changes as "ensure" operations in the bridge, by ID and version | The same change on hundreds of items without a body travelling either way (about 50 tokens an item, against a read and a rewrite of the whole body); an item already as asked is not written, so a repeat or a retry after a partial failure is safe; each item keeps every single-item check (version, lock, protected, status, kses); a batch can be undone | A bulk "replace" taking bodies; per-item calls of `update_published_content` (the token cost and the body truncation risk of 0.6) |
| Public pages read by the service, by content ID | What a visitor and a search engine get (no login, the real theme and SEO output) without opening a generic fetch: the address comes from the bridge, only on the site's own origin, no credentials, no redirects followed; summaries, never the HTML | Rendering in PHP inside the bridge (would run as the integration user); a fetch tool taking a URL (rule 2) |
| One `seo` shape over the SEO plugin the site runs | All in One SEO keeps its fields in its own table through its model, SEOPress in post meta; the tools, versions and bulk changes stay the same whichever is active, and `seo.plugin` says which. With none or both active, a write that sets SEO fields is refused whole instead of saving the rest without them | A tool set per plugin; writing to both |
| SEOPress's content analysis through its own ability, published items only | SEOPress 9.9+ offers the analysis as an ability (`seopress/analyze-post-content`), so the bridge runs it in PHP without widening the integration user's REST allowlist, then answers the counts and each check as capped plain text (about 5 KB). SEOPress scores the page a visitor gets, which it loads from the site: a draft has none without a signed-in session, and the integration user must not have one. That load needs a web server worker while the MCP proxy and the bridge request hold two others, so the service runs one analysis at a time (a second is answered Busy, not queued) and the bridge gives the load 15 s without SEOPress's local-address fallback | The service calling `/seopress/v1/posts/{id}/content-analysis` (a second plugin's REST namespace for the integration user); listing SEOPress's ability as a site ability (its raw answer is the whole page extract with HTML findings); building a draft's HTML in the bridge (the theme's output and head tags would be missing, so the score would differ from SEOPress's) |
| Search Console and site-wide SEO or theme settings not exposed | Search Console needs Google OAuth and internet access from the service, which has none by design: a dedicated connector fits better. AIOSEO's global settings, breadcrumbs and the theme's options are options and themes (never exposed); they are changed in wp-admin and checked with `inspect_page` | A Google token in the service; option writes for the integration user |
| Menus of widgets as well as theme locations | Classic themes show footer and sidebar link lists with Navigation Menu widgets (one per language with Polylang); the bridge reads the widget settings to know which menus are shown, never writes them | `edit_theme_options` for the integration user; a footer tool tied to one theme |
| Same machine | The WordPress credential can be restricted to loopback | Lambda or a second instance (documented fallback; needs the credential to work over the internet) |
| One process, not gateway + upstream | Both halves are ours; scopes cannot be forged between processes; saves memory on a 1 GB instance | The two-process layout of `editorial.workflow` |
| Pre-registered confidential clients | Removes the open registration endpoint | Client ID Metadata Documents (possible later, with pinned metadata) |
| SQLite state | Per-connection revocation, replay detection and a lockout that survives restarts | Stateless signed tokens only |
| Signed access tokens checked against the store | Stateless format, immediate revocation | Opaque tokens |
| `mcp` SDK 1.x (`>=1.30,<2`) | It is the line already working with ChatGPT and Claude in `editorial.workflow`; whether both clients speak the stateless 2026-07-28 revision was not verified | SDK 2.x; revisit in phase 3 with real clients |
| Own bearer gate instead of the SDK's auth middleware | Keeps the dependency on SDK internals to `FastMCP`, `list_tools` and `call_tool` | `TokenVerifier` + `AuthSettings` |
| systemd in production, compose only for local tests | 1 GB of RAM shared with Apache, PHP and MariaDB | Containers in production |
| Image worker started per request by systemd, not a resident process or a PHP download | Untrusted decoding and internet access stay out of the process that holds the WordPress credential; no idle memory; a fresh process per image | Egress for the main service (would let a compromise send the credential out); downloads in PHP with `wp_safe_remote_get` (weaker redirect and rebinding checks, and decoding in PHP) |
| Images from the web only from hosts the owner lists, checked by the worker against its own copy | Book covers come from catalogue and publisher image servers; a fixed list keeps the tool from being a URL fetcher, and a list held by the worker (not sent by the service) means a compromised service cannot reach other hosts through it | Any https address (SSRF and a way to send data out); the list in the request; covers by ISBN from one catalogue (misses most editions) |
| Re-encode every image | Removes metadata (GPS) and any payload in one step; scaling to 2560 px is what WordPress would do anyway | Keep the original bytes after checks (as `editorial.workflow` does, for C2PA) |
| `version` fingerprint for edits | `post_modified` has one-second resolution and SEO changes do not touch it | `expected_modified_gmt` (the original plan) |
| Upload links stored hashed in SQLite, not signed tokens | Single use across restarts, revocable with the connection, nothing readable in Apache's access log | Signed tokens with an in-memory used list (`editorial.workflow`) |

## Limits

- Single process, single SQLite file: no horizontal scaling, by design.
- Rate limits other than the consent lockout are in memory and reset on restart.
- Stateless transport: no server-initiated messages, no resumable streams.
