#!/usr/bin/env bash
# Ubuntu adapter: preserve the reviewed Ansible/cloud-init policy and make its
# formerly implicit complete tool set an explicit ubuntu-compat profile.
set -euo pipefail

# shellcheck source=scripts/setup-common.sh
. "$BIBI_ROOT/scripts/setup-common.sh"

BIBI_MODE=${BIBI_MODE:?bibi dispatcher must set BIBI_MODE}
[ "$BIBI_OS" = Linux ] || bibi_die "Ubuntu adapter received $BIBI_OS"
case "$BIBI_ARCH" in x86_64|aarch64|arm64) ;; *) bibi_die "unsupported Ubuntu architecture $BIBI_ARCH" ;; esac
if bibi_has_profile browser && [ "$BIBI_ARCH" != x86_64 ]; then
  bibi_die "browser profile installs official Chrome on Ubuntu x86_64 only; ARM needs a separately reviewed native browser prerequisite"
fi

ubuntu_release() {
  local file=${BIBI_OS_RELEASE_FILE:-/etc/os-release} id version major minor
  [ -r "$file" ] || bibi_die "cannot read $file"
  id=$(awk -F= '$1 == "ID" { value=$2; if (substr(value,1,1) == "\"") value=substr(value,2,length(value)-2); print value; exit }' "$file")
  version=$(awk -F= '$1 == "VERSION_ID" { value=$2; if (substr(value,1,1) == "\"") value=substr(value,2,length(value)-2); print value; exit }' "$file")
  [ "$id" = ubuntu ] || bibi_die "Linux adapter supports Ubuntu only, found ${id:-unknown}"
  [[ "$version" =~ ^[0-9]+\.[0-9]+$ ]] || bibi_die "Ubuntu VERSION_ID is not numeric: ${version:-unknown}"
  major=${version%%.*}
  minor=${version#*.}
  if [ "$major" -lt 24 ] || { [ "$major" -eq 24 ] && [ "$minor" -lt 4 ]; }; then
    bibi_die "Ubuntu 24.04 or newer is required, found $version"
  fi
  printf '%s\n' "$version"
}

profiles_json() {
  local item first=true old_ifs=$IFS
  printf '['
  IFS=,
  for item in $BIBI_PROFILES; do
    [ "$first" = true ] || printf ','
    first=false
    printf '"%s"' "$item"
  done
  IFS=$old_ifs
  printf ']'
}

ubuntu_plan() {
  local release
  release=$(ubuntu_release)
  cat <<EOF
PLAN ONLY - no files, packages, services, accounts, settings, or credentials will change.
Platform: Ubuntu $release ($BIBI_ARCH)
Backend: $BIBI_BACKEND
Profiles: $BIBI_PROFILES
Apply path: install ansible-core when missing, then run the existing local site.yml policy with explicit profile/backend variables.
Existing cloud-init remains compatible and defaults to profile ubuntu-compat.
Firstmate: $(bibi_yaml_scalar firstmate_repo_url)@$(bibi_yaml_scalar firstmate_ref), consumed unmodified.
Pi: $(bibi_yaml_scalar pi_version); no-mistakes: $(bibi_yaml_scalar no_mistakes_version).
Fresh operational home: $(bibi_yaml_scalar firstmate_instance_home)
Fresh Pi home: $(bibi_yaml_scalar bibi_pi_home)
Herdr Pi lifecycle integration: official bundled integration, installed only when absent into that exact Pi home.
No projects, backlog, state, credentials, grants, trust decisions, or sessions are imported.
EOF
  if bibi_has_profile browser; then
    printf '%s\n' 'Browser: checksum-pinned official Chrome Stable and pinned MCP; sandboxed daily-user AXI preflight. For browser-only reconciliation use browser.yml, not this full-machine apply.'
  fi
  if bibi_has_profile private-capabilities; then
    printf '%s\n' 'Private capabilities remain pending. After logging in as the daily user, run gh auth login and bibi-private-capabilities-update with a private exact-ref manifest.'
  fi
}

run_privileged() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; else "${BIBI_SUDO:-sudo}" "$@"; fi
}

ubuntu_apply() {
  local release extra
  release=$(ubuntu_release)
  bibi_note "Applying reviewed Ubuntu policy to Ubuntu $release ($BIBI_ARCH)."
  if ! command -v ansible-playbook >/dev/null 2>&1; then
    run_privileged apt-get update
    run_privileged apt-get install -y ansible-core
  fi
  extra="{\"bibi_profiles\":$(profiles_json),\"bibi_backend\":\"$BIBI_BACKEND\"}"
  run_privileged ansible-playbook -i 'localhost,' -c local "$BIBI_ROOT/site.yml" --extra-vars "$extra"
  bibi_note 'Ubuntu apply complete. Credentials, Pi trust, provider login, and private capability access remain fresh interactive steps.'
}

if [ "$BIBI_MODE" = plan ]; then ubuntu_plan; else ubuntu_apply; fi
