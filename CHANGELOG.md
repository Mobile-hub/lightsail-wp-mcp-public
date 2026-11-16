# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, minor versions may break things).

## [0.3.0] - 2026-11-16

### Changed
- The bridge plugin is now `wpmcp-bridge` with the REST namespace `wpmcp/v1`, the
  function prefix `wpmcp_` and constants `WPMCP_BRIDGE_*` (were site-specific names).
  Remove the old plugin files from `wp-content/mu-plugins` when upgrading.
- `get_content` returns site-specific fields under `extra`, filled by the
  `wpmcp_bridge_extra_fields` filter, instead of a built-in field for one site's plugin.
- Deployment settings (SSH target, host names, site name, overrides) come from a site
  file (`deploy/site.env`, git-ignored; `SITE_ENV` to keep it elsewhere). Apache vhosts
  and `wpmcp.env` are rendered from templates. `EXTRA_MU_PLUGINS` deploys site
  mu-plugins next to the bridge.

## [0.2.0] - 2026-11-16

### Added
- Draft tools: `create_draft`, `update_draft`, `create_category`, `import_image`,
  `request_image_upload`, `update_media`.
- Bridge write routes, drafts only, with translation linking, SEO through All in One SEO,
  and edits checked against a `version` fingerprint and the editor lock.
- Image worker: one sandboxed process per image (socket activation), re-encoding every
  image and downloading ChatGPT files from an allowlist of hosts.
- Single-use upload links (`/upload/{token}`) and a write budget per connection.

### Changed
- Unknown tool arguments are refused instead of silently dropped.

## [0.1.0] - 2026-11-15

### Added
- OAuth 2.1 authorization server with pre-registered clients, password + TOTP consent,
  refresh rotation with reuse detection and per-connection revocation.
- Stateless MCP endpoint behind a bearer gate, with an optional client address allowlist.
- Bridge mu-plugin: least-privilege integration user, loopback-only Application Password,
  REST allowlist, language-aware read routes.
- Read tools, the local WordPress test stack, and the production install for the Bitnami
  WordPress image (systemd, Apache, Let's Encrypt).
