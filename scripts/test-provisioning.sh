#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp_dir=$(mktemp -d "$root_dir/.test-provisioning.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT

fail() {
  echo "test failure: $*" >&2
  exit 1
}

make_archive() {
  local output=$1 architecture=$2 version=$3 staging
  staging=$(mktemp -d "$tmp_dir/staging.XXXXXX")
  cat >"$staging/doctl" <<EOF
#!/usr/bin/env bash
printf '{"version":"${version}-release","architecture":"${architecture}"}\\n'
EOF
  chmod 0755 "$staging/doctl"
  tar -czf "$output" -C "$staging" doctl
  rm -rf "$staging"
}

write_vars() {
  local output=$1 state_dir=$2 release_dir=$3 amd64_checksum=$4 arm64_checksum=$5
  cat >"$output" <<EOF
---
doctl_version: "1.166.0"
doctl_release_base_url: "file://${release_dir}"
doctl_cache_dir: "${state_dir}/cache"
doctl_install_path: "${state_dir}/bin/doctl"
doctl_install_owner: "$(id -un)"
doctl_install_group: "$(id -gn)"
doctl_install_mode: "0755"
doctl_archives:
  x86_64:
    arch: "amd64"
    sha256: "${amd64_checksum}"
  aarch64:
    arch: "arm64"
    sha256: "${arm64_checksum}"
EOF
  mkdir -p "$state_dir/bin"
}

run_installer() {
  local architecture=$1 vars_file=$2 output=$3
  ansible-playbook \
    --inventory 'localhost,' \
    "$root_dir/tests/fixtures/doctl-install.yml" \
    --extra-vars "@$vars_file" \
    --extra-vars "ansible_architecture=$architecture" >"$output" 2>&1
}

# Fresh public cloud-init rendering is deterministic, resolves every marker,
# and does not cross the private-package authentication boundary.
"$root_dir/scripts/render-cloud-init.sh" \
  https://github.com/example/bibi-rules-the-world.git >"$tmp_dir/cloud-a.yaml"
"$root_dir/scripts/render-cloud-init.sh" \
  https://github.com/example/bibi-rules-the-world.git >"$tmp_dir/cloud-b.yaml"
cmp "$tmp_dir/cloud-a.yaml" "$tmp_dir/cloud-b.yaml"
! grep -Eq '__BIBI_CONFIG_(REPO|REF)__' "$tmp_dir/cloud-a.yaml" || fail "cloud-init retained a render marker"
! grep -Eq 'pi-extensions|digitalocean-login|doctl[[:space:]]+auth' "$tmp_dir/cloud-a.yaml" \
  || fail "cloud-init attempts private package or doctl authentication"
! grep -Eq 'dop_v1_|github_pat_|ghp_[A-Za-z0-9]' "$tmp_dir/cloud-a.yaml" || fail "cloud-init contains a token signature"
python3 - "$tmp_dir/cloud-a.yaml" <<'PY'
import pathlib
import sys
import yaml

yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())
PY

# Exercise the production Cloudflare skill tasks against a local reviewed Git
# fixture, including migration from an unmanaged copied skill and idempotency.
skill_source="$tmp_dir/cloudflare-skills-source"
mkdir -p "$skill_source/skills/cloudflare/references" "$skill_source/skills/wrangler"
printf '%s\n' '---' 'name: cloudflare' 'description: fixture' >"$skill_source/skills/cloudflare/SKILL.md"
printf '%s\n' 'reference' >"$skill_source/skills/cloudflare/references/README.md"
printf '%s\n' '---' 'name: wrangler' 'description: fixture' >"$skill_source/skills/wrangler/SKILL.md"
git -C "$skill_source" init -q
git -C "$skill_source" add .
git -C "$skill_source" -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm 'fixture skills'
skill_ref=$(git -C "$skill_source" rev-parse HEAD)
skill_state="$tmp_dir/cloudflare-skills-state"
mkdir -p "$skill_state/links/cloudflare"
printf 'unmanaged\n' >"$skill_state/links/cloudflare/stale.txt"
cat >"$tmp_dir/cloudflare-skills-vars.yml" <<EOF
---
cloudflare_skills_repo_url: "file://${skill_source}"
cloudflare_skills_ref: "${skill_ref}"
cloudflare_skills_checkout: "${skill_state}/checkout"
cloudflare_skill_link_dir: "${skill_state}/links"
cloudflare_skill_names: [cloudflare, wrangler]
cloudflare_skills_owner: "$(id -un)"
cloudflare_skills_become: false
EOF
ansible-playbook --inventory 'localhost,' \
  "$root_dir/tests/fixtures/cloudflare-skills-install.yml" \
  --extra-vars "@$tmp_dir/cloudflare-skills-vars.yml" >"$tmp_dir/cloudflare-skills-first.log" 2>&1
