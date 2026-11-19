# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (before 1.0, minor versions may break things).

## [Unreleased]

## [0.10.0] - 2026-11-19

### Added
- `import_image_from_url` (`wp:write`): an image from its web address into the media
  library, re-encoded like `import_image`, only from the image hosts the owner lists in
  `WPMCP_IMAGE_HOSTS` (a name, `.domain` for its subdomains, `img*.domain`; at most 20; no
  IP addresses or bare endings). Without the setting the tool does not exist. Other hosts
  are refused before anything is sent, and the image worker checks the address and every
  redirect again against its own copy of the list, written by `remote-install.sh` to
  `wpmcp-media@.service.d/hosts.conf`, so the service cannot widen it. The tool's
  description lists the hosts, and the server instructions point to it. The bridge is
  unchanged (0.9.0).

## [0.9.0] - 2026-11-19

SEO fields through SEOPress as well as All in One SEO, and two server fixes found in
production.

### Added
- Bridge 0.9.0 (`seo.php`): the `seo` fields of every tool come from the SEO plugin the
  site runs, All in One SEO (its table) or SEOPress (post meta: title, description, the
  first target keyword as the focus keyphrase with the others kept, Facebook title and
  description, canonical, noindex and nofollow), with `seo.plugin` naming it. The site
  overview has `seo_plugin` and SEOPress's version. `check_sitemap` also looks for
  SEOPress's `/sitemaps.xml` when robots.txt names none.

### Changed
- A write that sets SEO fields while no SEO plugin is active, or both are, is refused
  whole (`wpmcp_no_seo`, `wpmcp_seo_conflict`); before, the rest was saved and the SEO
  fields dropped with only a note. Item versions change once on upgrade (`seo.plugin` is
  part of what they fingerprint).
- Local stack: SEOPress is installed inactive; integration tests switch to it, and to no
  SEO plugin, and back.
- `bin/setup-server` on Lightsail's own image (Debian) installs the PHP modules
  WordPress's Site Health lists as optional: `intl` and `imagick` (Debian's ImageMagick
  policy refuses PostScript, PDF and XPS input).

### Fixed
- Lightsail's own image (Debian): `.htaccess` is now really off for the site
  (`AllowOverride None` next to the permalink rules). The image allows it under
  `/var/www`, so a plugin's `.htaccess` with rewrite rules of its own (LiteSpeed Cache's)
  replaced the permalink rules: every permalink, the sitemap and the REST API (the block
  editor, Site Health, All in One SEO, the wpmcp bridge) answered 404. It also let PHP
  change the web server's configuration, which `docs/security.md` said it could not.

## [0.8.0] - 2026-11-19

