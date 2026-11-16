# Contributing

Thanks for helping. Before you open a pull request:

1. Run `make setup`, then `make test`. Ruff, pytest and shellcheck must all pass.
2. If you touched the bridge plugin, the WordPress client or a tool, start the local
   stack (`bin/dev up`, podman or docker) and run `make test` again: the integration tests
   run against it. Say which engine you used.
3. Add tests for any change under `src/wpmcp/auth/`. Every security branch needs a test
   that exercises the failing case.
4. If you touched `pyproject.toml`, run `make lock` and commit `uv.lock` and
   `deploy/requirements.txt` together.
5. Use Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
6. Update `docs/` for any behaviour change, and `memory-bank/` for any decision or change
   in progress.

Never commit anything that describes one deployment: host names, IP addresses, server
inventories, SSH keys or content counts belong in your site file (`deploy/site.env`,
git-ignored) or outside the repository.

New tools are welcome when they are typed, do one thing and fit the security model; a
tool that takes a REST path, SQL, a shell command, PHP or an arbitrary URL will not be
accepted. Read [AGENTS.md](AGENTS.md) for the layout and the rules.
