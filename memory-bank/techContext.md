# Tech context

- Python >= 3.11, uv, hatchling, ruff (with the `S` rules), pytest. `mcp` SDK 1.x
  (`>=1.30,<2`), Starlette, uvicorn, httpx. Crypto from the standard library.
- Target server: the Bitnami WordPress image (as on Lightsail): Debian 12, systemd 252,
  Python 3.11, Bitnami Apache 2.4 (runs as `daemon`), PHP 8.2 FPM, MariaDB 11.4. Built
  against WordPress 7.1.4, Polylang 3.9.2 free, AIOSEO 5.0.5.1, on a site whose core
  tables are utf8mb3. Deployment specifics stay in the site file, outside the repository.
- Bitnami's `wp` wrapper must be started as root (`sudo wp ...`); it drops to `daemon`.
- systemd's `RuntimeDirectory=` resets the directory's group on every exec: the socket
  directory comes from tmpfiles.d instead.
- Under rootless podman, requests to a published port reach the container from its own
  address; `dev/compose.yml` adds that address to `WPMCP_BRIDGE_ALLOWED_IPS`.
- WP-CLI can create Application Passwords for any user (it skips the availability
  filter); the bridge still refuses to authenticate them.

## Verified on the local stack (2026-11-15: WordPress 7.1.4, Polylang 3.9.2, AIOSEO 5.0.5.1)
- Polylang free does **not** filter core REST collections by `?lang=`: `/wp/v2/posts?lang=es`
  returns posts in every language. Language-aware listing must come from the bridge plugin.
- The post REST schema has no `lang` or `translations` fields; it does have
  `aioseo_meta_data`, `_aioseop_title` and `_aioseop_description` (as a real site does).
- Application Passwords are offered only over HTTPS, or when `WP_ENVIRONMENT_TYPE` is
  `local` (set in `dev/compose.yml`).
- `podman-compose run` recreates services listed in `depends_on`, which killed the
  database; the `wpcli` service therefore has no `depends_on` and runs with `--no-deps`.

## Learned while building phase 4 (2026-11-16)
- With utf8mb3 core tables WordPress stores emoji in title, excerpt and content as
  `&#x1f43b;` entities; post meta and term names silently lose 4-byte characters. The dev
  stack converts its core tables to utf8mb3 (`dev/charset.php`) so tests see the same.
- A new draft has `post_modified_gmt` (and `post_date_gmt`) at zero until its first
  update: derive GMT from `post_modified` (`wpmcp_modified_gmt`).
- `post_modified` has one-second resolution and AIOSEO-only saves do not touch it; edits
  are checked against a fingerprint (`version`) instead.
- AIOSEO 5.0.5.1 `Models\Post::savePost` applies patches (missing keys keep their value);
  the focus keyphrase lives in the `focus_keyword` column, older rows only in `keyphrases`.
- Polylang outputs hreflang and switcher links only for translations the visitor can read,
  so a linked draft stays invisible. Writes need synchronisation (`sync`) off.
- FastMCP drops unknown tool arguments silently; `WpMcp.call_tool` refuses them.
- On Lightsail the DNS resolver is the VPC resolver (a private address, from
  `/etc/resolv.conf` → `/run/systemd/resolve/resolv.conf`); remote-install.sh writes it into
  a drop-in so the image worker may reach that one private address.
- After a long-running session the dev container's bind mount of `wordpress/mu-plugins`
  showed an empty directory; `podman restart wpmcp-dev_wordpress_1` fixed it.
