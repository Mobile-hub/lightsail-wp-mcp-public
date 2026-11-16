# Active context

_Updated 2026-11-16._

## Current focus
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

Next in line: deploy 0.5.0 to a Bitnami server (test a throwaway instance first, since
the Bitnami path changed), then migrate a Bitnami site to Lightsail's own image in
another account (instance, SES mail stack, content migration, DNS cutover).

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
- Deploy 0.5.0; a Bitnami throwaway test first.
- `bin/migrate-site` and the `infra/mail` stack (SES), then a migration.
- Dedicated `wpmcp_integration` role (see `docs/security.md`, planned).
- Integration tests in CI (the stack runs under podman locally; docker needs
  `WPMCP_BRIDGE_ALLOWED_IPS` set to the bridge gateway).

## Decisions taken
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
