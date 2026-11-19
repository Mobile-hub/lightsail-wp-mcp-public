# Progress

## Done
- 2026-11-15 — Phase 1: OAuth 2.1 server, bearer gate, stateless MCP endpoint, CLI, audit.
- 2026-11-15 — Phase 2: bridge mu-plugin (fixed capabilities, loopback-only Application
  Password for the integration user only, no interactive login, REST allowlist, read
  routes with languages, translations and AIOSEO fields); WordPress client with loopback
  pinning; nine read tools; unit and integration tests against the local stack.
- 2026-11-15 — Phase 3: production install for the Bitnami WordPress image (Apache vhosts,
  own Let's Encrypt certificate, sandboxed systemd unit, CIDR list in monitor mode).
  ChatGPT connected with a pre-registered client and used the read tools; every request
  came from OpenAI's published ranges.
- 2026-11-16 — Phase 4 (0.2.0): draft tools, bridge write routes, image worker, upload
  links, write budget, unknown arguments refused. 369 tests, 99% coverage.
- 2026-11-16 — 0.3.0: generic names, extra-fields filter, site file for deploys, licence,
  SECURITY/CONTRIBUTING/CHANGELOG, CI and Dependabot.

- 2026-11-16 — 0.4.0: `infra/` Terraform, Lightsail's own image, `bin/bootstrap-site`;
  tested end to end on a throwaway instance and destroyed.

- 2026-11-16 — 0.5.0: phases 5 and 6 (live tools, menus, live budget, protected pages,
  digest); one set of files per platform and `bin/setup-server`. 411 tests, 99% coverage.

- 2026-11-16 — Unreleased: `bin/migrate-site`, `infra/mail`, `bin/mail-credentials`,
  Apache hardening; rehearsed on a real site, then used for a real migration.
- 2026-11-17 — `docs/zero-to-ninja.md`, the walkthrough for a person (console, ChatGPT,
  Claude, SES sandbox and production access).
- 2026-11-18 — `docs/prompts.md`: test prompts that call every tool and the refusals,
  for trying the tools against the real clients (see "Not done").
- 2026-11-19 — ChatGPT: `import_image` with generated images works against the real
  product. Editing long pages failed ("the connector does not return the version"): its
  tool-result budget dropped the body line and the `version` after it. The failing pages
  had about 18,000 characters; edits of the same pages worked the day before, when
  ChatGPT took the version from write results (no body) instead of get_content.
- 2026-11-19 — 0.6.0 (bridge 0.6.0): `insert_image`, `get_content` in parts with the
  version first, the shrink check (`allow_shrink`). Unit tests and 39 integration tests
  against the local stack.
- 2026-11-19 — 0.6.1: after "refresh tools" ChatGPT saw `insert_image`, but `get_content`
  still reached the model as `{}` even for 1,000 characters: ChatGPT hides results with a
  `content` field. Renamed to `body` (and the overview's `counts`).
  This also explains the day before (write results have no `content` field), so the size
  was probably not the cause; the 0.6.0 safeguards stay.

- 2026-11-19 — 0.7.0 (bridge 0.7.0): analysis of the MCP Adapter and the Abilities API
  (decision rows in `docs/architecture.md`); output schemas and structured content,
  idempotency hints; bridge operations as abilities; owner-listed site abilities as tools.
  448 tests, 99% coverage, on the local stack (WordPress 7.1.5).

- 2026-11-19 — 0.8.0 (bridge 0.8.0): bulk links and SEO with undo, per-item budgets,
  `inspect_page`, `check_sitemap`, menus of widgets and `add_menu_items`; the local stack
  gains a widget area (dev-only mu-plugin) and footer menus. 480 tests, 99% coverage.

- 2026-11-19 — 0.9.0 (bridge 0.9.0): SEO through SEOPress or All in One SEO, refused
  without exactly one; `.htaccess` off and `intl`/`imagick` on the Debian image. Found in
  production: a cache plugin's `.htaccess` took every permalink and `/wp-json` down, and
  AIOSEO's setup wizard installs its vendor's other plugins.

## Not done
- Connecting Claude.
- Trying the live tools against the real products (ChatGPT, Claude).
- Checking the ChatGPT menu path in `docs/zero-to-ninja.md` against the product after each
  UI change (OpenAI moves it).
- Integration tests in CI.

## Known gaps
- Claude's part of `docs/clients.md` is unverified.
- ChatGPT's tool-result budget is undocumented: `DEFAULT_PART` (8,000 characters) is a
  guess with margin. If long pages still fail, lower it before anything else.
- `insert_image` has not yet been tried against the real products.
- The bulk tools, `inspect_page` and `check_sitemap` have not yet been tried against the
  real products; the public reader has only been tried against the local stack.
