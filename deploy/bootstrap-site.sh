#!/bin/bash
# Runs ON THE SERVER, as root, from a staging directory prepared by bin/bootstrap-site:
#   sudo bash bootstrap-site.sh <staging dir>
# Prepares a FRESH instance of Lightsail's own WordPress image (wordpress_ls_1_0) for
# wpmcp. Idempotent, and it never touches content other than the sample items' language:
#   1. fixes the site address to https://WP_HOST (the image derives it from the request's
#      Host header, over http);
#   2. gets a Let's Encrypt certificate for WP_HOST and puts it on the site's HTTPS vhost;
#   3. installs and activates Polylang, activates All in One SEO (preinstalled);
#   4. creates the languages in SITE_LANGUAGES (the first is the default) and gives the
#      existing items the default language; sets "post name" permalinks, with the rewrite
#      rules in Apache's configuration (the image reads no .htaccess);
# Afterwards, change the admin's password in wp-admin: on this image the initial one (in
# /opt/aws/wordpress/credentials.log, which wp-config.php reads) is also the database's.
# On the Bitnami image, use Bitnami's own tools instead (bncert-tool) and skip this.
set -euo pipefail

S="$(cd "$1" && pwd)"
conf() { sed -n "s/^$1=//p" "$S/site.conf"; }
MCP_HOST="$(conf MCP_HOST)"
WP_HOST="$(conf WP_HOST)"
ACME_EMAIL="$(conf ACME_EMAIL)"
SITE_LANGUAGES="$(conf SITE_LANGUAGES)"
host_re='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
[[ "$WP_HOST" =~ $host_re ]] || { echo "bad WP_HOST" >&2; exit 1; }
[[ -z "$ACME_EMAIL" || "$ACME_EMAIL" =~ ^[^[:space:]@\"\']+@[a-z0-9.-]+$ ]] || { echo "bad ACME_EMAIL" >&2; exit 1; }
[[ "$SITE_LANGUAGES" =~ ^[a-z]{2,3}(_[A-Z]{2})?(,[a-z]{2,3}(_[A-Z]{2})?)*$ ]] ||
  { echo "SITE_LANGUAGES must be WordPress locales, e.g. en_GB,es_ES" >&2; exit 1; }
# shellcheck source=deploy/platform.sh
. "$S/platform.sh"
[[ "$PLATFORM" == debian ]] || { echo "bootstrap-site is for Lightsail's own WordPress image only" >&2; exit 1; }

step() { printf '\n== %s\n' "$*"; }

step "first boot"
command -v cloud-init >/dev/null && cloud-init status --wait >/dev/null || true
wp core is-installed && wp core version

step "site address: https://$WP_HOST"
config=/var/www/wp-config.php
[[ -f "$config" ]] || config="$WP/wp-config.php"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
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

step "certificate for $WP_HOST"
cert="/etc/letsencrypt/live/$WP_HOST/fullchain.pem"
key="/etc/letsencrypt/live/$WP_HOST/privkey.pem"
# The image's port-80 vhost serves /var/www/html and lets ACME challenges through.
[[ -s "$cert" ]] || certbot_issue "$WP_HOST" "$WP"
openssl x509 -in "$cert" -noout -subject -enddate
site_ssl=/etc/apache2/sites-available/default-ssl.conf
cp -p "$site_ssl" "$tmp"
sed -i -E "s#^(\s*SSLCertificateFile\s+).*#\1$cert#; s#^(\s*SSLCertificateKeyFile\s+).*#\1$key#" "$tmp"
grep -q '^\s*ServerName' "$tmp" || sed -i -E "0,/<VirtualHost/s#(<VirtualHost[^>]*>)#\1\n\tServerName $WP_HOST#" "$tmp"
grep -q 'Strict-Transport-Security' "$tmp" ||
  sed -i -E "0,/SSLEngine on/s#(\s*SSLEngine on)#\1\n\tHeader always set Strict-Transport-Security \"max-age=31536000\"#" "$tmp"
a2enmod -q headers ssl rewrite >/dev/null
apache_apply "$tmp" "$site_ssl"

step "permalinks without .htaccess"
# The image reads no .htaccess (AllowOverride None), and the web server may write the web
# root, so WordPress's rewrite rules go into Apache's own configuration instead.
cat >"$tmp" <<CONF
# WordPress permalinks (written by lightsail-wp-mcp bootstrap-site.sh).
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

step "plugins"
wp plugin is-installed polylang || wp plugin install polylang --quiet
wp plugin activate polylang all-in-one-seo-pack --quiet
wp plugin list --fields=name,status,version --status=active

step "languages: $SITE_LANGUAGES"
SITE_LANGUAGES="$SITE_LANGUAGES" wp eval-file "$S/bootstrap-languages.php"
wp rewrite structure '/%postname%/' --quiet
echo "permalinks: $(wp option get permalink_structure)"

step "checks"
echo "https://$WP_HOST/ over loopback: $(curl -s -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/")"
echo "https://$WP_HOST/wp-json/ over loopback: $(curl -s -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/wp-json/")"
echo "admin user: $(wp user list --role=administrator --field=user_login | head -1)." \
  "Change its password in wp-admin: the initial one is also the database password."
