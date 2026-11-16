# The Bitnami WordPress image (Lightsail blueprint "wordpress"). Shipped as platform.sh
# when the site file says PLATFORM=bitnami, and sourced (as root, on the server) after
# lib.remote.sh. deploy/platform.debian.sh has the same variables and functions.
# shellcheck shell=bash disable=SC2034  # the variables are used by the scripts that source this

WP=/opt/bitnami/wordpress
WP_CONFIG="$WP/wp-config.php"
WEB_GROUP=daemon                                 # Apache and PHP-FPM run as daemon
HTTPD=(/opt/bitnami/apache/bin/httpd -f /opt/bitnami/apache/conf/httpd.conf)
LE=/opt/bitnami/letsencrypt
ACME_WEBROOT=/opt/bitnami/apps/letsencrypt
MCP_CERT="$LE/certificates/${MCP_HOST:-}.crt"
MCP_KEY="$LE/certificates/${MCP_HOST:-}.key"
# Vhosts load in name order: these sort before wordpress-vhost.conf, whose "ServerAlias *"
# would answer for every name.
VHOST_HTTP=/opt/bitnami/apache/conf/vhosts/mcp-vhost.conf
VHOST_HTTPS=/opt/bitnami/apache/conf/vhosts/mcp-https-vhost.conf
# Apache's PHP handler: the FPM socket it proxies to is the PHP that serves the site.
FPM_PROXY_CONF=/opt/bitnami/apache/conf/bitnami/php-fpm.conf

# cert_files <host>: certificate (with its chain) and key of a name, as this server keeps them.
cert_files() { echo "$LE/certificates/$1.crt" "$LE/certificates/$1.key"; }

platform_check() {
  [[ -d "$WP" && -x "${HTTPD[0]}" ]] ||
    { echo "PLATFORM=bitnami, but this is not a Bitnami WordPress server" >&2; exit 1; }
}

wp() { /opt/bitnami/wp-cli/bin/wp --path="$WP" "$@"; }   # the wrapper drops to daemon itself
apache_test() { "${HTTPD[@]}" -t; }
apache_reload() { "${HTTPD[@]}" -k graceful; }
apache_link() { echo "$1"; }                     # every file in vhosts/ is loaded
apache_enable() { :; }
apache_disable() { :; }
# Bitnami's bncert-tool, run once for the site, installs lego and the ACME webroot that the
# MCP vhost includes; a fresh image has neither.
apache_prepare() {
  [[ -x "$LE/lego" && -f "$ACME_WEBROOT/conf/httpd-prefix.conf" ]] || {
    echo "no lego or ACME webroot: run Bitnami's bncert-tool for the site first (sudo /opt/bitnami/bncert-tool)" >&2
    exit 1
  }
}

# The certificate of MCP_HOST, from lego (Bitnami's ACME client) with this server's existing
# Let's Encrypt account (or ACME_EMAIL), renewed by a cron job.
mcp_cert_issue() {
  [[ -s "$MCP_CERT" ]] && return 0
  local email
  email="$(find "$LE/accounts/acme-v02.api.letsencrypt.org/" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | head -1)"
  [[ "$email" == *@* ]] || email="$ACME_EMAIL"
  [[ "$email" == *@* ]] || { echo "no Let's Encrypt account found: set ACME_EMAIL" >&2; exit 1; }
  "$LE/lego" --path "$LE" --email "$email" --accept-tos --http --http-timeout 30 \
    --http.webroot "$ACME_WEBROOT" --domains="$MCP_HOST" --user-agent wpmcp run 2>&1 |
    sed -E 's/[^ ]+@[^ ]+/<account>/g' | tail -5
  (
    umask 077
    cat >/etc/cron.d/wpmcp-cert <<CRON
# Renews the certificate of $MCP_HOST (installed by lightsail-wp-mcp).
41 3 * * * root $LE/lego --path $LE --email $email --http --http-timeout 30 --http.webroot $ACME_WEBROOT --domains=$MCP_HOST --user-agent wpmcp renew --days 30 >/dev/null 2>&1 && chmod 600 $MCP_KEY && ${HTTPD[*]} -k graceful
CRON
  )
  chmod 0600 /etc/cron.d/wpmcp-cert
}

