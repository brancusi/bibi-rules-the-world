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

# The private repository appears only as inert pins and a daily-user command;
# public provisioning must never run that command or authenticate either CLI.
grep -q 'pi_extensions_ref:' "$root_dir/group_vars/all.yml" || fail "private collection ref is not authoritative config"
! grep -Eq 'bibi-pi-extensions-update|pi install|doctl auth|digitalocean-login' "$root_dir/cloud-init.yaml" \
  || fail "cloud-init crosses an interactive authentication boundary"

echo "Provisioning tests passed."
