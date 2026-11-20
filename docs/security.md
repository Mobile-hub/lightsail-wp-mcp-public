# Security

One owner, one WordPress site, two hosted clients (Claude, ChatGPT). The server can
write to a public site, so the question for every control is: what can a
stranger, a stolen credential, or a manipulated model do?

Sections marked **planned** describe controls that are designed but not built yet.

## Threats and controls

| Threat | Control | Where |
|---|---|---|
| A stranger calls the MCP endpoint | Bearer token required; 401 with a discovery challenge | `auth/bearer.py` |
| A stranger registers a client and phishes the owner for consent | No registration endpoint; clients are created on the server with `wpmcp client add` and are confidential | `auth/oauth.py`, `cli.py` |
| A stolen authorization code | Single use, 120 s, bound to client and redirect URI, PKCE S256, client secret required; a replay revokes what the code produced | `auth/oauth.py`, `auth/store.py` |
| Guessing the consent password | scrypt hash, TOTP second factor, one generic error, 5 failures per address per 10 min and 30 overall per hour, persisted across restarts | `auth/oauth.py`, `auth/owner.py` |
| A replayed TOTP code | Each time step is accepted once | `auth/store.py` |
| A stolen refresh token | Rotated on every use; reuse after a 60 s grace window revokes the whole connection | `auth/store.py` |
| A stolen access token | 15 min lifetime; bound to this resource, this issuer and this database; checked against a live grant on every request, so revocation is immediate | `auth/oauth.py` |
| A token minted for another server, or after the database was replaced | Audience, issuer and database id are verified | `auth/oauth.py` |
| A read-only connection tries to write | Scope checked in one place before any tool runs; write tools are hidden from read-only connections | `mcp_server.py` |
| Requests from outside the clients' networks | Optional CIDR allowlist on `/mcp` and `/token` (off, monitor, enforce) | `auth/cidrs.py` |
| Spoofed `X-Forwarded-For` | Only the last hop, only from the local proxy; uvicorn's own proxy handling is off | `net.py`, `cli.py` |
| Request floods and oversized bodies | Per-address rate limits; body caps on every route | `app.py`, `auth/bearer.py` |
| A manipulated model asks for something dangerous | No generic tools (no tool runs a REST path, SQL, PHP, a command or an ability by name); users, plugins, themes, options, deletes and code are not reachable at all | `tools/` |
| An ability the owner listed for wpmcp does more than intended | Nothing is listed by default; only exact names from `WPMCP_BRIDGE_ABILITIES` or the `wpmcp_bridge_abilities` filter (never `wpmcp/*`, at most 20), each its own typed tool with the ability's schema, unknown arguments refused; it runs as the integration user, so its fixed capabilities decide (an ability that needs `manage_options` is refused); anything not marked read-only needs `wp:write`, counts against the live budget, is marked destructive and goes into the digest. It may have no version check or way back: list only abilities whose effect is understood | `site-abilities.php`, `tools/abilities.py` |
| A model writes something visitors see | Draft tools create drafts and edit only drafts and pending items; a draft linked as a translation stays out of the language switcher and hreflang until published (Polylang shows only translations the visitor can read). Live changes are separate tools, marked destructive (except publish and schedule) so clients ask first, and the server instructions tell the model to use them only on the user's explicit go-ahead | `rest-write.php`, `rest-live.php`, `tools/` |
| A live change goes wrong (publish, edit, trash, menu) | Every one is undoable: the state before a live edit or a restore is saved as a revision (and returned with the old categories, image, slug and SEO); trash only, never a permanent delete; untrash gives a draft; the last 5 versions of each menu are kept for `restore_menu` | `rest-live.php`, `rest-menus.php` |
| A runaway or manipulated model changes the live site in bulk | A second, lower budget for live changes per connection and hour (`WPMCP_LIVE_BUDGET`, 30; 0 turns them off); the site's admin e-mail gets an hourly digest of every live change (action, ID, time, edit link; no content) | `mcp_server.py`, `rest-live.php` |
| Key pages are unpublished, trashed or rewritten | The front page, posts page and privacy policy page, their translations, `WPMCP_BRIDGE_PROTECTED_IDS` (wp-config) and `WPMCP_PROTECTED_IDS` (service) cannot be unpublished, trashed, edited live or relinked through the tools | `rest-live.php`, `tools/live.py` |
| Menus are used to reach theme settings | Only menus a theme location or a Navigation Menu widget of a registered widget area shows can be changed, never which menu a location or widget shows (the widget settings are read, not written); items are typed (published post or page, category in the menu's language, https or site-relative link, an existing item kept); no `edit_theme_options`, so the Customizer, widgets and theme options stay out of reach | `rest-menus.php` |
| A bulk change goes wrong on many items at once | Each item keeps every single-item check (its version, the editor lock, protected items, its status) and is refused on its own; items already as asked are not written; at most 50 per call; each item counts against both hourly budgets and a call that would go over is refused whole; `dry_run` shows the plan without writing; the batch is kept (the last 10) and `undo_bulk_change` puts back every item not changed since (link changes keep a revision of each item first); one digest line per batch with the IDs | `rest-bulk.php`, `tools/bulk.py`, `mcp_server.py` |
| Saving a body as the integration user strips markup an administrator added (an iframe, a script) | A bulk link change counts the elements kses would remove from the whole new body and refuses the item if any would go | `rest-bulk.php`, `helpers.php` |
| `inspect_page` or `check_sitemap` used to fetch other sites (SSRF) or to send the credential | They take content IDs, not addresses: the bridge gives each published item's permalink, and the public reader accepts only the site's own origin (scheme, host, port), connects to the loopback address in production, sends no credentials or cookies, follows no redirects, caps sizes (3 MB a page, 10 MB a sitemap, 50 sitemaps) and answers summaries, not HTML or XML; sitemaps on other hosts are skipped | `tools/public.py`, `wp/public.py` |
| `analyze_seopress_content` used to load other pages, to send the credential, or to see drafts | It takes a content ID and runs SEOPress's own ability (`seopress/analyze-post-content`), never `/seopress/v1` (the integration user's REST allowlist is unchanged); only published items without a password, so the page SEOPress loads is one any visitor gets. SEOPress loads the item's own address, follows redirects only within the site and forwards only the caller's cookies, of which the integration user, signed in by Application Password, has none. Its own limit (30 analyses a minute per user) applies; a refusal by SEOPress's role settings is kept. The only write is SEOPress's stored analysis of that item, as when its editor runs it. Findings come back as capped plain text | `seo.php`, `tools/read.py` |
| Translation links are corrupted | `link_translations` keeps existing links, checks each item's language, and refuses two items for one language | `rest-live.php` |
| A tool argument the model believes was applied (`status="publish"`) | Unknown arguments are refused for every tool instead of silently dropped | `mcp_server.py` |
| A runaway loop of changes | Write budget per connection and hour (`WPMCP_WRITE_BUDGET`, 120, counting each item of a bulk call); reads continue | `mcp_server.py` |
| Two edits of the same item overwrite each other | Every change to an existing item or menu needs the `version` it was based on (a fingerprint of every editable field, since `post_modified` has one-second resolution and SEO edits do not touch it); refused while someone has the editor open | `rest-write.php`, `rest-live.php`, `rest-menus.php` |
| A client that saw only part of a long body replaces it with that part (ChatGPT cuts long tool results at a line boundary) | `get_content` answers the version and the length first and a long body in parts (`content_complete`, `next_offset`); a body less than half as long as the current one is refused without `allow_shrink`; `insert_image` adds an image without the body travelling at all, keeping the rest byte for byte and refusing when it could not | `tools/read.py`, `rest-write.php` |
| Saving a draft changes its published translations | Writes are refused while Polylang synchronisation is enabled | `rest-write.php` |
| Script or markup injection in content | The integration user has no `unfiltered_html`: WordPress's kses filter applies, and the tool tells the model when it changed the content | `access.php`, `rest-write.php` |
| A malicious image (decoder exploit, decompression bomb, polyglot, hidden payload) | Size and header pixel caps before decoding; decoder restricted to the sniffed format; every image re-encoded from its pixels (metadata such as GPS positions removed); all of it in the image worker, not in the service | `media/images.py`, `media/worker.py` |
| `import_image` used to fetch arbitrary URLs (SSRF) | Host allowlist of ChatGPT's file hosts, https only, every resolved address public, redirects re-checked; the worker's unit denies loopback, private and link-local destinations at the kernel level | `media/fetch.py`, `deploy/systemd/wpmcp-media@.service` |
| `import_image_from_url` used to fetch other addresses (SSRF), or by a manipulated model to send data out in an address (a query string to a server its author watches) | The tool exists only when the owner lists image hosts (`WPMCP_IMAGE_HOSTS`, at most 50, each a named site: no IP addresses, no whole domain endings such as `.org`); the service refuses other hosts before anything is sent, and the worker checks the address and every redirect again against its own copy of the list (a systemd drop-in written at deploy; the request names a URL, never hosts, so a compromised service cannot widen it); then the same https, public-address, size and re-encoding rules as `import_image`. List only image servers of catalogues, publishers and booksellers, never a host where anyone can publish files or read who fetched them (file sharing, generic cloud storage or shop platforms such as `cdn.shopify.com`, URL shorteners) | `tools/write.py`, `media/worker.py`, `config.py`, `deploy/remote-install.sh` |
| A leaked upload link | Random, stored hashed, single use, 15 min, dies with the connection that asked for it and with read-only grants; one image, into the media library only | `media/uploads.py`, `auth/store.py` |
| Clickjacking or script injection on the consent page | `frame-ancestors 'none'`, `default-src 'none'`, escaped output, `no-store` | `app.py`, `auth/pages.py` |
| A compromised dependency | Hash-pinned lock, `--require-hashes` install | `uv.lock`, `deploy/requirements.txt` |
| The WordPress credential is stolen | Accepted only from loopback, only for the integration user; that user cannot log in, reset its password or reach any REST route outside `wpmcp/v1` | `wordpress/mu-plugins/wpmcp-bridge/access.php` |
| WordPress grants the integration user more than intended (roles, other plugins) | Its capabilities are a fixed list that replaces the role's; no `unfiltered_html`, options, users, plugins, themes, `edit_theme_options` or SEO admin | `access.php` |
| An admin's Application Password, which would skip the 2FA login | Only the integration user may use Application Passwords | `access.php` |
| The bridge plugin is modified through PHP | Installed root-owned; the web server user cannot write it | `deploy/remote-install.sh` |
| The service is compromised | systemd sandbox: own user, network to loopback only, read-only system, no access to WordPress files, TLS keys, the database or home directories, no capabilities, syscall filter, 256 MB memory cap; it never decodes an image | `deploy/systemd/wpmcp.service` |
| The image worker is compromised (through a decoder bug) | One fresh process per image under a throwaway user, no credentials, no access to wpmcp's files, WordPress or its database, no loopback or private network, killed after 120 s; what it returns is only bytes the service uploads as an image | `deploy/systemd/wpmcp-media@.service` |
| Secrets in configuration files or backups | Signing key and Application Password are systemd credentials in `/etc/wpmcp` (root:wpmcp 640), outside WordPress and its backups | `deploy/` |
| Lightsail's own image derives the site address from the request's `Host` header, over http (poisoned links in e-mails, mixed content) | `bin/setup-server` fixes `WP_HOME`/`WP_SITEURL` to `https://WP_HOST` | `deploy/setup.debian.sh` |
| PHP rewrites the web server's configuration through `.htaccess` (the web root is writable by the web server on that image) | `.htaccess` stays disabled; the permalink rules are in Apache's own configuration, root-owned | `deploy/setup.debian.sh` |
| An unpatched PHP serves the site (PHP2Shell: CVE-2026-109618/109619, unauthenticated code execution) | `bin/setup-server` refuses a PHP older than 8.2.35 / 8.3.36 / 8.4.27 / 8.5.12 (on Bitnami, the FPM that Apache actually proxies to); turns on unattended security updates on Debian; `session.upload_progress` off (CVE-2026-115560) | `deploy/setup.*.sh`, `deploy/lib.remote.sh` |
| A migration carries a backdoor over | `bin/migrate-site` copies no code: core, plugins and themes come again from wordpress.org at the same versions (other code only from a reviewed directory, root-owned), the media library is copied without code or `.htaccess`, and uploads cannot run PHP on the new server | `deploy/migrate.sh`, `deploy/setup.debian.sh` |
| Copied credentials and sessions keep working on the new server | Every Application Password and login session is ended by the migration; the new server has its own salts and an empty wpmcp database | `deploy/migrate.sh` |
| WordPress's mail credentials are stolen through PHP | The SES user may only send raw mail through the site's identity and only from the listed From addresses; the credentials are constants in `/etc/wp-mail/smtp.php` (root, web server group read-only), outside the web root and the database; `bin/mail-credentials` rotates them | `infra/mail`, `deploy/mail-install.sh` |
| PHP replaces `wp-config.php` or writes into world-writable directories | On Lightsail's own image, `bin/setup-server` removes world write access under the web root and the web server's group write access to the directory of `wp-config.php` | `deploy/setup.debian.sh` |
| XML-RPC, version disclosure | On Lightsail's own image: `xmlrpc.php`, `readme.html`, `license.txt` denied; `ServerTokens Prod` | `deploy/setup.debian.sh` |
| SSH open to the internet on a new server | The Terraform firewall opens 22 only to the ranges given (`ssh_cidrs`); 80 and 443 are the only other open ports | `infra/instance/main.tf` |