[[ $(readlink -f "$skill_state/links/cloudflare") == "$skill_state/checkout/skills/cloudflare" ]] \
  || fail "cloudflare skill was not linked to the reviewed checkout"
[[ $(readlink -f "$skill_state/links/wrangler") == "$skill_state/checkout/skills/wrangler" ]] \
  || fail "wrangler skill was not linked to the reviewed checkout"
[[ ! -e "$skill_state/links/cloudflare/stale.txt" ]] || fail "unmanaged Cloudflare skill copy survived reconciliation"
ansible-playbook --inventory 'localhost,' \
  "$root_dir/tests/fixtures/cloudflare-skills-install.yml" \
  --extra-vars "@$tmp_dir/cloudflare-skills-vars.yml" >"$tmp_dir/cloudflare-skills-second.log" 2>&1
grep -Eq 'changed=0([[:space:]]|$)' "$tmp_dir/cloudflare-skills-second.log" \
  || fail "second Cloudflare skill reconciliation was not idempotent"

# Render and exercise the daily-user public Pi package reconciler with a fake
# Pi installer so exact settings, repair, and no-op behavior remain hermetic.
pi_update_home="$tmp_dir/pi-update-home"
pi_update_bin="$tmp_dir/pi-update-bin"
mkdir -p "$pi_update_home/.pi/agent" "$pi_update_bin"
python3 - "$root_dir/templates/bibi-pi-public-packages-update.j2" "$tmp_dir/pi-public-update" <<'PY'
import pathlib
import sys

