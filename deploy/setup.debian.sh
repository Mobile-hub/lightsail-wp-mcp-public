#!/bin/bash
# Runs ON THE SERVER, as root, from a staging directory prepared by bin/setup-server, on an
# instance of Lightsail's own WordPress image (PLATFORM=debian):
#   sudo bash setup.sh <staging dir>
# Prepares the server for the site and for wpmcp, and never touches content:
#   1. system packages up to date, security updates from then on (unattended-upgrades);
#   2. refuses a PHP without the October 2026 fixes; PHP hardening ini;
#   3. the site address fixed to https://WP_HOST (the image derives it from the request's
#      Host header, over http);
#   4. the site's own vhosts (port 80: ACME and redirect; 443: TLS, other names redirected)
#      and the permalink rules in Apache's configuration (the image reads no .htaccess);
#   5. a Let's Encrypt certificate for WP_HOST and WP_ALIASES once they point here (before
#      that, a copied certificate from bin/migrate-site is used if there is one).
# Idempotent: run it again after the DNS switch to get certbot's certificate.
# Afterwards, change the admin's password in wp-admin: on this image the initial one (in
# /opt/aws/wordpress/credentials.log, which wp-config.php reads) is also the database's.
set -euo pipefail

# shellcheck source=deploy/lib.remote.sh
. "$(dirname "$0")/lib.remote.sh" "$1"
# shellcheck source=deploy/platform.debian.sh
. "$S/platform.sh"
platform_check
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

step "first boot"
command -v cloud-init >/dev/null && cloud-init status --wait >/dev/null || true
wp core is-installed && echo "WordPress $(wp core version)"

step "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get -qq update
apt-get -qq -o Dpkg::Options::=--force-confold full-upgrade -y >/dev/null
apt_install unattended-upgrades certbot python3-venv
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'APT'
// Written by lightsail-wp-mcp setup.debian.sh: daily package lists, security updates.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT
echo "up to date; unattended security updates on"
[[ -f /var/run/reboot-required ]] && echo "a reboot is pending (kernel or libc update): reboot when convenient"

step "php"
version="$(served_php_version)"
echo "$(served_php) $version"
php_patched "$version" ||
  { echo "PHP $version lacks the fixes for CVE-2026-109618/109619: not continuing" >&2; exit 1; }
echo "fixes listed in the package changelog: $(zgrep -cE 'CVE-2026-10961[89]' "/usr/share/doc/php$(php_branch)-common/changelog.Debian.gz" 2>/dev/null || echo 0)"
php_hardening_ini >"$tmp"
if cmp -s "$tmp" "$(served_php_ini_dir)/99-wpmcp.ini"; then
  echo "hardening ini unchanged"
else
  install -m 0644 -o root -g root "$tmp" "$(served_php_ini_dir)/99-wpmcp.ini"
  echo "hardening ini written"
fi
# The upgrade may have replaced mod_php: a restart loads the new library and the ini.
served_php_reload

step "site address: https://$WP_HOST"
config="$WP_CONFIG"
# Only the two address lines (and WP-CLI's stand-in host) change; the file keeps its owner
# and mode because it is rewritten in place.
WP_HOST="$WP_HOST" python3 -I - "$config" "$tmp" <<'PY'
import os, re, sys
text = open(sys.argv[1], encoding="utf-8").read()
host = os.environ["WP_HOST"]
new = re.sub(r"^define\(\s*'WP_HOME'.*$", f"define( 'WP_HOME', 'https://{host}' );", text, flags=re.M)
new = re.sub(r"^define\(\s*'WP_SITEURL'.*$", f"define( 'WP_SITEURL', 'https://{host}' );", new, flags=re.M)
new = re.sub(r"^(\s*\$_SERVER\['HTTP_HOST'\]\s*=\s*)'[^']*';", rf"\g<1>'{host}';", new, flags=re.M)
if "WP_HOME" not in new:
    new = new.replace("/* That's all, stop editing!", f"define( 'WP_HOME', 'https://{host}' );\ndefine( 'WP_SITEURL', 'https://{host}' );\n\n/* That's all, stop editing!", 1)