# The PHP-FPM master that serves the site: the one listening on the socket Apache proxies
# to (Bitnami's own, or a package-managed one installed as in AWS's PHP update guide).
served_fpm_pid() {
  local sock pid
  sock="$(sed -nE 's#.*Proxy "unix:([^|"]+)\|.*#\1#p' "$FPM_PROXY_CONF" | head -1)"
  [[ -n "$sock" ]] || return 1
  for pid in $(ss -xlpnH | awk -v s="$sock" '$5 == s' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u); do
    [[ "$(ps -o ppid= -p "$pid" | tr -d ' ')" == 1 ]] && { echo "$pid"; return 0; }
  done
  return 1
}
served_php() { readlink -f "/proc/$(served_fpm_pid)/exe"; }
served_php_version() { "$(served_php)" -v | awk 'NR == 1 {print $2}'; }
served_php_ini_dir() { "$(served_php)" -i 2>/dev/null | sed -n 's/^Scan this dir for additional .ini files => //p'; }
served_php_reload() { kill -USR2 "$(served_fpm_pid)"; }   # graceful: workers finish their request

# For bin/inventory (read-only), which also defines names().
platform_inventory() {
  printf '\n## apache\n'
  "${HTTPD[@]}" -v 2>/dev/null | head -1
  echo "modules: $("${HTTPD[@]}" -M 2>/dev/null | grep -oE '(proxy[a-z_]*|rewrite|ssl|headers|remoteip|http2|mpm_[a-z]+|php[0-9]*|unixd|authz_host)_module' | tr '\n' ' ')"
  echo "vhost files: $(names /opt/bitnami/apache/conf/vhosts)"
  grep -rhoE '^\s*(ServerName|ServerAlias|AllowOverride|SSLCertificateFile|ProxyPass|Include)\s+\S+.*' \
    /opt/bitnami/apache/conf/vhosts/ /opt/bitnami/apache/conf/bitnami/bitnami.conf \
    /opt/bitnami/apache/conf/bitnami/bitnami-ssl.conf 2>/dev/null | sed 's/^\s*//' | sort | uniq -c
  echo "authorization passthrough: $(grep -rlE 'HTTP_AUTHORIZATION|CGIPassAuth' /opt/bitnami/apache/conf "$WP/.htaccess" 2>/dev/null | tr '\n' ' ')"

  printf '\n## php\n'
  echo "serving the site: $(served_php 2>/dev/null) $(served_php_version 2>/dev/null)"
  echo "bitnami's own: $(/opt/bitnami/php/bin/php -v 2>/dev/null | head -1)"
  echo "fpm masters: $(pgrep -f 'php-fpm: master' -a 2>/dev/null | sed 's/^[0-9]* //' | tr '\n' ';')"
  echo "upload_progress: $("$(served_php)" -i 2>/dev/null | sed -n 's/^session.upload_progress.enabled => //p')"
  grep -hE '^(pm|pm\.[a-z_]+|listen)\s*=' /opt/bitnami/php/etc/php-fpm.d/*.conf /opt/bitnami/php/etc/memory.conf 2>/dev/null

  printf '\n## tls\n'
  echo "lego directory: $(names "$LE")"
  echo "bncert-tool: $(ls /opt/bitnami/bncert-tool 2>/dev/null || echo absent)"
  echo "certificates: $(names "$LE/certificates" '*.crt')"
  echo "renewal jobs: $(cat /etc/cron.d/* /var/spool/cron/crontabs/* 2>/dev/null | grep -ciE 'lego|bncert|certbot')"
}