template = pathlib.Path(sys.argv[1]).read_text()
rendered = template.replace(
    "{{ pi_public_packages | join(' ') }}",
    "npm:@tmustier/pi-files-widget@0.2.0 npm:pi-web-access@0.24.0",
)
pathlib.Path(sys.argv[2]).write_text(rendered)
PY
chmod 0755 "$tmp_dir/pi-public-update"
cat >"$pi_update_bin/pi" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ $1 == install ]]
source=$2
spec=${source#npm:}
package_name=${spec%@*}
version=${spec##*@}
settings_dir=${PI_CODING_AGENT_DIR:-"$HOME/.pi/agent"}
mkdir -p "$settings_dir/npm/node_modules/$package_name"
printf '{"name":"%s","version":"%s"}\n' "$package_name" "$version" \
  >"$settings_dir/npm/node_modules/$package_name/package.json"
if [[ -r "$settings_dir/settings.json" ]]; then
  jq --arg source "$source" '.packages = (((.packages // []) + [$source]) | unique)' \
    "$settings_dir/settings.json" >"$settings_dir/settings.json.new"
else
  jq -n --arg source "$source" '{packages: [$source]}' >"$settings_dir/settings.json.new"
fi
mv "$settings_dir/settings.json.new" "$settings_dir/settings.json"
printf '%s\n' "$source" >>"$PI_INSTALL_CALLS"
EOF
chmod 0755 "$pi_update_bin/pi"
pi_update_env=(
  HOME="$pi_update_home"
  PI_CODING_AGENT_DIR="$pi_update_home/.pi/agent"
  PI_INSTALL_CALLS="$tmp_dir/pi-install-calls"
  PATH="$pi_update_bin:$PATH"
)
env "${pi_update_env[@]}" "$tmp_dir/pi-public-update" >"$tmp_dir/pi-update-first.log"
[[ $(wc -l <"$tmp_dir/pi-install-calls") == 2 ]] || fail "fresh Pi packages were not both installed"
env "${pi_update_env[@]}" "$tmp_dir/pi-public-update" >"$tmp_dir/pi-update-second.log"
[[ $(wc -l <"$tmp_dir/pi-install-calls") == 2 ]] || fail "ready Pi packages were unnecessarily reinstalled"
rm -rf "$pi_update_home/.pi/agent/npm/node_modules/pi-web-access"
env "${pi_update_env[@]}" "$tmp_dir/pi-public-update" >"$tmp_dir/pi-update-repair.log"
[[ $(wc -l <"$tmp_dir/pi-install-calls") == 3 ]] || fail "missing Pi package content was not repaired"

# Build hermetic architecture-specific release archives and exercise the same
# production Ansible task file used by site.yml.
release_dir="$tmp_dir/releases"
mkdir -p "$release_dir/v1.166.0"
amd64_archive="$release_dir/v1.166.0/doctl-1.166.0-linux-amd64.tar.gz"
arm64_archive="$release_dir/v1.166.0/doctl-1.166.0-linux-arm64.tar.gz"
make_archive "$amd64_archive" amd64 1.166.0
make_archive "$arm64_archive" arm64 1.166.0
amd64_checksum=$(sha256sum "$amd64_archive" | awk '{print $1}')
arm64_checksum=$(sha256sum "$arm64_archive" | awk '{print $1}')

write_vars "$tmp_dir/amd64-vars.yml" "$tmp_dir/amd64-state" "$release_dir" "$amd64_checksum" "$arm64_checksum"
run_installer x86_64 "$tmp_dir/amd64-vars.yml" "$tmp_dir/amd64-first.log"
grep -q '"architecture":"amd64"' <("$tmp_dir/amd64-state/bin/doctl" version --output json) \
  || fail "x86_64 did not select the amd64 artifact"
run_installer x86_64 "$tmp_dir/amd64-vars.yml" "$tmp_dir/amd64-second.log"
grep -Eq 'changed=0([[:space:]]|$)' "$tmp_dir/amd64-second.log" || fail "second doctl reconciliation was not idempotent"

write_vars "$tmp_dir/arm64-vars.yml" "$tmp_dir/arm64-state" "$release_dir" "$amd64_checksum" "$arm64_checksum"
run_installer aarch64 "$tmp_dir/arm64-vars.yml" "$tmp_dir/arm64.log"
grep -q '"architecture":"arm64"' <("$tmp_dir/arm64-state/bin/doctl" version --output json) \
  || fail "aarch64 did not select the arm64 artifact"

write_vars "$tmp_dir/checksum-vars.yml" "$tmp_dir/checksum-state" "$release_dir" \
  "$(printf '0%.0s' {1..64})" "$arm64_checksum"
if run_installer x86_64 "$tmp_dir/checksum-vars.yml" "$tmp_dir/checksum.log"; then
  fail "installer accepted a checksum mismatch"
fi
[[ ! -e "$tmp_dir/checksum-state/bin/doctl" ]] || fail "checksum mismatch installed a binary"

bad_release_dir="$tmp_dir/bad-releases"
mkdir -p "$bad_release_dir/v1.166.0"
bad_archive="$bad_release_dir/v1.166.0/doctl-1.166.0-linux-amd64.tar.gz"
make_archive "$bad_archive" amd64 1.165.0
bad_checksum=$(sha256sum "$bad_archive" | awk '{print $1}')
write_vars "$tmp_dir/version-vars.yml" "$tmp_dir/version-state" "$bad_release_dir" "$bad_checksum" "$arm64_checksum"
if run_installer x86_64 "$tmp_dir/version-vars.yml" "$tmp_dir/version.log"; then
  fail "installer accepted a version mismatch"
fi
[[ ! -e "$tmp_dir/version-state/bin/doctl" ]] || fail "version mismatch installed a binary"

# Exercise bibi-verify's exact version and metadata checks with local fixtures.
verify_dir="$tmp_dir/verify"
mkdir -p "$verify_dir"
verify_doctl="$verify_dir/doctl"
cat >"$verify_doctl" <<'EOF'
#!/usr/bin/env bash
printf '{"version":"1.166.0-release"}\n'
EOF
chmod 0755 "$verify_doctl"
cat >"$verify_dir/versions" <<EOF
doctl_version=1.166.0
doctl_install_path=$verify_doctl
doctl_install_owner=$(id -un)
doctl_install_group=$(id -gn)
doctl_install_mode=0755
EOF
verify_env=(
  BIBI_VERIFY_DOCTL_ONLY=1
  BIBI_VERSIONS_FILE="$verify_dir/versions"
)
env "${verify_env[@]}" "$root_dir/scripts/verify.sh" >"$verify_dir/good.log"

cat >"$verify_doctl" <<'EOF'
#!/usr/bin/env bash
printf '{"version":"1.165.0-release"}\n'
EOF
chmod 0755 "$verify_doctl"
if env "${verify_env[@]}" "$root_dir/scripts/verify.sh" >"$verify_dir/bad-version.log" 2>&1; then
  fail "bibi-verify accepted a version mismatch"
fi

chmod 0700 "$verify_doctl"
if env "${verify_env[@]}" "$root_dir/scripts/verify.sh" >"$verify_dir/bad-mode.log" 2>&1; then
  fail "bibi-verify accepted an ownership/mode mismatch"
fi

# Exercise exact npm, Pi package, and official skill readiness checks without
# using the host's package settings or global npm installation.
cat >"$verify_doctl" <<'EOF'
#!/usr/bin/env bash
printf '{"version":"1.166.0-release"}\n'
EOF
chmod 0755 "$verify_doctl"
make_package_metadata() {
  local root=$1 spec=$2 package_name version
  package_name=${spec%@*}
  version=${spec##*@}
  mkdir -p "$root/$package_name"
  printf '{"name":"%s","version":"%s"}\n' "$package_name" "$version" >"$root/$package_name/package.json"
}

global_npm_root="$verify_dir/global-npm"
pi_home="$verify_dir/home"
mkdir -p "$global_npm_root" "$pi_home/.pi/agent/npm/node_modules"
global_specs=(
  '@earendil-works/pi-coding-agent@0.83.0'
  'wrangler@4.125.0'
  'firecrawl-cli@1.19.27'
  'gh-axi@0.1.30'
  'chrome-devtools-axi@0.1.27'
  'lavish-axi@0.1.50'
  'tasks-axi@0.2.5'
  'quota-axi@0.1.29'
)
for spec in "${global_specs[@]}"; do
  make_package_metadata "$global_npm_root" "$spec"
done
public_sources=('npm:@tmustier/pi-files-widget@0.2.0' 'npm:pi-web-access@0.24.0')
for source in "${public_sources[@]}"; do
  make_package_metadata "$pi_home/.pi/agent/npm/node_modules" "${source#npm:}"
done
printf '{"packages":["%s","%s"]}\n' "${public_sources[0]}" "${public_sources[1]}" \
  >"$pi_home/.pi/agent/settings.json"

verify_skills_checkout="$verify_dir/cloudflare-skills"
mkdir -p "$verify_skills_checkout/skills/cloudflare" "$verify_skills_checkout/skills/wrangler" "$verify_dir/skill-links"
printf '%s\n' '---' 'name: cloudflare' 'description: fixture' >"$verify_skills_checkout/skills/cloudflare/SKILL.md"
printf '%s\n' '---' 'name: wrangler' 'description: fixture' >"$verify_skills_checkout/skills/wrangler/SKILL.md"
git -C "$verify_skills_checkout" init -q
git -C "$verify_skills_checkout" add .
git -C "$verify_skills_checkout" -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm 'fixture skills'
git -C "$verify_skills_checkout" remote add origin https://github.com/cloudflare/skills.git
verify_skills_ref=$(git -C "$verify_skills_checkout" rev-parse HEAD)
ln -s "$verify_skills_checkout/skills/cloudflare" "$verify_dir/skill-links/cloudflare"
ln -s "$verify_skills_checkout/skills/wrangler" "$verify_dir/skill-links/wrangler"
cat >>"$verify_dir/versions" <<EOF
pi_package=@earendil-works/pi-coding-agent@0.83.0
wrangler_package=wrangler@4.125.0
firecrawl_cli_package=firecrawl-cli@1.19.27
axi_packages=gh-axi@0.1.30 chrome-devtools-axi@0.1.27 lavish-axi@0.1.50 tasks-axi@0.2.5 quota-axi@0.1.29
pi_public_packages=npm:@tmustier/pi-files-widget@0.2.0 npm:pi-web-access@0.24.0
cloudflare_skills_repo=https://github.com/cloudflare/skills.git
cloudflare_skills_ref=$verify_skills_ref
cloudflare_skills_checkout=$verify_skills_checkout
cloudflare_skill_link_dir=$verify_dir/skill-links
cloudflare_skill_names=cloudflare wrangler
EOF
tooling_verify_env=(
  BIBI_VERIFY_TOOLING_ONLY=1
  BIBI_VERSIONS_FILE="$verify_dir/versions"
  BIBI_NPM_GLOBAL_ROOT="$global_npm_root"
  HOME="$pi_home"
)
env "${tooling_verify_env[@]}" "$root_dir/scripts/verify.sh" >"$verify_dir/tooling-good.log"
printf '{"name":"wrangler","version":"4.124.0"}\n' >"$global_npm_root/wrangler/package.json"
if env "${tooling_verify_env[@]}" "$root_dir/scripts/verify.sh" >"$verify_dir/tooling-bad-version.log" 2>&1; then
  fail "bibi-verify accepted a Wrangler version mismatch"
fi
printf '{"name":"wrangler","version":"4.125.0"}\n' >"$global_npm_root/wrangler/package.json"
rm "$verify_dir/skill-links/wrangler"
ln -s "$verify_skills_checkout/skills/cloudflare" "$verify_dir/skill-links/wrangler"
if env "${tooling_verify_env[@]}" "$root_dir/scripts/verify.sh" >"$verify_dir/tooling-bad-skill.log" 2>&1; then
  fail "bibi-verify accepted a Wrangler skill linked to the wrong source"
fi

# The private repository appears only as inert pins and a daily-user command;
# public provisioning must never run that command or authenticate either CLI.
grep -q 'pi_extensions_ref:' "$root_dir/group_vars/all.yml" || fail "private collection ref is not authoritative config"
! grep -Eq 'bibi-pi-extensions-update|pi install|doctl auth|digitalocean-login' "$root_dir/cloud-init.yaml" \
  || fail "cloud-init crosses an interactive authentication boundary"

echo "Provisioning tests passed."
