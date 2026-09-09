#!/usr/bin/env bash
# managed-by: bibi-portable-setup
# Read-only checks shared by portable platform verifiers.
set -euo pipefail

BIBI_VERIFY_FAILED=${BIBI_VERIFY_FAILED:-0}

bibi_verify_fail() { printf 'invalid  %s\n' "$*" >&2; BIBI_VERIFY_FAILED=1; }
bibi_verify_ok() { printf 'ok       %s\n' "$*"; }

bibi_receipt_value() {
  local key=$1 count
  count=$(awk -F= -v key="$key" '$1 == key { n++ } END { print n + 0 }' "$BIBI_VERIFY_RECEIPT")
  [ "$count" = 1 ] || return 1
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$BIBI_VERIFY_RECEIPT"
}

bibi_verify_command_present() {
  if command -v "$1" >/dev/null 2>&1; then
    bibi_verify_ok "command $1"
  else
    bibi_verify_fail "missing command $1"
  fi
}

bibi_verify_command_contains() {
  local command_name=$1 expected=$2 command_path output
  shift 2
  [ -n "$expected" ] || { bibi_verify_fail "missing expected version for $command_name"; return; }
  command_path=$(command -v "$command_name" 2>/dev/null || true)
  if [ -z "$command_path" ]; then bibi_verify_fail "missing command $command_name"; return; fi
  if [ -n "${BIBI_VERIFY_BIN:-}" ] && [ "$command_path" != "$BIBI_VERIFY_BIN/$command_name" ]; then
    bibi_verify_fail "$command_name resolves outside $BIBI_VERIFY_BIN"
    return
  fi
  if [ "$#" -gt 0 ]; then
    output=$("$command_name" "$@" 2>&1 | head -n 1 || true)
  else
    output=$("$command_name" --version 2>&1 | head -n 1 || true)
  fi
  case "$output" in *"$expected"*) bibi_verify_ok "$command_name $expected" ;; *) bibi_verify_fail "$command_name expected $expected, found ${output:-no version}" ;; esac
}

bibi_receipt_npm_version() {
  local wanted=$1 specs spec package
  local -a list
  specs=$(bibi_receipt_value axi_packages 2>/dev/null || true)
  read -r -a list <<< "$specs"
  for spec in "${list[@]}"; do
    package=${spec%@*}
    if [ "$package" = "$wanted" ]; then printf '%s\n' "${spec##*@}"; return 0; fi
  done
  return 1
}

bibi_profile_selected() {
  local profiles
  profiles=$(bibi_receipt_value profiles 2>/dev/null || true)
  case ",$profiles," in *",$1,"*|*,ubuntu-compat,*) return 0 ;; *) return 1 ;; esac
}

