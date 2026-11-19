# Active context

_Updated 2026-11-19._

## Current focus
0.8.0 (bridge 0.8.0), not yet deployed: bulk "ensure" changes without bodies
(`bulk_add_links`, `bulk_update_seo`, `undo_bulk_change`; `rest-bulk.php`,
`tools/bulk.py`), budgets that count each item (`ToolSpec.cost`, `SlidingWindow.allow(count=)`),
`search_content` with versions, `inspect_page` and `check_sitemap` reading the public site by
content ID (`wp/public.py`, `tools/public.py`), menus of Navigation Menu widgets (a classic
theme's footer) in `get_menus`/`apply_menu`/`restore_menu`, and `add_menu_items`. Prompted
by ChatGPT adding the same link to many posts one read-and-rewrite at a time.
Search Console and AIOSEO/theme site-wide settings deliberately not exposed
(`docs/architecture.md`). A site deployment can add read-only abilities that select its
own posts (a custom field) with versions, so the bulk tools need no reads.

Then, 0.7.0 (bridge 0.7.0) after reviewing WordPress's MCP Adapter (0.7.1, pre-1.0, no OAuth)
and the Abilities API (core since 6.9, read abilities only): nothing replaced, three
levers taken. Tools declare `outputSchema` + `structuredContent` and `idempotentHint`;
every bridge operation is a `wpmcp/*` ability that its route runs (`abilities.php`);
abilities the owner lists on the WordPress side become tools (`site-abilities.php`,
`tools/abilities.py`). Revisit replacing the service's MCP layer when the Adapter reaches
1.0 and OAuth with scopes lands in core (targeted 7.3). Not yet deployed.

Before 0.7.0: 0.6.0 (bridge 0.6.0) fixes editing long posts and pages from ChatGPT, which cuts long
tool results at a line boundary and so lost the body and the `version` after it:
`get_content` answers the version first and a long body in parts, a much shorter body is
refused without `allow_shrink`, and `insert_image` places an image without sending the
body; 0.6.1 renames the body field to `body`, since ChatGPT shows any result with a
`content` field as `{}`. Next: try `insert_image` and a long page edit from ChatGPT against production; if
parts still get cut, lower `DEFAULT_PART` in `tools/read.py`.

Release 0.5.0 (bridge 0.5.0) completes the editing features: live tools (publish,
schedule, unpublish, live edits with the prior state kept as a revision, restore
revision, trash/untrash, link translations) and menus (apply/restore with saved
versions), with a separate live budget, protected pages and an hourly digest e-mail.
Tested against the local stack (411 tests, 99% coverage).

Deployment is per platform with no detection: `PLATFORM` in the site file picks
`deploy/platform.<name>.sh`, `deploy/setup.<name>.sh` and `deploy/apache/<name>/`;
`bin/setup-server` prepares a server (patched-PHP gate for PHP2Shell, hardening ini, on
Debian packages/unattended upgrades/vhosts/certificates); `bin/bootstrap-site` is only for
a fresh site's content.

Released with 0.6.0: `bin/migrate-site` (no code carried over; database and media
only; credentials and sessions ended; certificates copied for testing before the DNS
switch), `infra/mail` + `bin/mail-credentials` (SES via WP Mail SMTP's free SMTP mailer,
constants outside the database), Apache hardening on Lightsail's own image. Rehearsed on
2026-11-16: a real Bitnami site copied to Lightsail's own image with matching counts and
pages; the Bitnami path was tested on a throwaway instance and 0.5.0 deployed on Bitnami.

`docs/zero-to-ninja.md` (2026-11-17) is the human walkthrough; it only orders what the
reference docs say, so keep it in step with `infra/README.md`, `operations.md` and
`clients.md` when they change.

Lessons from real servers: fresh Bitnami images ship an unpatched PHP (8.4.20 in
November 2026), no python3-venv, and no lego/ACME webroot until bncert-tool runs; a rewrite
flush without plugins loses Polylang's language prefixes; a new IAM key needs up to a
minute before SES SMTP accepts it.

Earlier: 0.3.0 made the repository generic for open source (bridge `wpmcp-bridge`,
`wpmcp/v1`, `wpmcp_bridge_extra_fields`, site file); 0.4.0 added `infra/` and Lightsail's
own image, tested end to end on a throwaway instance.

## Upgrading a deployment from 0.2.0
The old bridge files (the previous plugin name) must be removed from
`wp-content/mu-plugins` in the same step as the deploy; otherwise both copies load and
register their routes twice. Any field that came from a site's own plugin needs its
filter mu-plugin, deployed through `EXTRA_MU_PLUGINS`.

## Next
- Connect Claude; check `deny_ip` lines, then CIDR `enforce`.
- Release the migration and mail tooling (0.6.0) after a real DNS switch.
- Dedicated `wpmcp_integration` role (see `docs/security.md`, planned).
- Integration tests in CI (the stack runs under podman locally; docker needs
  `WPMCP_BRIDGE_ALLOWED_IPS` set to the bridge gateway).

## Decisions taken
- 2026-11-19: bulk changes are "ensure", never "replace": an item already as asked is
  answered unchanged and not written, every item keeps the single-item checks, a call is
  capped at 50 items and counts one budget unit per item (refused whole if over). Public
  pages are fetched by the service, by content ID only (never a URL argument), without
  credentials, on the site's own origin. Menus shown by widgets count as shown menus; the
  widget settings are read, never written.
- 2026-11-16: no platform detection; the site file names the platform and each platform
  has its own files. Menus are changed without `edit_theme_options`: the bridge checks the
  menu (assigned to a location) and types the items itself. Live changes are separate
  tools with their own budget; the model is told to use them only on the user's explicit
  go-ahead, and the server enforces budget, protected pages, digest and undo paths.
- 2026-11-16: new servers use Lightsail's own image by default; its quirks are handled in
  bootstrap-site (Host-derived address, no `.htaccess`, no Let's Encrypt client) rather
  than by enabling `.htaccess`. Software is installed by scripts, not Terraform user data,
  so both stay re-runnable.
- 2026-11-16: images are processed by a per-request sandboxed worker (the only component
  with internet access), always re-encoded; no arbitrary-URL import (ChatGPT file hosts
  only). Edits use a `version` fingerprint instead of `expected_modified_gmt`.
- 2026-11-16: open source under MIT ("lightsail-wp-mcp contributors"), published as a
  fresh repository; nothing about a particular deployment is committed (AGENTS rule 7).
