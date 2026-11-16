# Operations

The service runs on the WordPress instance itself, at `https://<MCP_HOST>` (the host name
set in the site file, see below). Two images are supported, named by `PLATFORM` in the
site file (nothing is detected): `debian`, Lightsail's own WordPress image
(`wordpress_ls_1_0`, Debian 12), and `bitnami`. Each has its own files in `deploy/`:
`platform.<name>.sh` (paths and the commands that differ), `setup.<name>.sh` (what
`bin/setup-server` runs) and `apache/<name>/`. A new server can be created with the
Terraform in `infra/`, whose `platform` output is the value for the site file.

## A new server

`infra/README.md`: Terraform for the instance and, optionally, the DNS records. Then:

1. `bin/setup-server` prepares the server. On `debian`: packages up to date and unattended
   security updates, a patched PHP (it refuses one without the October 2026 fixes), the
   site address fixed to `https://WP_HOST`, the site's vhosts, its certificate (`WP_HOST`
   and `WP_ALIASES`) and permalink rules. On `bitnami` it checks and hardens the PHP that
   serves the site; the certificate stays with Bitnami's `bncert-tool`.
2. `bin/bootstrap-site`, for a fresh site only: Polylang, the languages of
   `SITE_LANGUAGES`, All in One SEO, permalinks.
3. `bin/deploy` installs wpmcp.