Bulk changes that do not move bodies, checks of what search engines see, and the menus of
widgets (a classic theme's footer). Prompted by a client adding the same link to many
posts one read-and-rewrite at a time.

### Added
- `bulk_add_links`: one block with a link to a published post or page, added to up to 50
  posts or pages per call where the body does not link to it yet (at the end, or inside a
  body's closing wrapper tags). Bodies are neither read nor sent; each item is checked
  against its `version` (from `search_content`), the editor lock, protected items, its
  language and what kses would strip, and refused on its own; `dry_run` shows the plan.
- `bulk_update_seo`: All in One SEO fields of up to 50 items; only the fields that differ
  are written.
- `undo_bulk_change`: puts back a batch (the last 10 are kept) item by item, skipping
  items changed since. Link changes keep a revision of each item first.
- `inspect_page`: a published item's public page as a visitor gets it, summarised: status
  and redirect, title, description, robots, canonical, hreflang, Open Graph, JSON-LD types,
  H1 headings, and `checks` naming problems. By content ID only, never an address.
- `check_sitemap`: the XML sitemaps (from robots.txt, or `/sitemap.xml`) and the published
  items they leave out, noindex ones apart.
- `add_menu_items`: adds items to a menu and keeps the others; items already in it are
  skipped. The previous version is saved for `restore_menu`.
- `search_content` answers each item's `version`.
- Bridge 0.8.0: `wpmcp/bulk-add-links`, `wpmcp/bulk-update-seo`, `wpmcp/undo-bulk-change`
  and `wpmcp/add-menu-items` abilities; an internal `/links` route (public addresses of
  published items); the digest has one line per batch with its IDs.

### Changed
- `get_menus`, `apply_menu` and `restore_menu` also cover menus shown by Navigation Menu
  widgets in the theme's widget areas (`widget_area`, `widget_area_name`, `widget_title`,
  and the widget's Polylang language), not only theme locations. Which menu a widget
  shows is never changed.
- Budgets count changes, not calls: a bulk call counts one per item against both budgets
  (a dry run none), and a call that would go over is refused whole, saying how many are
  left. A call refused by the live budget no longer spends the write budget.
- Local stack: the bridge files are mounted one by one, next to a dev-only mu-plugin that
  registers a widget area; `bin/dev provision` adds footer menus as widgets. Run
  `bin/dev up` once to recreate the container.

### Not added
- Search Console (needs Google OAuth and internet access from the service) and AIOSEO's or
  the theme's site-wide settings (options and themes are never exposed): see
  `docs/architecture.md`.

## [0.7.0] - 2026-11-19

The bridge moves onto WordPress's Abilities API, and owner-listed abilities of core or
other plugins become tools. The MCP Adapter is not used yet: it has no OAuth, so hosted
ChatGPT and claude.ai cannot reach it on their own (see `docs/architecture.md`).

### Added
- Every tool declares an `outputSchema` (an object) and answers `structuredContent`
  next to the JSON text, as OpenAI's MCP documentation recommends.
- `idempotentHint` on every tool: true for reads and for changes guarded by
  `expected_version` (a repeat is refused as stale), false for creations, uploads,
  `restore_menu` and `restore_from_trash`.
- Bridge: every operation is also an ability of the WordPress Abilities API
  (`wpmcp/get-content`, `wpmcp/update-draft`, ... 23 in all, category `wpmcp`), defined
  once per file with its route. The routes run the ability, so every caller goes through
  the same input schema, permission check and core lifecycle hooks; WP-CLI, WordPress's AI
  client or an MCP Adapter server can use them too. They are not public (no
  `/wp-abilities/v1` listing, not on the Adapter's default server). On WordPress before
  6.9 the routes call the callbacks directly.
- Site abilities: abilities of WordPress core or other plugins that the owner lists on the
  WordPress side (`WPMCP_BRIDGE_ABILITIES` in `wp-config.php` or the
  `wpmcp_bridge_abilities` filter; none by default, at most 20) become typed tools named
  after them (`core/get-user-info` → `core_get_user_info`), with their own input schema.
  Read-only ones need `wp:read`; any other needs `wp:write`, counts against the live
  budget, is marked destructive and goes into the digest. They run as the integration user
  (bridge routes `GET /abilities`, `POST /abilities/run`), so its fixed capabilities still
  apply. Fields named `content` in their results are answered as `body`.

## [0.6.1] - 2026-11-19

### Fixed
- ChatGPT showed `get_content` as `{}`: it hides a JSON tool result that has a field named
  `content`. The body is now answered as `body`, and `get_site_overview`'s post counts as
  `counts` (bridge 0.6.1). No tool result has a `content` field any more.
  This, rather than the size of the body, is the likely cause of the failure 0.6.0 set
  out to fix: write results, which have no `content` field, always reached the model. The
  0.6.0 changes (parts, version first, `insert_image`, shrink check) stay as safeguards.

## [0.6.0] - 2026-11-19

Long posts and pages could not be edited from ChatGPT: it cuts long tool results at a line
boundary, the JSON body is one line, and `version` came after it, so both were lost and
the client refused to save ("the connector does not return the version").

### Added
- `insert_image`: puts an image from the media library into a post or page body (at the
  start, at the end, or after the one block that contains a given text) without the body
  travelling either way; the rest of the body is kept byte for byte. Alt text per use, in
  the item's language. On a published item it is a live edit (revision first, `before`,
  digest, protected pages refused); it counts against the live budget for drafts too.
- `get_content` returns a long body in parts (`offset`, `max_chars`, 8,000 characters by
  default) with `content_length`, `content_offset`, `content_complete` and `next_offset`.
- `update_draft` and `update_published_content` refuse a body less than half as long as
  the current one (of 1,000 characters or more) unless `allow_shrink` is true: the usual
  sign of a body read or kept only in part.
- `bin/migrate-site`: moves a site to a new server prepared with `bin/setup-server`,
  copying only the database and the media library (no code: core, plugins and themes come
  again from wordpress.org at the same versions), ending every Application Password and
  session, and copying the current certificates for testing before the DNS switch.
- `infra/mail` and `bin/mail-credentials`: outgoing mail through Amazon SES (Easy DKIM,
  MAIL FROM, DMARC; an IAM user limited to the From addresses), with SMTP credentials made
  locally and installed as WP Mail SMTP constants outside the database; a Reply-To
  mu-plugin.
- `docs/zero-to-ninja.md`: a step-by-step walkthrough for a person, from an empty AWS
  account to ChatGPT and Claude connected and mail through SES.
- On Lightsail's own image, `bin/setup-server` denies `xmlrpc.php` and version files,
  stops PHP from running in uploads, and sets `ServerTokens Prod`.
- On Lightsail's own image, `bin/setup-server` removes world write access under the web
  root (the image leaves five directories 777) and group write access to `/var/www`.

### Changed
- `get_content` and every write result answer `id`, `version`, `being_edited` and
  `content_length` first and the body last. Bridge 0.6.0, needed by the server (new route
  `/content/{id}/insert-image`, `allow_shrink`).
- The server instructions describe the parts of a long body and `insert_image`.

### Fixed
- `bin/inventory`'s fallback to `DEPLOY_*` no longer applies to other prefixes.
- `bin/migrate-site` lists only regular plugins when it removes the image's extras.
- After the DNS switch, a rewrite flush with the plugins loaded (Polylang's prefixes).

## [0.5.0] - 2026-11-16

### Added
- Live tools (phase 5): `publish_content`, `schedule_content`, `unpublish_content`,
  `update_published_content`, `restore_revision`, `trash_content`, `restore_from_trash`,
  `link_translations`. Each change names the version it was based on and is refused while
  the editor is open; the state before a live edit or restore is saved as a revision and
  returned; trash only. Protected pages: front, posts and privacy pages and their
  translations, plus `WPMCP_PROTECTED_IDS` / `WPMCP_BRIDGE_PROTECTED_IDS`.
- Menus (phase 6): `apply_menu` and `restore_menu` for menus shown by a theme location,
  with typed items, the language switcher kept, and the last 5 versions saved. No
  `edit_theme_options` for the integration user. `get_menus` returns `version` and item
  kinds.
- A separate hourly budget for live changes (`WPMCP_LIVE_BUDGET`, 30) and an hourly
  digest e-mail of live changes to the site's admin address.

### Fixed
- Grant IDs never start with `-`, which `wpmcp-admin grant revoke` read as an option.
- Bitnami: python3-venv is installed when missing (fresh images lack it), and a server
  where bncert-tool never ran (no lego, no ACME webroot) gets a clear error instead of a
  failed certificate request.

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
