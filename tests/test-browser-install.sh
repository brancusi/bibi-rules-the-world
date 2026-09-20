#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
fail() { echo "browser fixture: $*" >&2; exit 1; }
mkdir -p "$TMP/bin" "$TMP/source" "$TMP/installed"
printf '{"version":"153.0.8010.52-1"}\n' >"$TMP/source/google-chrome-stable_153.0.8010.52-1_amd64.deb"
cat >"$TMP/bin/dpkg-query" <<'EOF'
#!/bin/sh
exec /usr/bin/head -c 100 "$BROWSER_FIXTURE_ROOT/version"
EOF
chmod +x "$TMP/bin/dpkg-query"
# Redirect exactly the privileged apt action to the synthetic adapter. The
# production assertions/download/checksum/version/stat tasks execute unchanged.
python3 - "$ROOT/tasks/install-chrome-package.yml" "$TMP/tasks.yml" <<'PY'
import pathlib
import sys
source = pathlib.Path(sys.argv[1]).read_text()
assert source.count('ansible.builtin.apt:') == 1
pathlib.Path(sys.argv[2]).write_text(source.replace('ansible.builtin.apt:', 'ansible.legacy.apt:'))
PY
checksum=$(sha256sum "$TMP/source/"*.deb | cut -d ' ' -f 1)
cat >"$TMP/vars.yml" <<EOF
ansible_distribution: Ubuntu
ansible_distribution_version: '24.04'
ansible_architecture: x86_64
chrome_version: '153.0.8010.52-1'
chrome_deb_sha256: '$checksum'
chrome_deb_base_url: 'file://$TMP/source'
chrome_cache_dir: '$TMP/cache'
chrome_executable: '$TMP/installed/chrome'
browser_system_owner: '$(id -un)'
browser_system_group: '$(id -gn)'
browser_fixture_bin: '$TMP/bin'
browser_fixture_tasks: '$TMP/tasks.yml'
EOF
run() {
  ANSIBLE_ACTION_PLUGINS="$ROOT/tests/fixtures/action_plugins" \
    ansible-playbook -i localhost, "$ROOT/tests/fixtures/browser-install.yml" \
    -e "@$TMP/vars.yml" "$@" >"$TMP/log" 2>&1
}
run || { tail -40 "$TMP/log"; fail 'clean install'; }
[[ $(<"$TMP/installed/version") == 153.0.8010.52-1 ]] || fail 'wrong clean version'
run || { tail -40 "$TMP/log"; fail 'rerun'; }
grep -Eq 'changed=0([[:space:]]|$)' "$TMP/log" || fail 'not idempotent'
printf '150.0.0.1-1' >"$TMP/installed/version"
run || { tail -40 "$TMP/log"; fail 'upgrade'; }
[[ $(<"$TMP/installed/version") == 153.0.8010.52-1 ]] || fail 'wrong upgraded version'
printf '999.0.0.1-1' >"$TMP/installed/version"
if run; then fail 'accepted downgrade'; fi
[[ $(<"$TMP/installed/version") == 999.0.0.1-1 ]] || fail 'modified newer browser'
if run -e ansible_architecture=aarch64; then fail 'accepted Linux ARM'; fi
if run -e ansible_distribution=Darwin; then fail 'accepted macOS'; fi
if run -e chrome_deb_sha256=0000000000000000000000000000000000000000000000000000000000000000; then fail 'accepted wrong checksum'; fi
if run -e chrome_version=latest; then fail 'accepted moving version'; fi
echo 'browser fixture: clean/rerun/upgrade, no downgrade, checksum and platform refusal passed (synthetic apt adapter)'