## Kill switches

- `wpmcp grant revoke <id>` ends one connection; `wpmcp revoke-all` ends all of them. Both
  take effect on the next request.
- `wpmcp client disable <client_id>` blocks a client and revokes its connections.
- Rotating the signing secret invalidates every access token and pending consent request.

## Planned

- A dedicated `wpmcp_integration` role with only the listed capabilities, so the user stays
  narrow even if the bridge plugin were removed (today its stored role is `editor`).

## Accepted risks

- **Prompt injection through site content.** Text read from WordPress goes back to the
  model. The server tells the model to treat it as data, but that is advice, not a
  boundary. The boundary is what the tools cannot do. With a write connection, a
  manipulated model can still edit, publish or trash content; revisions, the trash and
  server snapshots are the recovery path.
- **The clients' confirmation prompts can be switched off** by the user in Claude or
  ChatGPT. They are a brake, not a control this server enforces. The same holds for the
  "explicit go-ahead" in the server instructions: advice to the model, while the budget,
  the protected pages, the digest and the undo paths are what the server enforces.
- **Menu changes delete the old menu items** (nav_menu_item posts, not content) after
  saving them; only the last 5 versions of each menu can be restored with the tool.
- **The digest e-mail depends on WordPress being able to send mail** and on WP-Cron
  (visitor-triggered unless a system cron runs it).