bibi_verify_common() {
  local schema source manifest_ref active_ref fm_home pi_home backend origin dirty line key value
  local -a required_keys=(schema setup_repo_commit firstmate_manifest_ref firstmate_repo firstmate_active_ref firstmate_source
    fm_home pi_home treehouse_dir backend profiles platform fresh_home_created git_version gh_version jq_version tmux_version
    node_version node_archive_sha256 pi_version pi_registry_integrity herdr_version
    treehouse_version no_mistakes_version no_mistakes_archive_sha256 wrangler_package wrangler_registry_integrity
    firecrawl_cli_package firecrawl_registry_integrity doctl_version doctl_archive_sha256
    jdk_runtime_version jdk_archive_sha256 clojure_cli_version clojure_archive_sha256 axi_packages
    pi_public_packages cloudflare_skills_repo cloudflare_skills_ref cloudflare_skills_checkout
    cloudflare_skill_names credentials_recorded)
  if [ ! -f "$BIBI_VERIFY_RECEIPT" ] || [ -L "$BIBI_VERIFY_RECEIPT" ]; then
    bibi_verify_fail "missing regular receipt $BIBI_VERIFY_RECEIPT"
    return
  fi
  for key in "${required_keys[@]}"; do
    value=$(bibi_receipt_value "$key" 2>/dev/null || true)
    [ -n "$value" ] || bibi_verify_fail "receipt key $key is missing, empty, or duplicated"
  done
  schema=$(bibi_receipt_value schema 2>/dev/null || true)
  [ "$schema" = bibi-portable-receipt.v1 ] || bibi_verify_fail "receipt schema is ${schema:-missing}"
  [ "$(bibi_receipt_value credentials_recorded 2>/dev/null || true)" = false ] \
    || bibi_verify_fail "receipt credential boundary is missing or invalid"
  if grep -Eiq '(^|_)(token|password|secret|api_key|auth)=' "$BIBI_VERIFY_RECEIPT"; then
    bibi_verify_fail "receipt contains a forbidden credential-like key"
  else
    bibi_verify_ok "receipt is non-secret"
  fi
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in *=*) ;; *) bibi_verify_fail "malformed receipt row" ;; esac
  done < "$BIBI_VERIFY_RECEIPT"

  source=$(bibi_receipt_value firstmate_source 2>/dev/null || true)
  manifest_ref=$(bibi_receipt_value firstmate_manifest_ref 2>/dev/null || true)
  active_ref=$(git -C "$source" rev-parse HEAD 2>/dev/null || true)
  origin=$(git -C "$source" remote get-url origin 2>/dev/null || true)
  dirty=$(git -C "$source" status --porcelain --untracked-files=all 2>/dev/null || true)
  if [ -L "$source" ] || [ ! -d "$source/.git" ] || [ -L "$source/.git" ]; then
    bibi_verify_fail "Firstmate source is not a direct Git checkout"
  fi
  [ "$origin" = "$(bibi_receipt_value firstmate_repo 2>/dev/null || true)" ] || bibi_verify_fail "Firstmate source remote differs"
  [ -z "$dirty" ] || bibi_verify_fail "Firstmate source has local changes"
  [ "$active_ref" = "$(bibi_receipt_value firstmate_active_ref 2>/dev/null || true)" ] \
    || bibi_verify_fail "Firstmate active ref differs from receipt"
  if [ "$active_ref" = "$manifest_ref" ] || git -C "$source" merge-base --is-ancestor "$manifest_ref" "$active_ref" 2>/dev/null; then
    bibi_verify_ok "Firstmate $active_ref (manifest floor $manifest_ref)"
  else
    bibi_verify_fail "Firstmate $active_ref is not the manifest pin or a guarded descendant"
  fi

  fm_home=$(bibi_receipt_value fm_home 2>/dev/null || true)
  pi_home=$(bibi_receipt_value pi_home 2>/dev/null || true)
  backend=$(bibi_receipt_value backend 2>/dev/null || true)
  case "$backend" in herdr|tmux) ;; *) bibi_verify_fail "unsupported backend ${backend:-missing}" ;; esac
  case "$(bibi_receipt_value fresh_home_created 2>/dev/null || true)" in
    true|false) ;; *) bibi_verify_fail "fresh-home receipt value is invalid" ;;
  esac
  if [ ! -d "$fm_home" ] || [ -L "$fm_home" ]; then bibi_verify_fail "invalid FM_HOME $fm_home"; fi
  if [ ! -d "$pi_home" ] || [ -L "$pi_home" ]; then bibi_verify_fail "invalid Pi home $pi_home"; fi
  if [ ! -f "$fm_home/config/backend" ] || [ -L "$fm_home/config/backend" ]; then
    bibi_verify_fail "backend config is not a direct regular file"
  elif [ "$(head -n 1 "$fm_home/config/backend" 2>/dev/null || true)" != "$backend" ]; then
    bibi_verify_fail "backend config differs from receipt"
  fi
  if [ ! -f "$fm_home/config/crew-harness" ] || [ -L "$fm_home/config/crew-harness" ]; then
    bibi_verify_fail "crew harness config is not a direct regular file"
  elif [ "$(head -n 1 "$fm_home/config/crew-harness" 2>/dev/null || true)" != pi ]; then
    bibi_verify_fail "crew harness is not pi"
  fi
  if [ "$backend" = herdr ]; then
    if [ -f "$pi_home/extensions/herdr-agent-state.ts" ] \
      && [ ! -L "$pi_home/extensions/herdr-agent-state.ts" ] \
      && PI_CODING_AGENT_DIR="$pi_home" herdr integration status 2>/dev/null | grep -q '^pi: current '; then
      bibi_verify_ok "Herdr Pi lifecycle integration in $pi_home"
    else
      bibi_verify_fail "Herdr Pi lifecycle integration absent or mismatched in $pi_home"
    fi
  fi
}
