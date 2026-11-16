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
