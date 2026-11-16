#!/bin/bash
# Runs ON THE SERVER, as root, from a staging directory prepared by bin/deploy:
#   sudo bash remote-install.sh <staging dir>
# Idempotent. Each Apache change is syntax-checked and undone if the check fails; a new
# release that does not answer its health check is rolled back to the previous one.
# Prints no secrets.
set -euo pipefail

S="$(cd "$1" && pwd)"
# Host names of this deployment, written by bin/deploy; checked again here.
MCP_HOST="$(sed -n 's/^MCP_HOST=//p' "$S/site.conf")"
WP_HOST="$(sed -n 's/^WP_HOST=//p' "$S/site.conf")"
host_re='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
[[ "$MCP_HOST" =~ $host_re && "$WP_HOST" =~ $host_re ]] || { echo "bad site.conf" >&2; exit 1; }
DOMAIN="$MCP_HOST"
WP=/opt/bitnami/wordpress
WPCLI=/opt/bitnami/wp-cli/bin/wp
VHOSTS=/opt/bitnami/apache/conf/vhosts
LE=/opt/bitnami/letsencrypt
CERT="$LE/certificates/$DOMAIN"
HTTPD=(/opt/bitnami/apache/bin/httpd -f /opt/bitnami/apache/conf/httpd.conf)

step() { printf '\n== %s\n' "$*"; }
wp() { "$WPCLI" --path="$WP" "$@"; }   # Bitnami's wrapper drops from root to daemon itself
random() { head -c 64 /dev/urandom | base64 -w0 | tr -d '/+=' | cut -c1-"$1"; }

apache_apply() {  # apache_apply <source> <dest>: install, check, reload; undo on failure
  local src="$1" dest="$2" backup=""
  if cmp -s "$src" "$dest" 2>/dev/null; then echo "unchanged: $dest"; return 0; fi
  [[ -e "$dest" ]] && { backup="$dest.bak.$$"; cp -p "$dest" "$backup"; }
  install -m 0644 -o root -g root "$src" "$dest"
  if "${HTTPD[@]}" -t 2>&1; then
    "${HTTPD[@]}" -k graceful
    rm -f "$backup"; echo "applied: $dest"
  else
    if [[ -n "$backup" ]]; then mv "$backup" "$dest"; else rm -f "$dest"; fi
    echo "apache rejected $dest: change undone" >&2
    return 1
  fi
}

step "service user and directories"
id wpmcp >/dev/null 2>&1 ||
  useradd --system --home-dir /var/lib/wpmcp --no-create-home --shell /usr/sbin/nologin wpmcp
install -d -m 0755 -o root -g root /opt/wpmcp /opt/wpmcp/releases
install -d -m 0750 -o root -g wpmcp /etc/wpmcp
install -d -m 0700 -o wpmcp -g wpmcp /var/lib/wpmcp
# Socket directory: Apache (group daemon) may enter it, nobody else; recreated at boot.
echo 'd /run/wpmcp 0710 wpmcp daemon -' >/etc/tmpfiles.d/wpmcp.conf
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
install -d -m 0755 -o root -g root "$mu/wpmcp-bridge"
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
apache_apply "$S/apache/mcp-vhost.conf" "$VHOSTS/mcp-vhost.conf"

step "certificate for $DOMAIN (separate from the site's)"
if [[ ! -s "$CERT.crt" ]]; then
  # Reuse the existing Let's Encrypt account of this server (not printed).
  email="$(find "$LE/accounts/acme-v02.api.letsencrypt.org/" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | head -1)"
  [[ "$email" == *@* ]] || { echo "no Let's Encrypt account found" >&2; exit 1; }
  "$LE/lego" --path "$LE" --email "$email" --accept-tos --http --http-timeout 30 \
    --http.webroot /opt/bitnami/apps/letsencrypt --domains="$DOMAIN" --user-agent wpmcp run 2>&1 |
    sed -E 's/[^ ]+@[^ ]+/<account>/g' | tail -5
  umask 077
  cat >/etc/cron.d/wpmcp-cert <<CRON
# Renews the certificate of $DOMAIN (installed by lightsail-wp-mcp).
41 3 * * * root $LE/lego --path $LE --email $email --http --http-timeout 30 --http.webroot /opt/bitnami/apps/letsencrypt --domains=$DOMAIN --user-agent wpmcp renew --days 30 >/dev/null 2>&1 && chmod 600 $CERT.key && ${HTTPD[*]} -k graceful
CRON
  chmod 0600 /etc/cron.d/wpmcp-cert
  umask 022
fi
chmod 0600 "$CERT.key"
openssl x509 -in "$CERT.crt" -noout -subject -enddate

step "apache: HTTPS vhost"
apache_apply "$S/apache/mcp-https-vhost.conf" "$VHOSTS/mcp-https-vhost.conf"

step "checks over loopback"
probe() { curl -s -o /dev/null -w "%{http_code}" --max-time 10 --resolve "$DOMAIN:443:127.0.0.1" "$@"; }
echo "healthz: $(probe "https://$DOMAIN/healthz")" \
  "metadata: $(probe "https://$DOMAIN/.well-known/oauth-protected-resource/mcp")" \
  "POST /mcp without token: $(probe -X POST "https://$DOMAIN/mcp")" \
  "/register: $(probe "https://$DOMAIN/register")"
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
