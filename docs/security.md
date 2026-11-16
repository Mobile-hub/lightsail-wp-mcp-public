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
| A manipulated model asks for something dangerous | No generic tools; users, plugins, themes, options, deletes and code are not reachable at all | `tools/` |
| A model writes something visitors see | Write tools create drafts only and edit only drafts and pending items; a draft linked as a translation stays out of the language switcher and hreflang until published (Polylang shows only translations the visitor can read); publishing does not exist yet | `rest-write.php`, `tools/write.py` |
| A tool argument the model believes was applied (`status="publish"`) | Unknown arguments are refused for every tool instead of silently dropped | `mcp_server.py` |
| A runaway loop of changes | Write budget per connection and hour (`WPMCP_WRITE_BUDGET`, 120); reads continue | `mcp_server.py` |
| Two edits of the same draft overwrite each other | `update_draft` needs the `version` it was based on (a fingerprint of every editable field, since `post_modified` has one-second resolution and SEO edits do not touch it); refused while someone has the editor open | `rest-write.php` |
| Saving a draft changes its published translations | Writes are refused while Polylang synchronisation is enabled | `rest-write.php` |
| Script or markup injection in content | The integration user has no `unfiltered_html`: WordPress's kses filter applies, and the tool tells the model when it changed the content | `access.php`, `rest-write.php` |
| A malicious image (decoder exploit, decompression bomb, polyglot, hidden payload) | Size and header pixel caps before decoding; decoder restricted to the sniffed format; every image re-encoded from its pixels (metadata such as GPS positions removed); all of it in the image worker, not in the service | `media/images.py`, `media/worker.py` |
| `import_image` used to fetch arbitrary URLs (SSRF) | Host allowlist of ChatGPT's file hosts, https only, every resolved address public, redirects re-checked; the worker's unit denies loopback, private and link-local destinations at the kernel level | `media/fetch.py`, `deploy/systemd/wpmcp-media@.service` |
| A leaked upload link | Random, stored hashed, single use, 15 min, dies with the connection that asked for it and with read-only grants; one image, into the media library only | `media/uploads.py`, `auth/store.py` |
| Clickjacking or script injection on the consent page | `frame-ancestors 'none'`, `default-src 'none'`, escaped output, `no-store` | `app.py`, `auth/pages.py` |
| A compromised dependency | Hash-pinned lock, `--require-hashes` install | `uv.lock`, `deploy/requirements.txt` |
| The WordPress credential is stolen | Accepted only from loopback, only for the integration user; that user cannot log in, reset its password or reach any REST route outside `wpmcp/v1` | `wordpress/mu-plugins/wpmcp-bridge/access.php` |
| WordPress grants the integration user more than intended (roles, other plugins) | Its capabilities are a fixed list that replaces the role's; no `unfiltered_html`, options, users, plugins, themes, menus or SEO admin | `access.php` |
| An admin's Application Password, which would skip the 2FA login | Only the integration user may use Application Passwords | `access.php` |
| The bridge plugin is modified through PHP | Installed root-owned; the web server user cannot write it | `deploy/remote-install.sh` |
| The service is compromised | systemd sandbox: own user, network to loopback only, read-only system, no access to WordPress files, TLS keys, the database or home directories, no capabilities, syscall filter, 256 MB memory cap; it never decodes an image | `deploy/systemd/wpmcp.service` |
| The image worker is compromised (through a decoder bug) | One fresh process per image under a throwaway user, no credentials, no access to wpmcp's files, WordPress or its database, no loopback or private network, killed after 120 s; what it returns is only bytes the service uploads as an image | `deploy/systemd/wpmcp-media@.service` |
| Secrets in configuration files or backups | Signing key and Application Password are systemd credentials in `/etc/wpmcp` (root:wpmcp 640), outside WordPress and its backups | `deploy/` |

## Kill switches

- `wpmcp grant revoke <id>` ends one connection; `wpmcp revoke-all` ends all of them. Both
  take effect on the next request.
- `wpmcp client disable <client_id>` blocks a client and revokes its connections.
- Rotating the signing secret invalidates every access token and pending consent request.

## Planned (phases 5 and 6)

- A dedicated `wpmcp_integration` role with only the listed capabilities, so the user stays
  narrow even if the bridge plugin were removed (today its stored role is `editor`).
- Publishing, unpublishing and trash (never permanent delete), revisions with a restore
  tool, a journal of the previous state of terms, translation links and menus.
- A list of protected pages, and a digest e-mail of publications and trashes.

## Accepted risks

- **Prompt injection through site content.** Text read from WordPress goes back to the
  model. The server tells the model to treat it as data, but that is advice, not a
  boundary. The boundary is what the tools cannot do. With a write connection, a
  manipulated model can still edit, publish or trash content; revisions, the trash and
  server snapshots are the recovery path.
- **The clients' confirmation prompts can be switched off** by the user in Claude or
  ChatGPT. They are a brake, not a control this server enforces.
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
- **The owner's TOTP secret and password hash live in the SQLite file.** Protect the file
  like a credential (mode 600, service user only) and keep it out of web-readable backups.

## Reporting

Report vulnerabilities privately as described in [`SECURITY.md`](../SECURITY.md). A fixed
finding is recorded here (threat, control, where) together with its test.
