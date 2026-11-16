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

## Not done
- Connecting Claude.
- Phases 5 and 6.
- Integration tests in CI.

## Known gaps
- Claude's part of `docs/clients.md` is unverified.
- `import_image` through ChatGPT's file parameter has not yet been tried against the real
  product.
