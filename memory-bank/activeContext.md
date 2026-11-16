# Active context

_Updated 2026-11-16._

## Current focus
Release 0.3.0 (bridge 0.4.0) prepares the repository for open source: the bridge has
generic names (`wpmcp-bridge`, `wpmcp/v1`, `wpmcp_`, `WPMCP_BRIDGE_*`), site-specific
fields come through the `wpmcp_bridge_extra_fields` filter (`extra` in `get_content`),
and deployment settings live in a site file outside the repository (`deploy/site.env`
or `SITE_ENV`) from which bin/deploy renders `wpmcp.env` and the Apache vhosts.

Phase 4 (drafts, translations, SEO, categories, images) is complete and has run in
production with ChatGPT. Claude has not been connected yet.

Release 0.4.0 adds `infra/` (Terraform: Lightsail instance + optional Route 53 stack in
any account), support for Lightsail's own WordPress image (`wordpress_ls_1_0`, Debian 12;
`deploy/platform.sh` picks the image) and `bin/bootstrap-site` for a fresh site. Tested
end to end on 2026-11-16 on a throwaway instance (create, DNS, bootstrap, deploy, tools
through MCP over HTTPS, a draft in a second language, the version check, the upload link
with EXIF/GPS removal, redeploy idempotent, no Terraform drift), then destroyed.

## Upgrading a deployment from 0.2.0
The old bridge files (the previous plugin name) must be removed from
`wp-content/mu-plugins` in the same step as the deploy; otherwise both copies load and
register their routes twice. Any field that came from a site's own plugin needs its
filter mu-plugin, deployed through `EXTRA_MU_PLUGINS`.

## Next
- Connect Claude; check `deny_ip` lines, then CIDR `enforce`.
- Phase 5: publish/schedule/unpublish/trash/restore, live edits, protected IDs, digest.
- Dedicated `wpmcp_integration` role (see `docs/security.md`, planned).
- Integration tests in CI (the stack runs under podman locally; docker needs
  `WPMCP_BRIDGE_ALLOWED_IPS` set to the bridge gateway).

## Decisions taken
- 2026-11-16: new servers use Lightsail's own image by default; its quirks are handled in
  bootstrap-site (Host-derived address, no `.htaccess`, no Let's Encrypt client) rather
  than by enabling `.htaccess`. Software is installed by scripts, not Terraform user data,
  so both stay re-runnable.
- 2026-11-16: images are processed by a per-request sandboxed worker (the only component
  with internet access), always re-encoded; no arbitrary-URL import (ChatGPT file hosts
  only). Edits use a `version` fingerprint instead of `expected_modified_gmt`.
- 2026-11-16: open source under MIT ("lightsail-wp-mcp contributors"), published as a
  fresh repository; nothing about a particular deployment is committed (AGENTS rule 7).
