#!/usr/bin/env bash
set -euo pipefail

versions_file=${BIBI_VERSIONS_FILE:-/etc/bibi-provisioned-versions}
failed=0

read_pin() {
  local key=$1
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; found=1; exit } END { if (!found) exit 1 }' "$versions_file"
}

verify_doctl() {
  local doctl_path expected_version expected_owner expected_group expected_mode actual_metadata version_json actual_version
  doctl_path=${BIBI_DOCTL_PATH:-$(read_pin doctl_install_path)}
  expected_version=$(read_pin doctl_version)
  expected_owner=${BIBI_DOCTL_OWNER:-$(read_pin doctl_install_owner)}
  expected_group=${BIBI_DOCTL_GROUP:-$(read_pin doctl_install_group)}
  expected_mode=${BIBI_DOCTL_MODE:-$(read_pin doctl_install_mode)}
  expected_mode=${expected_mode#0}

  if [[ ! -f "$doctl_path" || -L "$doctl_path" || ! -x "$doctl_path" ]]; then
    printf 'invalid  doctl must be a regular executable at %s\n' "$doctl_path" >&2
    failed=1
    return
  fi

  actual_metadata=$(stat -c '%U:%G %a' "$doctl_path")
  if [[ "$actual_metadata" != "$expected_owner:$expected_group $expected_mode" ]]; then
    printf 'invalid  doctl ownership/mode: expected %s:%s %s, found %s\n' \
      "$expected_owner" "$expected_group" "$expected_mode" "$actual_metadata" >&2
    failed=1
  else
    printf 'ok       doctl ownership/mode %s\n' "$actual_metadata"
  fi

  if ! version_json=$("$doctl_path" version --output json 2>/dev/null) \
    || ! actual_version=$(jq -er '.version | strings' <<<"$version_json" 2>/dev/null); then
    echo "invalid  doctl did not return its JSON version record" >&2
    failed=1
  elif [[ "$actual_version" != "$expected_version-release" ]]; then
    printf 'invalid  doctl version: expected %s-release, found %s\n' \
      "$expected_version" "$actual_version" >&2
    failed=1
  else
    printf 'ok       doctl %s\n' "$actual_version"
  fi
}

if [[ ! -r "$versions_file" ]]; then
  printf 'missing  provisioned version record %s\n' "$versions_file" >&2
  exit 1
fi

verify_doctl

if [[ ${BIBI_VERIFY_DOCTL_ONLY:-0} == 1 ]]; then
  exit "$failed"
fi

for command_name in node npm git gh jq pi herdr treehouse no-mistakes firecrawl \
  gh-axi chrome-devtools-axi lavish-axi tasks-axi quota-axi; do
  if command -v "$command_name" >/dev/null 2>&1; then
    printf 'ok       %s\n' "$command_name"
  else
    printf 'missing  %s\n' "$command_name" >&2
    failed=1
  fi
done

printf '\nVersions\n'
node --version || true
npm --version || true
pi --version || true
herdr --version || true
treehouse --version || true
no-mistakes --version || true
if command -v firecrawl >/dev/null 2>&1; then
  firecrawl --version 2>/dev/null || firecrawl --help 2>/dev/null | head -n 1 || true
else
  echo "firecrawl not installed"
fi

printf '\nPrivate Pi collection\n'
pi_extensions_repo=$(read_pin pi_extensions_repo)
pi_extensions_ref=$(read_pin pi_extensions_ref)
pi_extensions_version=$(read_pin pi_extensions_package_version)
pi_extensions_source=$(read_pin pi_extensions_source)
printf 'expected source: %s\n' "$pi_extensions_source"
printf 'expected DigitalOcean package: %s\n' "$pi_extensions_version"

settings_file="$HOME/.pi/agent/settings.json"
if [[ ! -e "$settings_file" ]]; then
  echo "pending  authenticate GitHub and run bibi-pi-extensions-update"
elif ! jq -e '(.packages // []) | type == "array"' "$settings_file" >/dev/null 2>&1; then
  printf 'invalid  Pi settings package list in %s\n' "$settings_file" >&2
  failed=1
elif jq -e --arg source "$pi_extensions_source" \
  'any((.packages // [])[]; (if type == "string" then . else .source end) == $source)' \
  "$settings_file" >/dev/null; then
  checkout_rel=${pi_extensions_repo#https://}
  checkout_rel=${checkout_rel%.git}
  checkout="$HOME/.pi/agent/git/$checkout_rel"
  if [[ $(git -C "$checkout" rev-parse HEAD 2>/dev/null || true) != "$pi_extensions_ref" ]]; then
    echo "invalid  private Pi collection checkout is not at its configured pin" >&2
    failed=1
  elif [[ $(jq -r '.version // empty' "$checkout/packages/digitalocean/package.json" 2>/dev/null || true) != "$pi_extensions_version" ]]; then
    echo "invalid  installed DigitalOcean package version differs from the reviewed pin" >&2
    failed=1
  else
    printf 'ok       private Pi collection %s\n' "$pi_extensions_ref"
  fi
elif jq -e --arg repo "$pi_extensions_repo" \
  'any((.packages // [])[]; ((if type == "string" then . else .source end) // "") | startswith($repo))' \
  "$settings_file" >/dev/null; then
  echo "invalid  private Pi collection is configured at a different or unpinned ref" >&2
  echo "         run bibi-pi-extensions-update to reconcile it" >&2
  failed=1
else
  echo "pending  authenticate GitHub and run bibi-pi-extensions-update"
fi

printf '\nFirstMate\n'
git -C "$HOME/firstmate" status --short --branch || failed=1
printf 'backend: '
head -n 1 "$HOME/firstmate/config/backend" || failed=1
printf 'crew harness: '
head -n 1 "$HOME/firstmate/config/crew-harness" || failed=1

if sudo -n true 2>/dev/null; then
  echo "security error: the daily agent user unexpectedly has passwordless sudo" >&2
  failed=1
else
  echo "ok       daily agent has no passwordless sudo"
fi

exit "$failed"
