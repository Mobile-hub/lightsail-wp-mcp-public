# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, minor versions may break things).

## [Unreleased]

### Changed
- **Breaking:** the site file must say `PLATFORM=debian` or `PLATFORM=bitnami`; nothing
  is detected any more. Each platform has its own files: `deploy/platform.<name>.sh`,
  `deploy/setup.<name>.sh` and `deploy/apache/<name>/`. Shared server code is in
  `deploy/lib.remote.sh`. `infra/instance` outputs `platform`.
- `bin/bootstrap-site` only bootstraps a fresh site's content (Polylang, languages) on
  either platform; the server part moved to `bin/setup-server`.
- `bin/inventory` takes its paths from the platform file, so it works on both images.

### Added
- `bin/setup-server`. On `debian`: packages up to date and unattended security updates;
  the site's own vhosts (aliases from `WP_ALIASES` redirected, security headers) in place
  of the image's defaults; certificates that can start as copies and are replaced by
  certbot once the name points at the server. On both platforms: refuses a PHP without
  the PHP2Shell fixes (CVE-2026-109618/109619) and turns `session.upload_progress` off
  (CVE-2026-115560).
- `infra/dns`: other records (`records`, or the mail stack's), `allow_overwrite` to take
  over records made by hand. `infra/instance`: SSH from Lightsail's browser console.

## [0.4.0] - 2026-11-16

### Added
- `infra/`: Terraform for a Lightsail WordPress instance (static IPv4, IPv6, firewall with
  SSH limited to given ranges, optional daily snapshots) and an optional, separate stack
  for Route 53 records in any account.
- Support for Lightsail's own WordPress image (`wordpress_ls_1_0`, Debian 12) next to the
  Bitnami one: `deploy/platform.sh` detects the image; certificates come from certbot.
- `bin/bootstrap-site`: prepares a fresh Lightsail image (fixed `https://` site address
  instead of one derived from the Host header, Let's Encrypt certificate, permalink rules
  in Apache's configuration, Polylang, All in One SEO, languages).
- `DEPLOY_KNOWN_HOSTS`, `ACME_EMAIL` and `SITE_LANGUAGES` in the site file.
- CI: `terraform fmt` and `validate`; shellcheck and PHP lint cover `deploy/`.

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
