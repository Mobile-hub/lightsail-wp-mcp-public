# lightsail-wp-mcp

A remote MCP server that lets ChatGPT and Claude operate one self-hosted WordPress site
(content, translations, SEO, media) from a conversation. It runs next to WordPress on the
same machine, behind the existing Apache, and carries its own OAuth 2.1 authorization
server for a single owner (password + authenticator code).

```
ChatGPT / Claude ──HTTPS──▶ Apache (mcp.example.org, TLS)
                              └─▶ Unix socket ─▶ wpmcp (systemd, own user, sandboxed)
                                                   ├─ OAuth 2.1: fixed clients, password + TOTP, SQLite
                                                   ├─ typed MCP tools (wp:read / wp:write)
                                                   ├─▶ WordPress REST on loopback, as a least-privilege user
                                                   └─▶ image worker (one sandboxed process per image)
```

Why not a WordPress plugin: OAuth keys and tokens stay out of the WordPress database and
its backups, the WordPress credential only works from loopback, and what the assistant may
do is enforced by WordPress capabilities and a small bridge plugin rather than by tool
code alone. See [`docs/architecture.md`](docs/architecture.md) and
[`docs/security.md`](docs/security.md).

## Status

Early, and built for one owner and one site. Use it as a starting point, read the security
model, and expect to adapt the deployment to your server.

| Phase | What | State |
|---|---|---|
| 1 | Auth core and MCP endpoint | done |
| 2 | WordPress bridge plugin, local stack, read tools | done |
| 3 | Production, read-only | done; verified with ChatGPT, Claude not yet |
| 4 | Drafts, translations, SEO, categories, images | done |
| 5 | Publishing and other live operations | done |
| 6 | Menus | done |

Read tools (`wp:read`): `get_connection_info`, `get_site_overview`, `search_content` (by
language, status, category, missing translations), `get_content` (body, SEO, site-specific
extra fields, `version`; a long body in parts), `list_terms`, `list_media`,
`list_revisions`, `get_menus`, `get_recent_changes`.

Draft tools (`wp:write`), none of which changes what visitors see: `create_draft` (in one
language, optionally linked as a translation, with categories, featured image and SEO),
`update_draft` (drafts and pending items only, against the `version` read),
`create_category`, `import_image` (ChatGPT files), `request_image_upload` (single-use
upload link, any client), `update_media` (title, alt text, caption).

Live tools (`wp:write`), which change what visitors see, each against the `version` read
and undoable: `publish_content`, `schedule_content`, `unpublish_content`,
`update_published_content` (the state before is kept as a revision), `insert_image` (an
image into a body without rewriting it; drafts too), `restore_revision`,
`trash_content` and `restore_from_trash` (never a permanent delete), `link_translations`,
`apply_menu` and `restore_menu` (menus shown by a theme location; the last 5 versions are
kept). They have their own hourly budget, protected pages, and an hourly digest e-mail to
the site's admin address.

Site abilities: abilities of WordPress core or other plugins that the owner lists by name
on the WordPress side become tools of their own, typed by their schema and confined to the
integration user's capabilities ([`docs/operations.md`](docs/operations.md#site-abilities)).
The bridge's own operations are WordPress abilities too (`wpmcp/*`, not public).

Never exposed: users, plugins, themes, options, comments, permanent deletes, media
deletes, files, WP-CLI, SQL or arbitrary URLs.

## Requirements

- **Server:** an AWS Lightsail WordPress instance, either Lightsail's own image
  (`wordpress_ls_1_0`: Debian 12, Apache 2, MariaDB) or the Bitnami one. `infra/` creates a
  new one with Terraform. The site file's `PLATFORM` (`debian` or `bitnami`) picks the
  matching `deploy/platform.<name>.sh` and `deploy/setup.<name>.sh`; nothing is detected.
  Another layout is a new pair of those files.
- **WordPress:** 6.9 or later for the abilities (on older versions the bridge falls back
  to plain routes; not tested);
  tested on 7.1 with **Polylang** 3.9 (free, required) and **All in One
  SEO** 5.0 (SEO fields are absent without it).
