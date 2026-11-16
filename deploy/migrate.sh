#!/bin/bash
# Runs ON THE SOURCE or THE TARGET server, as root, for bin/migrate-site, from a staging
# directory that bin/migrate-site put in /run/wpmcp-migrate (tmpfs, root only):
#   sudo bash migrate.sh <staging dir> <command>
# Source commands only read: inventory, dump, uploads, certs.
# Target commands: prepare, import, uploads, certs, finalize, inventory.
# Data streams on stdin/stdout; messages go to stderr. Prints no secrets.
set -euo pipefail

# shellcheck source=deploy/lib.remote.sh
. "$(dirname "$0")/lib.remote.sh" "$1"
# shellcheck source=deploy/platform.debian.sh
. "$S/platform.sh"
platform_check
command="$2"
say() { printf '%s\n' "$*" >&2; }
wpq() { wp --skip-themes --skip-plugins "$@" 2>/dev/null; }
uploads_dir() { echo "$(readlink -f "$WP/wp-content")/uploads"; }

# What the site is made of, and counts to compare afterwards (JSON on stdout).
inventory() {
  # shellcheck disable=SC2016 # PHP, not shell
  wp eval '
    $plugins = array();
    foreach ( get_plugins() as $file => $data ) {
      $plugins[] = array( "slug" => dirname( $file ) === "." ? basename( $file, ".php" ) : dirname( $file ), "file" => $file, "version" => $data["Version"], "active" => is_plugin_active( $file ) );
    }
    $themes = array();
    foreach ( wp_get_themes() as $slug => $theme ) {
      $themes[] = array( "slug" => $slug, "version" => $theme->get( "Version" ) );
    }
    $counts = array();
    foreach ( array( "post", "page", "attachment", "nav_menu_item", "wp_block" ) as $type ) {
      $counts[ $type ] = array_filter( (array) wp_count_posts( $type ) );
    }
    global $wpdb;
    $tables = $wpdb->get_col( "SHOW TABLES" );
    echo wp_json_encode( array(
      "wordpress"  => get_bloginfo( "version" ),
      "home"       => home_url(),
      "prefix"     => $wpdb->prefix,
      "path"       => untrailingslashit( ABSPATH ),
      "stylesheet" => get_stylesheet(),
      "template"   => get_template(),
      "plugins"    => $plugins,
      "themes"     => $themes,
      "mu_plugins" => array_keys( get_mu_plugins() ),
      "dropins"    => array_keys( get_dropins() ),
      "locales"    => array_values( array_unique( array_filter( array_merge( array( get_option( "WPLANG" ) ), function_exists( "pll_languages_list" ) ? pll_languages_list( array( "fields" => "locale" ) ) : array() ) ) ) ),
      "tables"     => $tables,
      "counts"     => $counts + array(
        "users"      => count_users()["total_users"],
        "terms"      => (int) wp_count_terms( array( "taxonomy" => "category", "hide_empty" => false ) ),
        "comments"   => (int) wp_count_comments()->total_comments,
        "postmeta"   => (int) $wpdb->get_var( "SELECT COUNT(*) FROM $wpdb->postmeta" ),
        "options"    => (int) $wpdb->get_var( "SELECT COUNT(*) FROM $wpdb->options" ),
        "languages"  => function_exists( "pll_languages_list" ) ? pll_languages_list() : array(),
      ),
    ) );'
}

# The source is only read: commands that write refuse to run there.
case "$command" in
  prepare | import | finalize)
    [[ "$(conf ROLE)" == target ]] || { say "refusing to run $command outside the target"; exit 1; } ;;
esac

case "$command" in
inventory) inventory ;;

dump) # The database on stdout; tables matching MIGRATE_SKIP_TABLES (an ERE) are left out.
  skip="$(conf MIGRATE_SKIP_TABLES)"
  exclude=""
  if [[ -n "$skip" ]]; then
    exclude="$(wpq db tables --all-tables-with-prefix | grep -E "$skip" | paste -sd, - || true)"
  fi
  say "dump (left out: ${exclude:-nothing})"
  wp db export - --single-transaction --quick --default-character-set=utf8mb4 ${exclude:+--exclude_tables="$exclude"} 2>/dev/null
  ;;