`bin/setup-server` is idempotent; run it again whenever you like (for a migrated site,
after the DNS switch, to replace the copied certificate with certbot's).

## An existing server

`bin/setup-server` first: it refuses to go on with an unpatched PHP. `bin/inventory`
prints a read-only report of the server (versions, Apache modules, PHP
limits, table charsets, plugins, REST namespaces, file modes, a kses audit). Keep its
output outside the repository. Things worth checking before an assistant gets write
access:

- **Snapshots.** Take one before each deploy that changes the bridge; turn on automatic
  snapshots if the host offers them.
- **Backups in the web root.** Migration plugins (All-in-One WP Migration's `.wpress`
  files) store full copies of the site, including password hashes and 2FA seeds, under
  `wp-content`. Move them out of the web root.
- **TLS private keys.** Bitnami's certificate tool has left the site's key world-readable
  (644), so PHP could read it. `chmod 600` it, and repeat that in the renewal job.
- **Comment moderation.** The service never touches comments, but content written by an
  assistant should not land next to unmoderated ones.
- **kses.** The integration user has no `unfiltered_html`. The inventory's kses audit
  shows what saving existing content without it would change. On the site this was built
  for it was cosmetic only (image attributes WordPress adds back, `&apos;`, whitespace);
  check yours, especially custom markup and embeds.
- **Table charsets.** With utf8mb3 core tables, emoji in titles and content are stored
  as entities and are refused in category names, alt text and SEO fields.
- **Polylang synchronisation** must be off for writes (the bridge refuses them otherwise).

## Layout on the server

| Path | What |
|---|---|
| `/opt/wpmcp/releases/<version>-<timestamp>/venv` | Installed releases (root-owned, last 3 kept) |
| `/opt/wpmcp/current` | Symlink to the running release |
| `/etc/wpmcp/wpmcp.env` | Settings, no secrets |
| `/etc/wpmcp/secret` | Token signing key (root:wpmcp 640), loaded as a systemd credential |
| `/etc/wpmcp/wp-app-password` | Application Password of the `wpmcp` WordPress user (root:wpmcp 640) |
| `/etc/wpmcp/cidrs.txt` | Client egress ranges (`bin/cidrs-update`), re-read on change |
| `/var/lib/wpmcp/wpmcp.sqlite` | Owner, clients, connections (wpmcp 600) |
| `/run/wpmcp/wpmcp.sock` | The service's only listener (directory 710, owner wpmcp, group of Apache: `daemon` on Bitnami, `www-data` on Debian; from `/etc/tmpfiles.d/wpmcp.conf`) |
| `/etc/systemd/system/wpmcp.service` | Sandboxed unit: network to loopback only, no access to WordPress files, keys or the database |
| `/etc/systemd/system/wpmcp-media.socket`, `wpmcp-media@.service` | Image worker: `/run/wpmcp-media.sock` (root:wpmcp 660), one sandboxed process per image, public network only; `wpmcp-media@.service.d/dns.conf` allows the DNS resolver |
| Bitnami: `/opt/bitnami/apache/conf/vhosts/mcp-vhost.conf`, `mcp-https-vhost.conf`; Debian: `/etc/apache2/sites-available/wpmcp.conf`, `wpmcp-ssl.conf` | Apache: ACME + redirect on 80, TLS + proxy to the socket on 443 |
| Bitnami: `/opt/bitnami/letsencrypt/certificates/<MCP_HOST>.*` (lego, `/etc/cron.d/wpmcp-cert`); Debian: `/etc/letsencrypt/live/<MCP_HOST>/` (certbot, its systemd timer) | The service's own certificate and its renewal |
| Debian, from `bin/setup-server`: `/etc/letsencrypt/live/<WP_HOST>/`, `sites-available/wordpress.conf`, `wordpress-ssl.conf` (in place of the image's `000-default`, `default-ssl`), `conf-available/wordpress-permalinks.conf`, `/etc/php/<v>/apache2/conf.d/99-wpmcp.ini`, `/etc/apt/apt.conf.d/20auto-upgrades` | The site's certificate, vhosts, permalink rules, PHP hardening, security updates |
| Bitnami, from `bin/setup-server`: `99-wpmcp.ini` in the ini directory of the PHP-FPM that Apache proxies to | PHP hardening |
| `wp-content/mu-plugins/wpmcp-bridge*` | The bridge plugin, root-owned so PHP cannot modify it |
| `wp-content/mu-plugins/*.php` from `EXTRA_MU_PLUGINS` | Optional site-specific mu-plugins, also root-owned |

## Deploy

Before the first deploy, point the A/AAAA records of `MCP_HOST` at the instance (the
certificate is requested over HTTP-01) and write the site file:

```bash
cp deploy/site.env.example deploy/site.env   # git-ignored; or keep it elsewhere and set SITE_ENV
bin/deploy
```

The site file holds the SSH target and key, `MCP_HOST`, `WP_HOST`, `SITE_NAME`, optional
`EXTRA_MU_PLUGINS`, and `WPMCP_*` overrides of `deploy/wpmcp.env`. Nothing in it is
secret, but it describes your server: keep it out of the repository.

`bin/deploy` runs the test suite, builds the wheel, fetches the client ranges, and runs
`deploy/remote-install.sh` as root on the server. Idempotent. An Apache change that fails
`httpd -t` is undone; a release that fails its health check is rolled back to the previous
one. Take a snapshot of the instance before deploys that change the bridge plugin.

## First-time setup (the owner, in a terminal)

Secrets are shown once, to the terminal that asked for them; do these yourself:

```bash
ssh -t -i <key> <user>@<server>
sudo wpmcp-admin owner set                 # consent password + TOTP secret for your authenticator app
sudo wpmcp-admin client add --preset claude
sudo wpmcp-admin client add --preset chatgpt
```

Then connect each product as described in `clients.md`.

## Day to day

```bash
sudo wpmcp-admin grant list                # connections, last token refresh (about every 15 min while in use)
sudo wpmcp-admin grant revoke <id>
sudo wpmcp-admin revoke-all                # emergency: every connection ends now
sudo wpmcp-admin client list
sudo wpmcp-admin client disable <client_id>
journalctl -u wpmcp -f                     # service log and audit lines (wpmcp.audit)
journalctl -u wpmcp | grep '"wp_write"'     # every change: tool, object id, client, connection
journalctl -u 'wpmcp-media@*'              # image worker errors
journalctl -u wpmcp | grep '"deny_ip"'      # requests from outside the client ranges (monitor mode)
sudo tail -f /opt/bitnami/apache/logs/mcp-access_log   # Debian: /var/log/apache2/mcp-access.log
```

Full stop: `sudo systemctl disable --now wpmcp` (Apache then answers 503 on the
subdomain), or remove the integration user's Application Password in WordPress.

## Limits

`WPMCP_WRITE_BUDGET` (120 write calls per connection and hour), `WPMCP_LIVE_BUDGET` (of
those, 30 that change what visitors see: publishing, live edits, trash, translation links,
menus; 0 turns them off), `WPMCP_MEDIA_MAX_BYTES` (10 MB per image; Apache allows 11 MB on
`/upload/`), `WPMCP_UPLOAD_TTL` (900 s). Override them in the site file
(`WPMCP_WRITE_BUDGET=60`) and deploy.

## Live changes

- **Protected pages.** The front page, posts page and privacy policy page, with their
  translations, are protected by the bridge. Add more with `WPMCP_PROTECTED_IDS=12,34` in
  the site file (the service refuses them), or `WPMCP_BRIDGE_PROTECTED_IDS` in
  `wp-config.php` (the bridge does).
- **Digest.** Every live change is recorded (action, ID, time) and mailed to the site's
  admin e-mail an hour after the first one (WP-Cron), with edit links. WordPress must be
  able to send mail.
- **Undo.** A live edit returns `before` (the revision with the old text, and the old
  categories, image, slug and SEO); `restore_revision` puts text back; trashed items come
  back as drafts; `restore_menu` restores the last saved version of a menu (5 are kept, in
  the `wpmcp_menu_backups` option).

Images are always new uploads; removing one is done in wp-admin (the tools never delete).

## Switching the address allowlist to enforce

After both clients have connected and worked for a while, check that no legitimate call
was logged as `deny_ip`, then add `WPMCP_CIDR_MODE=enforce` to the site file and
deploy. Refresh the list with `bin/cidrs-update` (part of every deploy).

## Smoke test on the server

`sudo wpmcp-admin token --ttl 120` mints a 2-minute read-only token for a local check; use
it on the server and do not copy it elsewhere.
