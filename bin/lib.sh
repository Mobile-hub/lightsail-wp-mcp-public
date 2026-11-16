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

# Settings of one deployment (bin/deploy, bin/inventory), outside .env so that the site's
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
