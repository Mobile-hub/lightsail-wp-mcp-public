#!/bin/bash
# Runs ON THE SERVER, as root, from a staging directory prepared by bin/setup-server, on a
# Bitnami WordPress instance (PLATFORM=bitnami):
#   sudo bash setup.sh <staging dir>
# Bitnami's own tools keep the site's certificate (bncert-tool) and the stack; this only
# checks and hardens the PHP that serves the site:
#   1. refuses a PHP without the October 2026 fixes (CVE-2026-109618/109619). Bitnami's
#      bundled PHP is not updated by apt: follow AWS's guide (a package-managed PHP-FPM that
#      Apache proxies to) or move the site to Lightsail's own image (bin/migrate-site);
#   2. a hardening ini for that PHP (session.upload_progress off, for CVE-2026-115560),
#      reloaded gracefully.
# Idempotent.
set -euo pipefail

# shellcheck source=deploy/lib.remote.sh
. "$(dirname "$0")/lib.remote.sh" "$1"
# shellcheck source=deploy/platform.bitnami.sh
. "$S/platform.sh"
platform_check

step "php serving the site"
served_fpm_pid >/dev/null || { echo "cannot find the PHP-FPM that Apache proxies to ($FPM_PROXY_CONF)" >&2; exit 1; }
version="$(served_php_version)"
echo "$(served_php) $version"
php_patched "$version" || {
  echo "PHP $version lacks the fixes for CVE-2026-109618/109619. Bitnami's PHP is not updated" \
    "by apt: see https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-update-php-security-vulnerabilities.html" >&2
  exit 1
}

step "php hardening"
dir="$(served_php_ini_dir)"
[[ -d "$dir" ]] || { echo "that PHP reads no ini directory ($dir): add the settings to its php.ini by hand" >&2; exit 1; }
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
php_hardening_ini >"$tmp"
if cmp -s "$tmp" "$dir/99-wpmcp.ini"; then
  echo "unchanged: $dir/99-wpmcp.ini"
else
  install -m 0644 -o root -g root "$tmp" "$dir/99-wpmcp.ini"
  served_php_reload
  echo "written: $dir/99-wpmcp.ini (PHP-FPM reloaded)"
fi
sleep 2
echo "upload_progress now: $("$(served_php)" -i 2>/dev/null | sed -n 's/^session.upload_progress.enabled => //p')"

step "checks over loopback"
echo "https://$WP_HOST/: $(curl -s -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/")"