- **Images are shared by every language and post.** `update_media` changes alt text and
  captions wherever the image appears, published posts included; it is marked
  destructive so clients ask first. Uploads only add new images, never replace one.
- **Whoever holds an unused upload link can add one image** to the media library (not to
  a post) within its 15 minutes. Links are given only to the owner, in the conversation.
- **New images are public files as soon as they are uploaded** (anyone with the URL can
  open them), as with any WordPress upload; drafts themselves need a login to preview.
- **The CIDR allowlist is not authentication:** other users of the same products share
  those addresses.
- **Consent lockout can be triggered by others.** Anyone who knows a client id can fail
  the consent form 30 times an hour and lock new approvals for that hour. Existing
  connections are unaffected.
- **Without the bridge plugin the integration user is a plain editor** whose Application
  Password works from anywhere. The plugin is root-owned and must-use, so only root can
  remove it; the planned dedicated role closes this.
- **Requests without a host name** (no `Host` header or no SNI, e.g. old scanners) reach
  the MCP vhost, because it must sort before WordPress's catch-all vhost. They get the
  same 401/404 answers as anyone else.
- **On Lightsail's own image, the initial WordPress admin password is also the database
  password**, kept in `/opt/aws/wordpress/credentials.log`, which `wp-config.php` reads
  and the web server can therefore read too. Change the admin password after
  `bin/setup-server`; the database password stays readable by PHP, as on any WordPress.
- **The owner's TOTP secret and password hash live in the SQLite file.** Protect the file
  like a credential (mode 600, service user only) and keep it out of web-readable backups.

## Reporting

Report vulnerabilities privately as described in [`SECURITY.md`](../SECURITY.md). A fixed
finding is recorded here (threat, control, where) together with its test.