- **A host name for the service** (for example `mcp.example.org`) pointing at the server.
- **Locally:** [uv](https://docs.astral.sh/uv/), and podman or docker for the WordPress
  test stack.

## Try it locally

```bash
make setup                               # .venv from the lock file, .env with a generated secret
bin/wpmcp owner set                      # consent password + TOTP secret for your authenticator
bin/wpmcp client add --preset claude     # prints client_id and client_secret once
make serve                               # http://127.0.0.1:8080 (loopback only)
make test                                # ruff, pytest with coverage, shellcheck
bin/dev up                               # local WordPress + Polylang + All in One SEO on 127.0.0.1:8081
```

With the stack up, `make test` also runs the integration tests against it. `bin/wpmcp
token` mints a short-lived read-only access token for smoke tests.

## Deploy

Step by step for a person, from an empty AWS account to ChatGPT and Claude connected and
mail through SES: [`docs/zero-to-ninja.md`](docs/zero-to-ninja.md).

A new server, from nothing ([`infra/README.md`](infra/README.md)):

```bash
terraform -chdir=infra/instance apply -var-file=$HOME/my-site/instance.tfvars
terraform -chdir=infra/dns apply -var-file=$HOME/my-site/dns.tfvars   # optional: Route 53 records
SITE_ENV=$HOME/my-site/site.env bin/setup-server                       # packages, PHP, TLS, vhosts
SITE_ENV=$HOME/my-site/site.env bin/bootstrap-site                     # a fresh site: Polylang, languages
SITE_ENV=$HOME/my-site/site.env bin/deploy
```

Moving a site to a new server (only the database and media are copied; code comes again
from wordpress.org), and outgoing mail through Amazon SES: `bin/migrate-site`,
`infra/mail` and `bin/mail-credentials`, in [`docs/operations.md`](docs/operations.md).

An existing server:

```bash
cp deploy/site.env.example deploy/site.env   # PLATFORM, SSH target, MCP_HOST, WP_HOST, SITE_NAME
bin/setup-server                             # checks (and on Bitnami hardens) the PHP serving the site
bin/deploy
```

`deploy/site.env` is git-ignored; you can also keep it outside the checkout and run
`SITE_ENV=/path/to/site.env bin/deploy`. Then follow
[`docs/operations.md`](docs/operations.md) for the first-time setup and
[`docs/clients.md`](docs/clients.md) to connect Claude and ChatGPT.

Site-specific read-only fields (for example the meta of your own plugin) can be added to
`get_content` from a separate mu-plugin with the `wpmcp_bridge_extra_fields` filter; set
`EXTRA_MU_PLUGINS` in the site file to deploy it with the bridge.

## Where things are

- `src/wpmcp/auth/` — authorization server, store, owner credentials, bearer gate
- `src/wpmcp/tools/` — the tool registry; a tool that is not registered does not exist
- `src/wpmcp/wp/` — the client for the bridge routes
- `src/wpmcp/media/` — image checks and re-encoding, ChatGPT downloads, the image worker, upload links
- `wordpress/mu-plugins/` — the bridge plugin (access rules, `wpmcp/v1` routes)
- `infra/` — Terraform: Lightsail instance, optional Route 53 records
- `deploy/`, `bin/setup-server`, `bin/deploy`, `bin/bootstrap-site` — per-platform files
  (`platform.<name>.sh`, `setup.<name>.sh`, `apache/<name>/`), systemd units, the server
  install and a fresh site's bootstrap
- `dev/`, `bin/dev` — the local WordPress stack for integration tests
- `docs/` — a walkthrough from zero, architecture, security model, operations, clients,
  test prompts for every tool
- `memory-bank/` — current state and working notes of the project
- `AGENTS.md` — rules for anyone (human or agent) changing this repository
- `PROVENANCE.md` — code taken from `editorial.workflow`

## Contributing and security

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Report vulnerabilities privately as described in
[`SECURITY.md`](SECURITY.md), not in public issues.

## License

[MIT](LICENSE). Parts come from
[editorial.workflow](https://github.com/Machine-Learning-Labs/chatgpt-local-mcp-editorial.workflow),
also MIT; see [`PROVENANCE.md`](PROVENANCE.md).

## Similar projects

Other projects meet the same need in more general environments, most as a WordPress plugin
that works on any host. [`docs/alternatives.md`](docs/alternatives.md) describes them,
along with the trade-offs between the two approaches.
