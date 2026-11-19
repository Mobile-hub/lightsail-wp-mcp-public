# AGENTS.md

Instructions for AI coding agents and for people working on this repository. `CLAUDE.md`
points here.

## What this project is

A remote MCP server for one WordPress site and one owner, reachable from ChatGPT and
Claude. One Python process holds the OAuth 2.1 authorization server and the MCP endpoint;
a small WordPress mu-plugin (`wordpress/mu-plugins/wpmcp-bridge`) provides what core REST
lacks. Read `docs/architecture.md` and `docs/security.md` before changing anything in
`src/wpmcp/auth/`.

## Commands

- `make setup` creates `.venv` from `uv.lock` and a local `.env`.
- `make test` runs ruff check, ruff format --check, pytest with coverage and shellcheck.
- `make lock` after any change to `pyproject.toml`; commit `uv.lock` and
  `deploy/requirements.txt` together.
- Test scripts against a throwaway env file (`ENV_FILE=/tmp/x.env bin/setup`), never the
  real `.env`.

## Rules

1. **Every change under `src/wpmcp/auth/` needs tests,** and every security branch (PKCE,
   redirect matching, token type, expiry, audience, scopes, lockout, rotation, revocation)
   needs a failing-path test. Keep coverage of `auth/` at or above 90%.
2. **No generic tools.** Each tool is typed, does one thing and is registered in
   `tools/` with its scope and annotations. Never add a tool that takes a REST path, SQL,
   a shell command, PHP, or an arbitrary URL to fetch. `import_image_from_url` takes an
   address, but only on the owner's image hosts (`WPMCP_IMAGE_HOSTS`), which the image
   worker checks against its own copy; never widen that to any host. The one exception
   to registering in `tools/` is site abilities (`tools/abilities.py`): each becomes its
   own tool, only by exact name from the owner's WordPress-side list, typed by its own
   schema. There is no tool that runs an ability by name, and there must not be.
3. **Never exposed:** users, plugins, themes, options, comments, form entries, permanent
   delete, media delete, file edits, WP-CLI.
4. **Security controls are not optional.** Do not weaken any of these without an entry in
   `docs/security.md` and the memory bank: pre-registered clients only (no dynamic
   registration), exact redirect URIs, password + TOTP consent and its lockout, audience
   and database binding of tokens, refresh rotation with reuse detection, the scope check
   in `WpMcp.call_tool`, loopback-only listeners, `proxy_headers=False` and the
   last-hop rule in `net.py`, per-route body caps, hash-pinned dependencies.
5. **Keep the runtime dependency list short** and crypto in the standard library.
6. **Never print or commit secrets.** `.env` and `var/` are git-ignored. Secrets shown by
   the CLI (`owner set`, `client add`) are shown once, to a terminal.
7. **Nothing about one deployment in the repository.** Host names, IP addresses, SSH
   keys, account ids, server inventories and content counts live in the site file
   (`deploy/site.env`, git-ignored, or wherever `SITE_ENV` points) or outside the
   repository. Examples use `example.org` and documentation addresses (`203.0.113.0/24`).
   Site-specific WordPress fields go through the `wpmcp_bridge_extra_fields` filter in a
   separate mu-plugin, never into the bridge.
8. **Production is not a test bed.** Anything that changes the server, WordPress or AWS is
   confirmed with the owner first. `bin/inventory` is read-only and must stay so.
9. Audit lines record who did what to which object, never argument contents.
10. Write docs, code comments and commit messages in English. Use Conventional Commits.

## Before you finish a task

- `make test` is green.
- `memory-bank/activeContext.md` and `memory-bank/progress.md` are current; `docs/` is
  updated if behaviour changed.
