#!/bin/bash
# Runs ON THE SERVER, as root, from a staging directory prepared by bin/mail-credentials,
# with the SMTP user name and password on stdin (two lines; never on a command line):
#   sudo bash mail-install.sh <staging dir> < credentials
# Points WordPress's mail at Amazon SES's SMTP endpoint through WP Mail SMTP ("Other SMTP",
# its free mailer), with every setting as a constant in /etc/wp-mail/smtp.php (root, read
# by the web server's group only) instead of the database, which backups copy. Forces the
# From address, adds a Reply-To (a mu-plugin) and, if MAIL_TEST_TO is set, sends a test.
# Prints no secrets. Idempotent.
set -euo pipefail

# shellcheck source=deploy/lib.remote.sh
. "$(dirname "$0")/lib.remote.sh" "$1"
# shellcheck source=deploy/platform.debian.sh
. "$S/platform.sh"
platform_check

IFS= read -r smtp_user
IFS= read -r smtp_pass
[[ "$smtp_user" =~ ^AKIA[A-Z0-9]{16}$ ]] || { echo "bad SMTP user on stdin" >&2; exit 1; }
[[ "$smtp_pass" =~ ^[A-Za-z0-9+/=]{40,60}$ ]] || { echo "bad SMTP password on stdin" >&2; exit 1; }
MAIL_HOST="$(conf MAIL_HOST)"
MAIL_FROM="$(conf MAIL_FROM)"
MAIL_FROM_NAME="$(conf MAIL_FROM_NAME)"
MAIL_REPLY_TO="$(conf MAIL_REPLY_TO)"
MAIL_TEST_TO="$(conf MAIL_TEST_TO)"
[[ "$MAIL_HOST" =~ ^email-smtp\.[a-z0-9-]+\.amazonaws\.com$ ]] || { echo "bad MAIL_HOST" >&2; exit 1; }
email_re='^[^[:space:]@"'"'"'\\]+@[a-z0-9.-]+$'
for value in "$MAIL_FROM" ${MAIL_REPLY_TO:+"$MAIL_REPLY_TO"} ${MAIL_TEST_TO:+"$MAIL_TEST_TO"}; do
  [[ "$value" =~ $email_re ]] || { echo "not an e-mail address: $value" >&2; exit 1; }
done
[[ "$MAIL_FROM_NAME" != *[\'\\]* ]] || { echo "MAIL_FROM_NAME: no quotes or backslashes" >&2; exit 1; }

step "WP Mail SMTP"
wp plugin is-installed wp-mail-smtp || wp plugin install wp-mail-smtp --quiet
wp plugin is-active wp-mail-smtp || wp plugin activate wp-mail-smtp --quiet
echo "wp-mail-smtp $(wp plugin get wp-mail-smtp --field=version)"

step "settings: /etc/wp-mail/smtp.php"
install -d -m 0750 -o root -g "$WEB_GROUP" /etc/wp-mail
(
  umask 027
  cat >/etc/wp-mail/smtp.php.new <<PHP
<?php
// Written by lightsail-wp-mcp (bin/mail-credentials). WordPress's mail goes out through
// Amazon SES; the SMTP user may send only as the From address below. Do not edit here:
// run bin/mail-credentials again (it also rotates the credentials).
define( 'WPMS_ON', true );
define( 'WPMS_MAILER', 'smtp' );
define( 'WPMS_SMTP_HOST', '$MAIL_HOST' );
define( 'WPMS_SMTP_PORT', 587 );
define( 'WPMS_SSL', 'tls' );
define( 'WPMS_SMTP_AUTOTLS', true );
define( 'WPMS_SMTP_AUTH', true );
define( 'WPMS_SMTP_USER', '$smtp_user' );
define( 'WPMS_SMTP_PASS', '$smtp_pass' );
define( 'WPMS_MAIL_FROM', '$MAIL_FROM' );
define( 'WPMS_MAIL_FROM_FORCE', true );
define( 'WPMS_MAIL_FROM_NAME', '$MAIL_FROM_NAME' );
define( 'WPMS_MAIL_FROM_NAME_FORCE', true );
define( 'WPMS_SET_RETURN_PATH', true );
define( 'WP_MAIL_REPLY_TO', '$MAIL_REPLY_TO' );
PHP
)
php -l /etc/wp-mail/smtp.php.new >/dev/null || { rm -f /etc/wp-mail/smtp.php.new; echo "generated settings do not parse" >&2; exit 1; }
chown root:"$WEB_GROUP" /etc/wp-mail/smtp.php.new && chmod 0440 /etc/wp-mail/smtp.php.new
mv /etc/wp-mail/smtp.php.new /etc/wp-mail/smtp.php
echo "written (root:$WEB_GROUP 0440)"

step "wp-config.php loads it"
line="if ( is_readable( '/etc/wp-mail/smtp.php' ) ) { require_once '/etc/wp-mail/smtp.php'; } // lightsail-wp-mcp mail"
if grep -qF "/etc/wp-mail/smtp.php" "$WP_CONFIG"; then
  echo "unchanged"
else
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  LINE="$line" python3 -I - "$WP_CONFIG" "$tmp" <<'PY'
import os, sys
text = open(sys.argv[1], encoding="utf-8").read()
marker = "/* That's all, stop editing!"
if marker not in text:
    sys.exit("wp-config.php has no \"That's all, stop editing\" line: add the require by hand")
open(sys.argv[2], "w", encoding="utf-8").write(text.replace(marker, os.environ["LINE"] + "\n\n" + marker, 1))
PY
  php -l "$tmp" >/dev/null || { echo "edited wp-config.php does not parse: unchanged" >&2; exit 1; }
  cp -p "$WP_CONFIG" "$WP_CONFIG.bak.$(date -u +%Y%m%d%H%M%S)"
  cat "$tmp" >"$WP_CONFIG"
  echo "added the require line (backup next to it)"
fi

step "Reply-To mu-plugin"
mu="$(readlink -f "$WP/wp-content")/mu-plugins"
install -d -m 0755 -o root -g root "$mu"
install -m 0644 -o root -g root "$S/wp-mail-reply-to.php" "$mu/"
wp plugin list --status=must-use --fields=name --format=csv | grep -i reply

step "check"
# shellcheck disable=SC2016 # PHP, not shell
wp eval 'echo "mailer: ", WPMS_MAILER, " host: ", WPMS_SMTP_HOST, " from: ", WPMS_MAIL_FROM, " reply-to: ", WP_MAIL_REPLY_TO, "\n";'
if [[ -n "$MAIL_TEST_TO" ]]; then
  # shellcheck disable=SC2016 # PHP, not shell
  # A new access key takes a little while to work everywhere in AWS: try for two minutes.
  for attempt in 1 2 3 4 5 6 7 8; do
    # shellcheck disable=SC2016 # PHP, not shell
    if MAIL_TEST_TO="$MAIL_TEST_TO" wp eval 'exit( wp_mail( getenv( "MAIL_TEST_TO" ), "[" . get_bloginfo( "name" ) . "] mail test", "Sent through Amazon SES by bin/mail-credentials at " . gmdate( "c" ) . "." ) ? 0 : 1 );'; then
      echo "test mail sent (attempt $attempt)"
      break
    fi
    [[ "$attempt" == 8 ]] && { echo "test mail FAILED: see WP Mail SMTP > Tools > Email Log/Test" >&2; exit 1; }
    sleep 15
  done
fi
