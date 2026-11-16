#!/usr/bin/env bash
# Shared helpers for bin/* scripts. Source it, don't run it.
# Adapted from editorial.workflow bin/lib.sh (see PROVENANCE.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
cd "$ROOT"

# shellcheck disable=SC2034  # colors are used by the scripts that source this file
if [[ -t 1 ]]; then
  C_RED=$'\e[31m' C_GRN=$'\e[32m' C_YLW=$'\e[33m' C_BLU=$'\e[34m' C_DIM=$'\e[2m' C_OFF=$'\e[0m'
else
  C_RED='' C_GRN='' C_YLW='' C_BLU='' C_DIM='' C_OFF=''
fi

info() { printf '%s==>%s %s\n' "$C_BLU" "$C_OFF" "$*"; }
ok()   { printf '%s ok %s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn() { printf '%swarn%s %s\n' "$C_YLW" "$C_OFF" "$*" >&2; }
fail() { printf '%sfail%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }
die()  { fail "$*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Read KEY from .env without sourcing it (values may contain spaces or symbols).
env_get() {
  local key="$1" default="${2:-}" line
  if [[ -f "$ENV_FILE" ]] && line=$(grep -E "^${key}=" "$ENV_FILE" | tail -n1); then
    line="${line#*=}"; line="${line%\"}"; line="${line#\"}"
    printf '%s' "${line:-$default}"
  else
    printf '%s' "$default"
  fi
}

# Settings of one deployment (bin/deploy, bin/setup-server, ...), outside .env so that the site's
# host names never need to be in the repository. See deploy/site.env.example.
SITE_ENV="${SITE_ENV:-$ROOT/deploy/site.env}"

# setting KEY [default]: the environment first, then $SITE_ENV, then .env; "~/" expanded.
setting() {
  local key="$1" value
  value="${!key:-}"
  [[ -n "$value" ]] || value="$(ENV_FILE="$SITE_ENV" env_get "$key")"
  [[ -n "$value" ]] || value="$(env_get "$key" "${2:-}")"
  printf '%s' "${value/#\~\//$HOME/}"
}

HOST_RE='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'

# The server's platform, from PLATFORM in the site file (`terraform output platform` gives
# it for a server made with infra/). Nothing detects it: it picks which
# deploy/platform.<name>.sh and setup.<name>.sh the server gets.
site_platform() {
  local platform
  platform="$(setting PLATFORM)"
  [[ "$platform" == bitnami || "$platform" == debian ]] ||
    die "set PLATFORM=bitnami or PLATFORM=debian in $SITE_ENV"
  printf '%s' "$platform"
}

# ssh_setup [PREFIX]: the target and ssh options from <PREFIX>_SSH, <PREFIX>_SSH_KEY and
# <PREFIX>_KNOWN_HOSTS (default DEPLOY_*), into the globals ssh_target and ssh_args.
ssh_setup() {
  local prefix="${1:-DEPLOY}" key known
  ssh_target="$(setting "${prefix}_SSH" "$(setting DEPLOY_SSH)")"
  key="$(setting "${prefix}_SSH_KEY" "$(setting DEPLOY_SSH_KEY)")"
  known="$(setting "${prefix}_KNOWN_HOSTS" "$(setting DEPLOY_KNOWN_HOSTS)")"
  [[ -n "$ssh_target" ]] || die "set ${prefix}_SSH (user@host) in $SITE_ENV"
  [[ -z "$key" || -r "$key" ]] || die "cannot read ${prefix}_SSH_KEY: $key"
  ssh_args=(-o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=yes)
  [[ -n "$key" ]] && ssh_args+=(-i "$key" -o IdentitiesOnly=yes)
  [[ -n "$known" ]] && ssh_args+=(-o UserKnownHostsFile="$known")
  return 0
}

# ship <stage dir> <script>: copy the staging directory to a temporary directory on the
# server and run <script> from it as root; the directory is removed afterwards.
ship() {
  # shellcheck disable=SC2016 # expanded on the server
  tar -C "$1" -cz . | ssh "${ssh_args[@]}" "$ssh_target" \
    'd="$(mktemp -d)"; trap "rm -rf \"$d\"" EXIT; tar -C "$d" -xz && sudo -n bash "$d/'"$2"'" "$d"'
}

# render <template> <output> KEY=VALUE...: replaces @KEY@; nothing else is expanded, and a
# placeholder left over is an error.
render() {
  python3 -I -c 'import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
for pair in sys.argv[3:]:
    key, value = pair.split("=", 1)
    text = text.replace("@" + key + "@", value)
left = re.findall(r"@[A-Z_]+@", text)
if left:
    sys.exit(f"{sys.argv[1]}: unknown placeholder {left[0]}")
open(sys.argv[2], "w", encoding="utf-8").write(text)' "$@"
}

# Site settings every server-side script reads (from site.conf in its staging directory).
# WP_ALIASES: other names of the site (e.g. www.example.org), redirected to WP_HOST.
site_conf() {
  local wp_host mcp_host acme_email aliases alias
  wp_host="$(setting WP_HOST)"
  mcp_host="$(setting MCP_HOST)"
  acme_email="$(setting ACME_EMAIL)"
  aliases="$(setting WP_ALIASES)"
  [[ "$wp_host" =~ $HOST_RE ]] || die "WP_HOST must be a lower-case host name, e.g. example.org"
  [[ "$mcp_host" =~ $HOST_RE ]] || die "MCP_HOST must be a lower-case host name, e.g. mcp.example.org"
  [[ "$mcp_host" != "$wp_host" ]] || die "MCP_HOST needs its own name, not the site's"
  for alias in ${aliases//,/ }; do
    [[ "$alias" =~ $HOST_RE ]] || die "WP_ALIASES: not a host name: $alias"
  done
  [[ -z "$acme_email" || "$acme_email" =~ ^[^[:space:]@\"\']+@[a-z0-9.-]+$ ]] ||
    die "ACME_EMAIL is not an e-mail address"
  printf 'PLATFORM=%s\nMCP_HOST=%s\nWP_HOST=%s\nWP_ALIASES=%s\nACME_EMAIL=%s\n' \
    "$(site_platform)" "$mcp_host" "$wp_host" "${aliases//,/ }" "$acme_email"
}

# Set KEY=VALUE in .env (replace or append).
env_set() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k{print k "=" v; next} {print}' \
      "$ENV_FILE" >"$tmp"
  else
    cat "$ENV_FILE" >"$tmp" 2>/dev/null || true
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  mv "$tmp" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

find_uv() {
  local candidate
  for candidate in "${UV:-}" "$(command -v uv 2>/dev/null || true)" \
    "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    [[ -n "$candidate" && -x "$candidate" ]] && { echo "$candidate"; return 0; }
  done
  return 1
}

# env -u: an active conda env or venv must not leak into the resolution.
run_uv() {
  local uv
  uv="$(find_uv)" || die "uv is required: https://docs.astral.sh/uv/"
  env -u VIRTUAL_ENV -u CONDA_PREFIX -u PYTHONPATH -u PYTHONHOME "$uv" "$@"
}

random_secret() {
  if have openssl; then openssl rand -base64 48 | tr -d '\n/+=' | cut -c1-"${1:-48}"
  else head -c 64 /dev/urandom | base64 | tr -d '\n/+=' | cut -c1-"${1:-48}"; fi
}

# Run the wpmcp CLI from the local venv with the variables of .env exported to it only.
wpmcp() {
  local line key
  local -a vars=()
  if [[ -f "$ENV_FILE" ]]; then
    while IFS= read -r line; do
      [[ "$line" =~ ^[A-Z_][A-Z0-9_]*= ]] || continue
      key="${line%%=*}"
      vars+=("$key=$(env_get "$key")")
    done <"$ENV_FILE"
  fi
  env ${vars[@]+"${vars[@]}"} "$ROOT/.venv/bin/wpmcp" "$@"
}