uploads) # The media library: from the source as a tar stream, into the target without code.
  dir="$(uploads_dir)"
  if [[ "$(conf ROLE)" == source ]]; then
    tar -C "$dir" --exclude='*.php' --exclude='*.php[0-9]' --exclude='*.phtml' --exclude='*.phar' \
      --exclude='.htaccess' --exclude='.user.ini' -cf - .
    say "uploads: $(find "$dir" -type f | wc -l) files on the source, $(find "$dir" -type f \( -iname '*.php*' -o -iname '*.phtml' -o -iname '*.phar' -o -name '.htaccess' -o -name '.user.ini' \) | wc -l) left out (code or web server settings)"
  else
    install -d -m 0755 -o "$WEB_GROUP" -g "$WEB_GROUP" "$dir"
    tar -C "$dir" --no-same-owner --no-same-permissions -xf -
    chown -R "$WEB_GROUP:$WEB_GROUP" "$dir"
    find "$dir" -type d -exec chmod 0755 {} + && find "$dir" -type f -exec chmod 0644 {} +
    left="$(find "$dir" -type f \( -iname '*.php*' -o -iname '*.phtml' -o -iname '*.phar' \) | wc -l)"
    [[ "$left" == 0 ]] || { say "code found in uploads after the copy: $left files"; exit 1; }
    say "uploads: $(find "$dir" -type f | wc -l) files on the target"
  fi
  ;;

certs) # Source: the current certificates of WP_HOST and MCP_HOST (tar on stdout, root only).
       # Target: kept in /etc/letsencrypt/live/ until the names point here (cert_ensure).
  if [[ "$(conf ROLE)" == source ]]; then
    list=()
    for host in "$WP_HOST" "$MCP_HOST"; do
      read -r crt key < <(cert_files "$host")
      [[ -s "$crt" && -s "$key" ]] || { say "no certificate for $host on the source"; continue; }
      list+=("$crt" "$key")
    done
    tar -cf - --transform 's#.*/##' "${list[@]}" 2>/dev/null
  else
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    chmod 0700 "$tmp" && tar -C "$tmp" -xf -
    for host in "$WP_HOST" "$MCP_HOST"; do
      live="/etc/letsencrypt/live/$host"
      if [[ -f "/etc/letsencrypt/renewal/$host.conf" ]]; then say "$host: certbot already manages one, kept"; continue; fi
      crt="$(find "$tmp" -maxdepth 1 \( -name "$host.crt" -o -name fullchain.pem \) | head -1)"
      key="$(find "$tmp" -maxdepth 1 \( -name "$host.key" -o -name privkey.pem \) | head -1)"
      [[ -n "$crt" && -n "$key" ]] || { say "$host: none received"; continue; }
      install -d -m 0755 "$live"
      install -m 0644 "$crt" "$live/fullchain.pem" && install -m 0600 "$key" "$live/privkey.pem"
      say "$host: copied, valid until $(openssl x509 -in "$live/fullchain.pem" -noout -enddate | cut -d= -f2), names: $(openssl x509 -in "$live/fullchain.pem" -noout -ext subjectAltName | tail -1 | tr -d ' ')"
    done
  fi
  ;;

prepare) # Target: core, plugins and themes from wordpress.org at the source's versions.
  inv="$S/source.json"
  version="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["wordpress"])' "$inv")"
  [[ "$(wp core version)" == "$version" ]] || wp core update --version="$version" --force --quiet
  wp core verify-checksums --quiet && say "core $version: checksums ok"
  skip=" $(conf MIGRATE_SKIP_PLUGINS) "
  python3 -I -c 'import json,sys; [print(p["slug"], p["version"]) for p in json.load(open(sys.argv[1]))["plugins"]]' "$inv" |
    while read -r slug version; do
      if [[ "$skip" == *" $slug "* ]]; then say "plugin $slug: left out"; continue; fi
      if [[ -d "$S/plugins/$slug" ]]; then
        rm -rf "$WP/wp-content/plugins/$slug"
        cp -r "$S/plugins/$slug" "$WP/wp-content/plugins/$slug"
        chown -R root:root "$WP/wp-content/plugins/$slug" && chmod -R go-w "$WP/wp-content/plugins/$slug"
        say "plugin $slug: from EXTRA_PLUGINS (reviewed code, root-owned)"
      elif [[ "$(wpq plugin get "$slug" --field=version || true)" == "$version" ]]; then
        say "plugin $slug $version: already there"
      else
        wp plugin install "$slug" --version="$version" --force --quiet >&2 ||
          { say "plugin $slug $version: not on wordpress.org; put it in EXTRA_PLUGINS or in MIGRATE_SKIP_PLUGINS"; exit 1; }
        say "plugin $slug $version: installed from wordpress.org"
      fi
    done
  keep="$(python3 -I -c 'import json,sys; print(" ".join(p["slug"] for p in json.load(open(sys.argv[1]))["plugins"]))' "$inv")"
  for slug in $(wpq plugin list --field=name); do
    [[ " $keep " == *" $slug "* && "$skip" != *" $slug "* ]] || { wp plugin delete "$slug" --quiet && say "plugin $slug: removed (not on the source)"; }
  done
  read -r stylesheet template < <(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["stylesheet"], d["template"])' "$inv")
  python3 -I -c 'import json,sys; [print(t["slug"], t["version"]) for t in json.load(open(sys.argv[1]))["themes"]]' "$inv" |
    while read -r slug version; do
      [[ "$slug" == "$stylesheet" || "$slug" == "$template" ]] || continue
      [[ "$(wpq theme get "$slug" --field=version || true)" == "$version" ]] ||
        wp theme install "$slug" --version="$version" --force --quiet >&2 ||
        { say "theme $slug $version: not on wordpress.org"; exit 1; }
      say "theme $slug $version: ready"
    done
  prefix="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["prefix"])' "$inv")"
  [[ "$prefix" =~ ^[A-Za-z0-9_]+$ ]] || { say "odd table prefix"; exit 1; }
  if [[ "$(wpq config get table_prefix)" != "$prefix" ]]; then
    wp config set table_prefix "$prefix" --type=variable --quiet && say "table prefix: $prefix"
  fi
  ;;

