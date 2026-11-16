# Lightsail's own WordPress image (blueprint "wordpress_ls_1_0"): Debian 12, Apache 2 with
# mod_php, MariaDB, WP-CLI in /usr/local/bin. Shipped as platform.sh when the site file says
# PLATFORM=debian, and sourced (as root, on the server) after lib.remote.sh.
# deploy/platform.bitnami.sh has the same variables and functions.
# shellcheck shell=bash disable=SC2034  # the variables are used by the scripts that source this

WP=/var/www/html
WEB_GROUP=www-data
ACME_WEBROOT=/var/lib/wpmcp-acme                 # root-owned, outside the web root
MCP_CERT="/etc/letsencrypt/live/${MCP_HOST:-}/fullchain.pem"
MCP_KEY="/etc/letsencrypt/live/${MCP_HOST:-}/privkey.pem"
# sites-enabled loads in name order. wordpress*.conf (setup.debian.sh) sort first and are
# the default for any other name; wpmcp*.conf answer only for MCP_HOST.
VHOST_HTTP=/etc/apache2/sites-available/wpmcp.conf
VHOST_HTTPS=/etc/apache2/sites-available/wpmcp-ssl.conf

platform_check() {
  [[ -f "$WP/wp-load.php" && -x /usr/sbin/apache2ctl && ! -d /opt/bitnami ]] ||
    { echo "PLATFORM=debian, but this is not Lightsail's own WordPress image" >&2; exit 1; }
}

wp() { runuser -u www-data -- env HOME=/tmp /usr/local/bin/wp --path="$WP" "$@"; }
apache_test() { apache2ctl -t; }
apache_reload() { systemctl reload apache2; }
# sites-available/x.conf -> sites-enabled/x.conf, conf-available/x.conf -> conf-enabled/x.conf
apache_link() { local d; d="$(basename "$(dirname "$1")")"; echo "/etc/apache2/${d%-available}-enabled/$(basename "$1")"; }
apache_enable() { ln -sfn "../$(basename "$(dirname "$1")")/$(basename "$1")" "$(apache_link "$1")"; }
apache_disable() { rm -f "$(apache_link "$1")"; }
apache_prepare() {
  a2enmod -q proxy proxy_http headers ssl rewrite >/dev/null
  install -d -m 0755 -o root -g root "$ACME_WEBROOT"
}

# certbot_issue <webroot> <host> [alias...]: one certificate for the names, renewed by
# certbot.timer, which reloads Apache through the deploy hook.
certbot_issue() {
  local webroot="$1" host="$2" name
  shift 2
  local -a account=(--register-unsafely-without-email) domains=(-d "$host")
  [[ -n "$ACME_EMAIL" ]] && account=(--email "$ACME_EMAIL")
  for name in "$@"; do domains+=(-d "$name"); done
  apt_install certbot
  certbot certonly --non-interactive --agree-tos "${account[@]}" --webroot -w "$webroot" \
    --cert-name "$host" "${domains[@]}" --key-type ecdsa --deploy-hook "systemctl reload apache2" 2>&1 |
    sed -E 's/[^ ]+@[^ ]+/<account>/g' | tail -4
}

# cert_ensure <webroot> <host> [alias...]: a certificate in /etc/letsencrypt/live/<host>/.
# One certbot manages is kept. A copied one (bin/migrate-site, before the DNS switch) is
# used until the host points here, then replaced by certbot's. Fails if there is none and
# the host does not point here yet.
cert_ensure() {
  local webroot="$1" host="$2" live aside=""
  live="/etc/letsencrypt/live/$host"
  if [[ -f "/etc/letsencrypt/renewal/$host.conf" ]]; then
    echo "certificate for $host: managed by certbot"; return 0
  fi
  if ! dns_points_here "$host"; then
    [[ -s "$live/fullchain.pem" ]] && { echo "certificate for $host: the copied one, until $host points here"; return 0; }
    echo "certificate for $host: none yet, $host does not point at this server" >&2; return 1
  fi
  if [[ -e "$live" ]]; then
    install -d -m 0700 /etc/letsencrypt/migrated
    aside="/etc/letsencrypt/migrated/$host.$(date -u +%Y%m%d%H%M%S)"
    mv "$live" "$aside"
  fi
  if certbot_issue "$@" && [[ -s "$live/fullchain.pem" ]]; then
    echo "certificate for $host: issued"; return 0
  fi
  [[ -n "$aside" ]] && { rm -rf "$live"; mv "$aside" "$live"; }
  echo "certificate for $host: certbot failed" >&2; return 1
}

mcp_cert_issue() { cert_ensure "$ACME_WEBROOT" "$MCP_HOST"; }

# mod_php from Debian's packages serves the site.
php_branch() { php -r 'echo PHP_MAJOR_VERSION, ".", PHP_MINOR_VERSION;'; }
served_php() { echo "libapache2-mod-php$(php_branch)"; }
served_php_version() { dpkg-query -W -f='${Version}' "$(served_php)"; }
served_php_ini_dir() { echo "/etc/php/$(php_branch)/apache2/conf.d"; }
served_php_reload() { systemctl restart apache2; }   # mod_php: a reload keeps the old library

# For bin/inventory (read-only), which also defines names().
platform_inventory() {
  printf '\n## apache\n'
  apache2ctl -v 2>/dev/null | head -1
  echo "modules: $(apache2ctl -M 2>/dev/null | grep -oE '(proxy[a-z_]*|rewrite|ssl|headers|remoteip|http2|mpm_[a-z]+|php[0-9._]*|unixd|authz_host)_module' | tr '\n' ' ')"
  echo "sites enabled: $(names /etc/apache2/sites-enabled)"
  echo "confs enabled: $(names /etc/apache2/conf-enabled)"
  grep -rhoE '^\s*(ServerName|ServerAlias|AllowOverride|SSLCertificateFile|ProxyPass|Include)\s+\S+.*' \
    /etc/apache2/sites-enabled/ /etc/apache2/conf-enabled/ 2>/dev/null | sed 's/^\s*//' | sort | uniq -c

  printf '\n## php\n'
  echo "serving the site: $(served_php) $(served_php_version 2>/dev/null)"
  echo "fixes in the package changelog: $(zgrep -cE 'CVE-2026-10961[89]' "/usr/share/doc/php$(php_branch)-common/changelog.Debian.gz" 2>/dev/null || echo 0)"
  echo "upload_progress: $(grep -rh 'upload_progress' "$(served_php_ini_dir)" 2>/dev/null | tr '\n' ' ')"
  echo "unattended upgrades: $(apt-config dump APT::Periodic::Unattended-Upgrade 2>/dev/null | cut -d'"' -f2)"

  printf '\n## tls\n'
  echo "certificates: $(names /etc/letsencrypt/live '*.*')"
  echo "managed by certbot: $(names /etc/letsencrypt/renewal)"
  echo "renewal timer: $(systemctl is-active certbot.timer 2>/dev/null)"
}
