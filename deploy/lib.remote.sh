# Sourced (as root, on the server) by every script bin/* ships: the settings of this
# deployment (site.conf in the staging directory) and helpers shared by both platforms.
# The platform's own file (platform.sh, a copy of platform.<name>.sh) is sourced after it.
# shellcheck shell=bash disable=SC2034  # the variables are used by the scripts that source this

S="$(cd "$1" && pwd)"
conf() { sed -n "s/^$1=//p" "$S/site.conf"; }
PLATFORM="$(conf PLATFORM)"
MCP_HOST="$(conf MCP_HOST)"
WP_HOST="$(conf WP_HOST)"
WP_ALIASES="$(conf WP_ALIASES)"
ACME_EMAIL="$(conf ACME_EMAIL)"
host_re='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
for _h in "$MCP_HOST" "$WP_HOST" $WP_ALIASES; do
  [[ "$_h" =~ $host_re ]] || { echo "bad site.conf: $_h" >&2; exit 1; }
done
[[ -z "$ACME_EMAIL" || "$ACME_EMAIL" =~ ^[^[:space:]@\"\']+@[a-z0-9.-]+$ ]] || { echo "bad ACME_EMAIL" >&2; exit 1; }

step() { printf '\n== %s\n' "$*"; }

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
    if [[ -n "$backup" ]]; then mv "$backup" "$dest"; else rm -f "$dest"; apache_disable "$dest"; fi
    echo "apache rejected $dest: change undone" >&2
    return 1
  fi
}

# php_patched <version>: true for a PHP release with the October 2026 fixes (CVE-2026-109618
# and CVE-2026-109619): 8.2.35, 8.3.36, 8.4.27, 8.5.12 or later. Distribution suffixes
# ("8.2.35-1~deb12u1") are ignored.
php_patched() {
  local major minor patch
  IFS=. read -r major minor patch <<<"${1%%[-+~]*}"
  [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ && "$patch" =~ ^[0-9]+$ ]] || return 1
  ((major > 8)) && return 0
  ((major == 8)) || return 1
  case "$minor" in
    0 | 1) return 1 ;;
    2) ((patch >= 35)) ;;
    3) ((patch >= 36)) ;;
    4) ((patch >= 27)) ;;
    5) ((patch >= 12)) ;;
    *) ((minor > 5)) ;;
  esac
}

# Settings for PHP on any platform, as an ini file for the PHP that serves the site.
# session.upload_progress off: the PHP project's mitigation for CVE-2026-115560.
php_hardening_ini() {
  cat <<'INI'
; Written by lightsail-wp-mcp (setup.<platform>.sh).
session.upload_progress.enabled = 0
expose_php = Off
INI
}

# This instance's public IPv4, from the instance metadata service (IMDSv2); empty if unknown.
public_ipv4() {
  local token
  token="$(curl -fsS --max-time 3 -X PUT http://169.254.169.254/latest/api/token \
    -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null)" || return 0
  curl -fsS --max-time 3 -H "X-aws-ec2-metadata-token: $token" \
    http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true
}

# dns_points_here <host>: true if the host's A record is this instance's public address (or
# the address cannot be known, in which case trying is the only way to find out).
dns_points_here() {
  local ip
  ip="$(public_ipv4)"
  [[ -z "$ip" ]] && return 0
  getent ahostsv4 "$1" | awk '{print $1}' | grep -qxF "$ip"
}