open(sys.argv[2], "w", encoding="utf-8").write(new)
PY
if cmp -s "$config" "$tmp"; then
  echo "unchanged"
else
  php -l "$tmp" >/dev/null || { echo "edited wp-config.php does not parse: unchanged" >&2; exit 1; }
  cp -p "$config" "$config.bak.$(date -u +%Y%m%d%H%M%S)"
  cat "$tmp" >"$config"
  echo "updated $config (backup next to it)"
fi
echo "home: $(wp option get home)"

step "file permissions"
# The image leaves wp-content, plugins, themes and upgrade world-writable, and /var/www
# (which holds wp-config.php) writable by the web server's group, so PHP could replace
# wp-config.php. The web server keeps its group write access to the web root (updates).
chmod g-w,o-w "$(dirname "$WP_CONFIG")"
writable="$(find "$WP" -xdev -perm -0002 ! -type l | wc -l)"
find "$WP" -xdev -perm -0002 ! -type l -exec chmod o-w {} +
echo "world-writable entries fixed: $writable; $(stat -c '%A %U:%G' "$(dirname "$WP_CONFIG")") $(dirname "$WP_CONFIG")"

step "apache: the site's vhosts and permalinks"
a2enmod -q headers ssl rewrite >/dev/null
cat >"$tmp" <<CONF
# WordPress permalinks (written by lightsail-wp-mcp setup.debian.sh).
<Directory $WP>
  RewriteEngine On
  RewriteBase /
  RewriteRule ^index\.php$ - [L]
  RewriteCond %{REQUEST_FILENAME} !-f
  RewriteCond %{REQUEST_FILENAME} !-d
  RewriteRule . /index.php [L]
</Directory>
CONF
apache_apply "$tmp" /etc/apache2/conf-available/wordpress-permalinks.conf
cat >"$tmp" <<CONF
# WordPress hardening (written by lightsail-wp-mcp setup.debian.sh).
ServerTokens Prod
ServerSignature Off
<Directory $WP>
  # XML-RPC (password guessing, pingback abuse) and files that tell the version.
  <FilesMatch "^(xmlrpc\.php|readme\.html|license\.txt|wp-config-sample\.php)\$">
    Require all denied
  </FilesMatch>
</Directory>
# Uploads are data: nothing in them runs as PHP, even if a plugin lets a .php through.
<Directory $WP/wp-content/uploads>
  php_admin_flag engine off
  <FilesMatch "\.(php[0-9]?|phtml|phar)\$">
    Require all denied
  </FilesMatch>
</Directory>
CONF
apache_apply "$tmp" /etc/apache2/conf-available/wordpress-hardening.conf
apache_apply "$S/apache/site-http.conf" /etc/apache2/sites-available/wordpress.conf
# The image's defaults give way to the site's vhosts once these are in place.
disable_default() {
  [[ -e "/etc/apache2/sites-enabled/$1.conf" ]] || return 0
  a2dissite -q "$1" && apache_reload && echo "disabled the image's $1.conf"
}
disable_default 000-default

step "certificate for $WP_HOST ${WP_ALIASES:+(and $WP_ALIASES)}"
# shellcheck disable=SC2086 # the aliases are separate words
if cert_ensure "$WP" "$WP_HOST" $WP_ALIASES; then
  openssl x509 -in "/etc/letsencrypt/live/$WP_HOST/fullchain.pem" -noout -subject -enddate
  apache_apply "$S/apache/site-https.conf" /etc/apache2/sites-available/wordpress-ssl.conf
  disable_default default-ssl
  apache_reload   # a new certificate with an unchanged vhost
else
  echo "HTTPS for the site waits: point $WP_HOST at this server and run bin/setup-server again" >&2
  apache_test && apache_reload
fi

step "checks over loopback"
# Apache may have just restarted (mod_php): give it up to 30 s.
for _ in $(seq 1 30); do
  [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/")" == 000 ]] || break
  sleep 1
done
echo "https://$WP_HOST/: $(curl -s -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/")" \
  "/wp-json/: $(curl -s -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/wp-json/")" \
  "http redirect: $(curl -s -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$WP_HOST:80:127.0.0.1" "http://$WP_HOST/")"
