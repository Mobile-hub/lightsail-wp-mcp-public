# Sourced (as root, on the server) by remote-install.sh and bootstrap-site.sh. Detects the
# WordPress image and defines what differs between the two supported ones:
#   bitnami  the Bitnami WordPress image (Lightsail blueprint "wordpress")
#   debian   Lightsail's own WordPress image (blueprint "wordpress_ls_1_0"): Debian 12,
#            Apache 2 with mod_php, MariaDB, WP-CLI in /usr/local/bin
# shellcheck shell=bash disable=SC2034  # the variables are used by the scripts that source this

if [[ -d /opt/bitnami/wordpress && -x /opt/bitnami/apache/bin/httpd ]]; then
  PLATFORM=bitnami
  WP=/opt/bitnami/wordpress
  WEB_GROUP=daemon                               # Apache and PHP-FPM run as daemon
  HTTPD=(/opt/bitnami/apache/bin/httpd -f /opt/bitnami/apache/conf/httpd.conf)
  LE=/opt/bitnami/letsencrypt
  ACME_WEBROOT=/opt/bitnami/apps/letsencrypt
  MCP_CERT="$LE/certificates/$MCP_HOST.crt"
  MCP_KEY="$LE/certificates/$MCP_HOST.key"
  # name in the staging dir -> installed path (vhosts load in name order: these sort
  # before wordpress-vhost.conf, whose "ServerAlias *" would answer for every name)
  VHOST_HTTP=/opt/bitnami/apache/conf/vhosts/mcp-vhost.conf
  VHOST_HTTPS=/opt/bitnami/apache/conf/vhosts/mcp-https-vhost.conf
  VHOST_SRC_HTTP=bitnami/mcp-vhost.conf
  VHOST_SRC_HTTPS=bitnami/mcp-https-vhost.conf
  wp() { /opt/bitnami/wp-cli/bin/wp --path="$WP" "$@"; }   # the wrapper drops to daemon itself
  apache_test() { "${HTTPD[@]}" -t; }
  apache_reload() { "${HTTPD[@]}" -k graceful; }
  apache_enable() { :; }                         # every file in vhosts/ is loaded
  apache_link() { echo "$1"; }
elif [[ -f /var/www/html/wp-load.php && -x /usr/sbin/apache2ctl ]]; then
  PLATFORM=debian
  WP=/var/www/html
  WEB_GROUP=www-data
  ACME_WEBROOT=/var/lib/wpmcp-acme               # root-owned, outside the web root
  MCP_CERT="/etc/letsencrypt/live/$MCP_HOST/fullchain.pem"
  MCP_KEY="/etc/letsencrypt/live/$MCP_HOST/privkey.pem"
  # sites-enabled loads in name order: these sort after 000-default.conf and
  # default-ssl.conf, which stay the defaults for every other name.
  VHOST_HTTP=/etc/apache2/sites-available/wpmcp.conf
  VHOST_HTTPS=/etc/apache2/sites-available/wpmcp-ssl.conf
  VHOST_SRC_HTTP=debian/wpmcp.conf
  VHOST_SRC_HTTPS=debian/wpmcp-ssl.conf
  wp() { runuser -u www-data -- env HOME=/tmp /usr/local/bin/wp --path="$WP" "$@"; }
  apache_test() { apache2ctl -t; }
  apache_reload() { systemctl reload apache2; }
  # sites-available/x.conf -> sites-enabled/x.conf, conf-available/x.conf -> conf-enabled/x.conf
  apache_link() { local d; d="$(basename "$(dirname "$1")")"; echo "/etc/apache2/${d%-available}-enabled/$(basename "$1")"; }
  apache_enable() { ln -sfn "../$(basename "$(dirname "$1")")/$(basename "$1")" "$(apache_link "$1")"; }
else
  echo "unsupported server: neither the Bitnami nor Lightsail's own WordPress image" >&2
  exit 1
fi

# apache_apply <source> <dest>: install, enable, check, reload; undo on failure.
apache_apply() {
  local src="$1" dest="$2" backup="" link
  link="$(apache_link "$dest")"
  if cmp -s "$src" "$dest" 2>/dev/null && [[ -e "$link" ]]; then
    echo "unchanged: $dest"; return 0
  fi
  [[ -e "$dest" ]] && { backup="$dest.bak.$$"; cp -p "$dest" "$backup"; }
  install -m 0644 -o root -g root "$src" "$dest"
  apache_enable "$dest"
  if apache_test 2>&1; then
    apache_reload
    rm -f "$backup"; echo "applied: $dest"
  else
    if [[ -n "$backup" ]]; then mv "$backup" "$dest"; else rm -f "$dest"; [[ "$PLATFORM" == debian ]] && rm -f "$link"; fi
    echo "apache rejected $dest: change undone" >&2
    return 1
  fi
}

# Python with venv support (Debian splits ensurepip into python3-venv).
ensure_venv() {
  python3 -c 'import ensurepip, sys; assert sys.version_info >= (3, 11)' 2>/dev/null && return 0
  [[ "$PLATFORM" == debian ]] || { echo "python3 >= 3.11 with venv is required" >&2; exit 1; }
  DEBIAN_FRONTEND=noninteractive apt-get -qq update
  DEBIAN_FRONTEND=noninteractive apt-get -qq install -y --no-install-recommends python3-venv >/dev/null
}

ensure_certbot() {
  have_certbot() { command -v certbot >/dev/null 2>&1; }
  have_certbot && return 0
  DEBIAN_FRONTEND=noninteractive apt-get -qq update
  DEBIAN_FRONTEND=noninteractive apt-get -qq install -y --no-install-recommends certbot >/dev/null
}

# certbot_issue <host> <webroot>: a certificate for one name (debian), renewed by
# certbot.timer, which reloads Apache through the deploy hook.
certbot_issue() {
  local host="$1" webroot="$2"
  local -a account=(--register-unsafely-without-email)
  [[ -n "${ACME_EMAIL:-}" ]] && account=(--email "$ACME_EMAIL")
  ensure_certbot
  certbot certonly --non-interactive --agree-tos "${account[@]}" --webroot -w "$webroot" \
    -d "$host" --key-type ecdsa --deploy-hook "systemctl reload apache2" 2>&1 |
    sed -E 's/[^ ]+@[^ ]+/<account>/g' | tail -4
}