import) # Target: the dump on stdin replaces the database.
  # shellcheck disable=SC2016 # SQL, not shell
  drop="$(wpq db query 'SELECT CONCAT("DROP TABLE IF EXISTS `", table_name, "`;") FROM information_schema.tables WHERE table_schema = DATABASE()' --skip-column-names)"
  [[ -z "$drop" ]] || wpq db query "SET FOREIGN_KEY_CHECKS=0; $drop"
  wp db import - >&2
  say "imported: $(wpq db query 'SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()' --skip-column-names) tables"
  ;;

finalize) # Target: what a copied database must not keep, then the site's own settings.
  inv="$S/source.json"
  read -r old_path old_home < <(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["path"], d["home"])' "$inv")
  if [[ "$old_path" != "$WP" ]]; then
    say "paths $old_path -> $WP: $(wp search-replace "$old_path" "$WP" --all-tables-with-prefix --precise --format=count 2>/dev/null) changes"
  fi
  [[ "$old_home" == "https://$WP_HOST" ]] || say "the source's address is $old_home, not https://$WP_HOST: check it"
  # Credentials and sessions do not move: every Application Password (wpmcp's own too:
  # bin/deploy makes a new one) and every login session ends here.
  for user in $(wpq user list --field=ID); do
    wpq user application-password delete "$user" --all >/dev/null || true
  done
  rm -f /etc/wpmcp/wp-app-password
  # shellcheck disable=SC2016 # PHP, not shell
  wpq eval 'WP_Session_Tokens::destroy_all_for_all_users();'
  # Plugins left out stay out of active_plugins.
  # shellcheck disable=SC2016 # PHP, not shell
  wp eval '$active = array_values( array_filter( (array) get_option( "active_plugins" ), static fn ( $f ) => file_exists( WP_PLUGIN_DIR . "/" . $f ) ) ); update_option( "active_plugins", $active ); echo count( $active ), " active plugins\n";' >&2
  types="$(conf MIGRATE_DELETE_POST_TYPES)"
  for type in $types; do
    ids="$(wpq post list --post_type="$type" --post_status=any --format=ids)"
    # shellcheck disable=SC2086 # one ID per word
    [[ -z "$ids" ]] || { wpq post delete $ids --force >/dev/null; say "$type: $(wc -w <<<"$ids") items deleted"; }
  done
  python3 -I -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["locales"]))' "$inv" | tr ' ' '\n' |
    while read -r locale; do
      [[ -n "$locale" ]] || continue
      { wpq language core install "$locale" && wpq language plugin install --all "$locale" && wpq language theme install --all "$locale"; } >/dev/null &&
        say "language packs: $locale" || say "language packs: none for $locale"
    done
  # Themes the source does not use (the image's own; the active one could not go before).
  read -r stylesheet template < <(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["stylesheet"], d["template"])' "$inv")
  for slug in $(wpq theme list --field=name); do
    [[ "$slug" == "$stylesheet" || "$slug" == "$template" ]] || { wpq theme delete "$slug" && say "theme $slug: removed"; }
  done
  wp core update-db --quiet >&2 || true
  # With the plugins loaded: Polylang adds the language prefixes to the rules.
  wp --skip-themes rewrite flush --quiet 2>/dev/null
  wpq cache flush
  wpq transient delete --all >/dev/null || true
  say "site over loopback: $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/")," \
    "a feed: $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/feed/")," \
    "the sitemap: $(curl -s -o /dev/null -w '%{http_code}' --max-time 20 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/sitemap.xml")"
  ;;

*) say "unknown command: $command"; exit 1 ;;
esac
