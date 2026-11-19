# System patterns

Stable decisions and their reasons live in `docs/architecture.md` and
`docs/security.md`. This file records the patterns to follow when adding code.

- **One enforcement point per concern.** Authentication: `auth/bearer.py`. Scope:
  `WpMcp.call_tool`. Client address: `net.py`. Do not re-implement any of them in a tool.
- **Fail closed.** No principal in the request means no tools listed and none run.
- **State changes are separate tools.** Creating a draft never publishes; publishing is
  its own call.
- **Tools are registered with scope and annotations** (`tools/__init__.py`). Read tools
  return capped excerpts; only the "get one item" tool returns full bodies.
- **Time is injected** (`OAuthServer(clock=...)`, `now=` on store methods) so tests control it.
- **Secrets at rest are hashed** (codes, refresh tokens, client secrets: SHA-256 of a
  256-bit random value; owner password: scrypt).
- **A database was added on purpose** (unlike `editorial.workflow`): revocation, replay
  detection and a persistent lockout need it. One SQLite file, one process.
- **Untrusted bytes never reach the service process.** Images are decoded, downloaded and
  re-encoded in the image worker (`media/worker.py`, one systemd-activated process per
  request); the service only forwards bytes and uploads the result.
- **Drafts-only writes are enforced in the bridge**, not only in tool code: the bridge
  forces `post_status=draft` on create and refuses edits of anything but draft/pending.
- **Edits name the version they were based on** (`version` from `get_content`).
- **Write results omit the body** the caller just sent and add `notes` for anything that
  could not be carried over or that WordPress changed.
- **Small fields first, the body last, long bodies in parts.** ChatGPT cuts long tool
  results at a line boundary and the JSON body is one line, so anything after it is lost.
  Keep `version`/`content_length` at the top of every content answer, keep parts under
  `DEFAULT_PART`, and prefer tools that change a body without sending it (`insert_image`)
  over "read all, rewrite all". The shrink check backs this up.
- **One table per bridge file, two registrations.** `wpmcp_*_operations()` defines each
  operation once (route, callback, permission, args, label, annotations); `abilities.php`
  registers the route and the `wpmcp/*` ability, and the route runs the ability. Add an
  operation there, then the matching tool in `tools/` with the same annotations (an
  integration test compares them).
- **Site abilities are the only dynamic tools.** Listed by exact name on the WordPress
  side, each its own typed tool, never a "run ability by name" tool (AGENTS.md rule 2).
- **No field named `content` in a tool result.** ChatGPT shows such a result as `{}` to
  the model (reported by others, confirmed here on 2026-11-19 with `get_content`). The body
  is `body`; the overview's counts are `counts`. Input arguments named `content` are fine.
