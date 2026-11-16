#!/bin/bash
# Runs ON THE SERVER, as root, from a staging directory prepared by bin/bootstrap-site:
#   sudo bash bootstrap-site.sh <staging dir>
# Only for a FRESH site (not one you migrated, which has its own content and plugins), after
# bin/setup-server. Idempotent, and it never touches content other than the sample items'
# language:
#   1. installs and activates Polylang, activates All in One SEO if it is installed;
#   2. creates the languages in SITE_LANGUAGES (the first is the default) and gives the
#      existing items the default language; sets "post name" permalinks.
set -euo pipefail

# shellcheck source=deploy/lib.remote.sh
. "$(dirname "$0")/lib.remote.sh" "$1"
# shellcheck source=deploy/platform.debian.sh
. "$S/platform.sh"
platform_check
SITE_LANGUAGES="$(conf SITE_LANGUAGES)"
[[ "$SITE_LANGUAGES" =~ ^[a-z]{2,3}(_[A-Z]{2})?(,[a-z]{2,3}(_[A-Z]{2})?)*$ ]] ||
  { echo "SITE_LANGUAGES must be WordPress locales, e.g. en_GB,es_ES" >&2; exit 1; }

step "plugins"
wp plugin is-installed polylang || wp plugin install polylang --quiet
wp plugin activate polylang --quiet
wp plugin is-installed all-in-one-seo-pack && wp plugin activate all-in-one-seo-pack --quiet
wp plugin list --fields=name,status,version --status=active

step "languages: $SITE_LANGUAGES"
# On stdin: WP-CLI may run as the web server's user, which cannot read the staging directory.
SITE_LANGUAGES="$SITE_LANGUAGES" wp eval-file - <"$S/bootstrap-languages.php"
wp rewrite structure '/%postname%/' --quiet
echo "permalinks: $(wp option get permalink_structure)"
echo "admin user: $(wp user list --role=administrator --field=user_login | head -1)."
