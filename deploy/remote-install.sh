#!/bin/bash
# Runs ON THE SERVER, as root, from a staging directory prepared by bin/deploy:
#   sudo bash remote-install.sh <staging dir>
# Works on the Bitnami WordPress image and on Lightsail's own WordPress image (see
# platform.sh). The WordPress site itself must already answer over HTTPS on WP_HOST (for a
# fresh Lightsail image, run bin/bootstrap-site first).
# Idempotent. Each Apache change is syntax-checked and undone if the check fails; a new
# release that does not answer its health check is rolled back to the previous one.
# Prints no secrets.
set -euo pipefail

S="$(cd "$1" && pwd)"
# Settings of this deployment, written by bin/deploy; checked again here.
conf() { sed -n "s/^$1=//p" "$S/site.conf"; }
MCP_HOST="$(conf MCP_HOST)"
WP_HOST="$(conf WP_HOST)"
ACME_EMAIL="$(conf ACME_EMAIL)"
host_re='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
[[ "$MCP_HOST" =~ $host_re && "$WP_HOST" =~ $host_re ]] || { echo "bad site.conf" >&2; exit 1; }
[[ -z "$ACME_EMAIL" || "$ACME_EMAIL" =~ ^[^[:space:]@\"\']+@[a-z0-9.-]+$ ]] || { echo "bad ACME_EMAIL" >&2; exit 1; }
# shellcheck source=deploy/platform.sh
. "$S/platform.sh"

step() { printf '\n== %s\n' "$*"; }
random() { head -c 64 /dev/urandom | base64 -w0 | tr -d '/+=' | cut -c1-"$1"; }
echo "platform: $PLATFORM ($WP)"

step "service user and directories"
id wpmcp >/dev/null 2>&1 ||
  useradd --system --home-dir /var/lib/wpmcp --no-create-home --shell /usr/sbin/nologin wpmcp
install -d -m 0755 -o root -g root /opt/wpmcp /opt/wpmcp/releases
install -d -m 0750 -o root -g wpmcp /etc/wpmcp
install -d -m 0700 -o wpmcp -g wpmcp /var/lib/wpmcp
# Socket directory: Apache (its group) may enter it, nobody else; recreated at boot.
echo "d /run/wpmcp 0710 wpmcp $WEB_GROUP -" >/etc/tmpfiles.d/wpmcp.conf
systemd-tmpfiles --create /etc/tmpfiles.d/wpmcp.conf

step "configuration"
install -m 0640 -o root -g wpmcp "$S/wpmcp.env" /etc/wpmcp/wpmcp.env
install -m 0640 -o root -g wpmcp "$S/cidrs.txt" /etc/wpmcp/cidrs.txt
if [[ ! -s /etc/wpmcp/secret ]]; then
  (umask 027 && random 64 >/etc/wpmcp/secret)
  echo "signing secret created"
fi
chown root:wpmcp /etc/wpmcp/secret && chmod 0640 /etc/wpmcp/secret
install -m 0755 -o root -g root "$S/wpmcp-admin" /usr/local/sbin/wpmcp-admin

step "bridge plugin (mu-plugin, root-owned: the web server cannot change it)"
mu="$(readlink -f "$WP/wp-content")/mu-plugins"
install -d -m 0755 -o root -g root "$mu" "$mu/wpmcp-bridge"
install -m 0644 -o root -g root "$S/mu-plugins/wpmcp-bridge.php" "$mu/"
install -m 0644 -o root -g root "$S"/mu-plugins/wpmcp-bridge/*.php "$mu/wpmcp-bridge/"
# Extra mu-plugins of this site (EXTRA_MU_PLUGINS): installed and updated, never removed.
for extra in "$S"/mu-plugins/extra/*.php; do
  [[ -e "$extra" ]] || continue
  [[ ! -e "$mu/$(basename "$extra")" || "$(stat -c %U "$mu/$(basename "$extra")")" == root ]] ||
    { echo "refusing to replace $(basename "$extra"): not root-owned on the server" >&2; exit 1; }
  install -m 0644 -o root -g root "$extra" "$mu/"
  echo "extra mu-plugin: $(basename "$extra")"
done
wp plugin list --status=must-use --fields=name,version --format=csv 2>/dev/null | grep -i wpmcp

step "integration user"
if ! wp user get wpmcp --field=ID >/dev/null 2>&1; then
  # No --user_pass: WordPress picks a random one nobody knows; the account cannot log in.
  wp user create wpmcp "wpmcp-integration@$WP_HOST" --role=editor \
    --display_name="wpmcp (integration)" --porcelain >/dev/null
  echo "user created"
fi
if [[ ! -s /etc/wpmcp/wp-app-password ]]; then
  (umask 027 && wp user application-password create wpmcp wpmcp-service --porcelain 2>/dev/null |
    tr -d '[:space:]' >/etc/wpmcp/wp-app-password.new)
  [[ "$(wc -c </etc/wpmcp/wp-app-password.new)" -ge 24 ]] || { echo "no application password" >&2; exit 1; }
  mv /etc/wpmcp/wp-app-password.new /etc/wpmcp/wp-app-password
  echo "application password created"
fi
chown root:wpmcp /etc/wpmcp/wp-app-password && chmod 0640 /etc/wpmcp/wp-app-password
# shellcheck disable=SC2016 # PHP, not shell
wp eval 'foreach (["edit_posts","publish_posts","upload_files","unfiltered_html","manage_options","edit_users","install_plugins","edit_theme_options","aioseo_manage_seo"] as $c) { echo $c, "=", user_can(get_user_by("login","wpmcp"), $c) ? "yes" : "no", " "; } echo "\n";' 2>/dev/null

step "release"
ensure_venv
version="$(cat "$S/VERSION")"
rel="/opt/wpmcp/releases/$version-$(date -u +%Y%m%d%H%M%S)"
install -d -m 0755 -o root -g root "$rel"
python3 -m venv "$rel/venv"
pip=("$rel/venv/bin/pip" --quiet --no-cache-dir --disable-pip-version-check)
"${pip[@]}" install --require-hashes --only-binary=:all: -r "$S/requirements.txt"
"${pip[@]}" install --no-deps --no-index "$S"/wpmcp-*.whl
chown -R root:root "$rel" && chmod -R go-w "$rel"
echo "installed $rel"

step "systemd units"
install -m 0644 -o root -g root "$S/systemd/wpmcp.service" "$S/systemd/wpmcp-media.socket" \
  "$S/systemd/wpmcp-media@.service" /etc/systemd/system/
# The image worker may reach public addresses only; its one private destination is this
# instance's DNS resolver (the VPC resolver on Lightsail).
install -d -m 0755 /etc/systemd/system/wpmcp-media@.service.d
{
  echo "# Written by remote-install.sh from /etc/resolv.conf."
  echo "[Service]"
  awk '/^nameserver/ {print "IPAddressAllow=" $2}' /etc/resolv.conf
} >/etc/systemd/system/wpmcp-media@.service.d/dns.conf
systemctl daemon-reload
systemctl enable --quiet --now wpmcp-media.socket
previous="$(readlink -f /opt/wpmcp/current 2>/dev/null || true)"
ln -sfn "$rel" /opt/wpmcp/current
systemctl enable --quiet wpmcp
systemctl restart wpmcp
healthy() {
  for _ in $(seq 1 20); do
    curl -fsS --max-time 3 --unix-socket /run/wpmcp/wpmcp.sock http://wpmcp/healthz 2>/dev/null && return 0
    sleep 1
  done
  return 1
}
if healthy; then
  echo; echo "service healthy: $(systemctl show -p MemoryCurrent --value wpmcp) bytes in use"
else
  journalctl -u wpmcp -n 40 --no-pager >&2 || true
  if [[ -n "$previous" && -d "$previous" ]]; then
    ln -sfn "$previous" /opt/wpmcp/current && systemctl restart wpmcp
    echo "new release unhealthy: rolled back to $previous" >&2
  fi
  exit 1
fi

step "apache: plain HTTP vhost (ACME challenge, redirect)"
if [[ "$PLATFORM" == debian ]]; then
  a2enmod -q proxy proxy_http headers ssl rewrite >/dev/null
  install -d -m 0755 -o root -g root "$ACME_WEBROOT"
fi
apache_apply "$S/apache/$VHOST_SRC_HTTP" "$VHOST_HTTP"

step "certificate for $MCP_HOST (separate from the site's)"
if [[ ! -s "$MCP_CERT" ]]; then
  if [[ "$PLATFORM" == bitnami ]]; then
    # Reuse the existing Let's Encrypt account of this server (not printed).
    email="$(find "$LE/accounts/acme-v02.api.letsencrypt.org/" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | head -1)"
    [[ "$email" == *@* ]] || email="$ACME_EMAIL"
    [[ "$email" == *@* ]] || { echo "no Let's Encrypt account found: set ACME_EMAIL" >&2; exit 1; }
    "$LE/lego" --path "$LE" --email "$email" --accept-tos --http --http-timeout 30 \
      --http.webroot "$ACME_WEBROOT" --domains="$MCP_HOST" --user-agent wpmcp run 2>&1 |
      sed -E 's/[^ ]+@[^ ]+/<account>/g' | tail -5
    umask 077
    cat >/etc/cron.d/wpmcp-cert <<CRON
# Renews the certificate of $MCP_HOST (installed by lightsail-wp-mcp).
41 3 * * * root $LE/lego --path $LE --email $email --http --http-timeout 30 --http.webroot $ACME_WEBROOT --domains=$MCP_HOST --user-agent wpmcp renew --days 30 >/dev/null 2>&1 && chmod 600 $MCP_KEY && ${HTTPD[*]} -k graceful
CRON
    chmod 0600 /etc/cron.d/wpmcp-cert
    umask 022
  else
    certbot_issue "$MCP_HOST" "$ACME_WEBROOT"
  fi
fi
chmod 0600 "$(readlink -f "$MCP_KEY")"
openssl x509 -in "$MCP_CERT" -noout -subject -enddate

step "apache: HTTPS vhost"
apache_apply "$S/apache/$VHOST_SRC_HTTPS" "$VHOST_HTTPS"

step "checks over loopback"
probe() { curl -s -o /dev/null -w "%{http_code}" --max-time 10 --resolve "$MCP_HOST:443:127.0.0.1" "$@"; }
# A graceful reload returns before the new configuration serves: wait for it.
for _ in $(seq 1 15); do [[ "$(probe "https://$MCP_HOST/healthz")" == 200 ]] && break; sleep 1; done
echo "healthz: $(probe "https://$MCP_HOST/healthz")" \
  "metadata: $(probe "https://$MCP_HOST/.well-known/oauth-protected-resource/mcp")" \
  "POST /mcp without token: $(probe -X POST "https://$MCP_HOST/mcp")" \
  "/register: $(probe "https://$MCP_HOST/register")"
echo "site still answers: $(curl -s -o /dev/null -w '%{http_code}' --max-time 15 --resolve "$WP_HOST:443:127.0.0.1" "https://$WP_HOST/")"

step "image worker"
# A 1x1 PNG through the socket, as the service user (sandbox, Pillow, protocol), then two
# downloads: a host outside the allowlist (refused) and ChatGPT's file host (DNS and egress
# work if the answer is an HTTP status rather than a resolution error).
# shellcheck disable=SC2016 # Python, not shell
setpriv --reuid=wpmcp --regid=wpmcp --init-groups /opt/wpmcp/current/venv/bin/python -I -c '
import base64, json, socket
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
def ask(header, data=b""):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(60)
    s.connect("/run/wpmcp-media.sock")
    s.sendall(json.dumps(header).encode() + b"\n" + data)
    answer = b""
    while b"\n" not in answer:
        chunk = s.recv(4096)
        if not chunk:
            break
        answer += chunk
    s.close()
    return json.loads(answer.split(b"\n")[0])
ok = ask({"op": "sanitize", "length": len(PNG)}, PNG)
print("sanitize:", "ok" if ok.get("ok") else ok)
print("fetch outside allowlist:", ask({"op": "fetch", "url": "https://example.org/x.png"}).get("error"))
print("fetch file host:", ask({"op": "fetch", "url": "https://files.oaiusercontent.com/wpmcp-probe"}).get("error"))
'
systemd-analyze security wpmcp-media@probe.service 2>/dev/null | tail -1 || true

step "old releases"
find /opt/wpmcp/releases -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | tail -n +4 |
  cut -d' ' -f2- | xargs -r rm -rf
ls -1 /opt/wpmcp/releases
