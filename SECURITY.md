# Security

This project puts an endpoint on the public internet that can write to a WordPress site.
The threat model and every control are in [docs/security.md](docs/security.md); read it
before deploying.

## Assumptions

- One owner, one site, one server. The service and WordPress share the machine; the
  WordPress credential is only accepted from loopback.
- The owner keeps the consent password and the authenticator secret to themselves, and
  approves only consent requests they started.
- The server itself (SSH, the OS, the other WordPress plugins) is maintained separately.
  This project narrows what an assistant can do through WordPress; it does not harden the
  host.

## Recommendations

- Connect with **Read only** unless you need to write; a read-only connection never sees
  the write tools.
- Keep the confirmation prompts of Claude and ChatGPT switched on for write tools.
- Review `journalctl -u wpmcp | grep '"wp_write"'` from time to time, and
  `sudo wpmcp-admin grant list` for connections you do not recognise.
- After both clients have worked for a while, switch the address allowlist to `enforce`
  (`docs/operations.md`).
- `sudo wpmcp-admin revoke-all` if you suspect a token or a device has leaked.
- Keep dependencies pinned: after changing `pyproject.toml`, run `make lock` and commit
  the result.

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please open a private security advisory on GitHub (*Security → Report a vulnerability*)
instead of a public issue. Include the version, the steps to reproduce and what an
attacker gains. We aim to answer within 7 days.
