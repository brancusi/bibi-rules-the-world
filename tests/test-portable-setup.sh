#!/usr/bin/env bash
# Static and mocked portability checks. These are not real macOS acceptance.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
# Keep the mocked target under the direct checkout path. macOS aliases /tmp to
# /private/tmp; using it as a fake HOME would correctly trip the symlink guard.
TMP=$(mktemp -d "$ROOT/.bibi-portable.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_contains() { [[ "$1" == *"$2"* ]] || fail "expected '$2'"; }

# Read-only Ubuntu and mocked-Darwin plans must not create the target home.
printf '%s\n' 'ID=ubuntu' 'VERSION_ID="24.04"' > "$TMP/os-release"
linux_plan=$(BIBI_SETUP_TESTING=1 BIBI_TEST_OS=Linux BIBI_TEST_ARCH=x86_64 \
  BIBI_OS_RELEASE_FILE="$TMP/os-release" BIBI_TARGET_HOME="$TMP/linux-home" \
  "$ROOT/bin/bibi-setup" --plan)
assert_contains "$linux_plan" "PLAN ONLY"
assert_contains "$linux_plan" "Profiles: ubuntu-compat"
[[ ! -e "$TMP/linux-home" ]] || fail "Linux plan wrote target home"

mac_plan=$(BIBI_SETUP_TESTING=1 BIBI_TEST_OS=Darwin BIBI_TEST_ARCH=arm64 \
  BIBI_TARGET_HOME="$TMP/mac-home" "$ROOT/bin/bibi-setup" --plan --profile base)
assert_contains "$mac_plan" "Darwin-arm64"
assert_contains "$mac_plan" "provisional"
assert_contains "$mac_plan" "$TMP/mac-home/.local/share/firstmate/source/40c50ea8843c5b6a5351db8352675537252b653e"
assert_contains "$mac_plan" "Herdr Pi lifecycle integration"
[[ ! -e "$TMP/mac-home" ]] || fail "macOS plan wrote target home"

mac_browser_plan=$(BIBI_SETUP_TESTING=1 BIBI_TEST_OS=Darwin BIBI_TEST_ARCH=arm64 \
  BIBI_TARGET_HOME="$TMP/mac-browser-home" "$ROOT/bin/bibi-setup" --plan --profile browser)
assert_contains "$mac_browser_plan" "disposable local-page smoke"
[[ ! -e "$TMP/mac-browser-home" ]] || fail "macOS browser plan wrote target home"

if BIBI_SETUP_TESTING=1 BIBI_TEST_OS=Darwin BIBI_TEST_ARCH=x86_64 \
  "$ROOT/bin/bibi-setup" --plan >"$TMP/intel.out" 2>&1; then
  fail "Intel macOS was accepted"
fi
grep -q 'Intel macOS is out of scope' "$TMP/intel.out" || fail "Intel refusal was unclear"

# Private auth is checked before Homebrew or any target path can mutate.
mkdir -p "$TMP/fakebin"
cat > "$TMP/fakebin/xcode-select" <<'SH'
#!/usr/bin/env bash
[[ ${1:-} == -p ]]
SH
cat > "$TMP/fakebin/brew" <<'SH'
#!/usr/bin/env bash
echo called >> "${BREW_CALLED:?}"
SH
cat > "$TMP/fakebin/gh" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "$TMP/fakebin/"*
cat > "$TMP/private.manifest" <<'EOF'
schema=bibi-private-capabilities.v1
source=git:https://github.com/EXAMPLE/reviewed.git@0123456789abcdef0123456789abcdef01234567
EOF
if BIBI_SETUP_TESTING=1 BIBI_TEST_OS=Linux BIBI_TEST_ARCH=x86_64 \
  BIBI_OS_RELEASE_FILE="$TMP/os-release" BIBI_TARGET_HOME="$TMP/linux-private-home" \
  "$ROOT/bin/bibi-setup" --plan --profile private-capabilities \
  --private-manifest "$TMP/private.manifest" >"$TMP/linux-private.out" 2>&1; then
  fail "Ubuntu dispatcher silently accepted a caller manifest under the privileged phase"
fi
grep -q 'bibi-private-capabilities-update' "$TMP/linux-private.out" || fail "Ubuntu private-manifest refusal omitted daily-user route"
[[ ! -e "$TMP/linux-private-home" ]] || fail "Ubuntu private-manifest refusal mutated target home"

if PATH="$TMP/fakebin:$PATH" BIBI_SETUP_TESTING=1 BIBI_TEST_OS=Darwin BIBI_TEST_ARCH=arm64 \
  BIBI_TARGET_HOME="$TMP/private-home" BIBI_XCODE_SELECT="$TMP/fakebin/xcode-select" \
  BIBI_BREW="$TMP/fakebin/brew" BIBI_GH="$TMP/fakebin/gh" BREW_CALLED="$TMP/brew-called" \
  "$ROOT/bin/bibi-setup" --apply --profile private-capabilities \
  --private-manifest "$TMP/private.manifest" >"$TMP/private.out" 2>&1; then
  fail "unauthenticated private apply succeeded"
fi
grep -q "gh auth login" "$TMP/private.out" || fail "private auth refusal omitted remediation"
[[ ! -e "$TMP/private-home" && ! -e "$TMP/brew-called" ]] || fail "private preflight mutated before auth"

cat > "$TMP/private-empty.manifest" <<'EOF'
schema=bibi-private-capabilities.v1
EOF
if BIBI_SETUP_TESTING=1 BIBI_TEST_OS=Darwin BIBI_TEST_ARCH=arm64 \
  "$ROOT/bin/bibi-setup" --plan --profile private-capabilities \
  --private-manifest "$TMP/private-empty.manifest" >"$TMP/private-empty.out" 2>&1; then
  fail "source-free private manifest was accepted"
fi
grep -q 'contains no sources' "$TMP/private-empty.out" || fail "empty private manifest refusal was unclear"

# Limited YAML parsing never evaluates shell text and reads exact pins.
malicious="$TMP/malicious.yml"
cat > "$malicious" <<'EOF'
safe_key: "$(touch /tmp/bibi-should-not-exist)"
EOF
rm -f /tmp/bibi-should-not-exist
value=$(BIBI_ROOT="$ROOT" bash -c '. "$1/scripts/setup-common.sh"; bibi_yaml_scalar safe_key "$2"' _ "$ROOT" "$malicious")
[[ "$value" == "\$(touch /tmp/bibi-should-not-exist)" ]] || fail "manifest scalar changed"
[[ ! -e /tmp/bibi-should-not-exist ]] || fail "manifest scalar executed"

# A checksum failure leaves an already-active binary byte-identical.
printf 'old-active\n' > "$TMP/active"
cat > "$TMP/fakebin/curl-bad" <<'SH'
#!/usr/bin/env bash
for ((i=1; i<=$#; i++)); do
  if [[ ${!i} == -o ]]; then j=$((i+1)); printf 'bad-download\n' > "${!j}"; exit 0; fi
done
exit 2
SH
chmod +x "$TMP/fakebin/curl-bad"
if BIBI_ROOT="$ROOT" BIBI_CURL="$TMP/fakebin/curl-bad" bash -c \
  '. "$1/scripts/setup-common.sh"; bibi_download_verified https://example.invalid/file 0000000000000000000000000000000000000000000000000000000000000000 100 "$2"' \
  _ "$ROOT" "$TMP/stage" >"$TMP/checksum.out" 2>&1; then
  fail "wrong checksum succeeded"
fi
[[ $(cat "$TMP/active") == old-active ]] || fail "checksum failure changed active binary"
[[ ! -e "$TMP/stage" ]] || fail "checksum failure left the rejected artifact behind"
grep -q 'active install unchanged' "$TMP/checksum.out" || fail "checksum failure did not state atomic boundary"
cat > "$TMP/fakebin/curl-interrupted" <<'SH'
#!/usr/bin/env bash
for ((i=1; i<=$#; i++)); do
  if [[ ${!i} == -o ]]; then j=$((i+1)); printf 'partial-download\n' > "${!j}"; exit 56; fi
done
exit 2
SH
chmod +x "$TMP/fakebin/curl-interrupted"
if BIBI_ROOT="$ROOT" BIBI_CURL="$TMP/fakebin/curl-interrupted" bash -c \
  '. "$1/scripts/setup-common.sh"; bibi_download_verified https://example.invalid/file "$2" 100 "$3"' \
  _ "$ROOT" 0000000000000000000000000000000000000000000000000000000000000000 "$TMP/interrupted" \
  >"$TMP/interrupted.out" 2>&1; then
  fail "interrupted download succeeded"
fi
[[ ! -e "$TMP/interrupted" && $(cat "$TMP/active") == old-active ]] \
  || fail "interrupted download left partial data or changed the active binary"

# The portable receipt is owner-only, non-secret, and records exact isolated
# paths, public versions, checksums, and source commits.
mkdir -p "$TMP/receipt-bin"
cat > "$TMP/receipt-bin/git" <<'SH'
#!/usr/bin/env bash
if [[ ${1:-} == --version ]]; then echo 'git version fixture'; else exec /usr/bin/git "$@"; fi
SH
for command_name in gh jq tmux; do
  cat > "$TMP/receipt-bin/$command_name" <<SH
#!/usr/bin/env bash
echo '$command_name fixture version'
SH
done
chmod +x "$TMP/receipt-bin/"*
PATH="$TMP/receipt-bin:$PATH" BIBI_ROOT="$ROOT" BIBI_SHARE="$TMP/receipt-share" \
  BIBI_FM_HOME="$TMP/receipt-fm" BIBI_PI_HOME="$TMP/receipt-fm/pi" \
  BIBI_BACKEND=herdr BIBI_PROFILES=base BIBI_OS=Darwin BIBI_ARCH=arm64 \
  BIBI_FIRSTMATE_SOURCE="$TMP/receipt-source" \
  BIBI_FIRSTMATE_ACTIVE_REF=40c50ea8843c5b6a5351db8352675537252b653e \
  BIBI_FRESH_HOME_CREATED=true bash -c \
  '. "$1/scripts/setup-common.sh"; bibi_write_receipt "$2"' _ "$ROOT" "$TMP/receipt"
[[ -f "$TMP/receipt" && ! -L "$TMP/receipt" ]] || fail "portable receipt is not a direct regular file"
if stat -c '%a' "$TMP/receipt" >/dev/null 2>&1; then
  receipt_mode=$(stat -c '%a' "$TMP/receipt")
else
  receipt_mode=$(stat -f '%Lp' "$TMP/receipt")
fi
[[ "$receipt_mode" == 600 ]] || fail "portable receipt mode is not 0600"
grep -q '^treehouse_dir=.*/receipt-fm/treehouse$' "$TMP/receipt" || fail "receipt omitted isolated Treehouse pool"
grep -q '^node_archive_sha256=' "$TMP/receipt" || fail "receipt omitted reviewed hashes"
grep -q '^credentials_recorded=false$' "$TMP/receipt" || fail "receipt omitted credential boundary"
if grep -Eiq 'token=|password=|auth\.json|trust\.json' "$TMP/receipt"; then fail "receipt contains credential material"; fi

# Fresh-home creation is minimal; rerun preserves arbitrary state and a
# differing backend is refused rather than reset.
BIBI_ROOT="$ROOT" BIBI_FM_HOME="$TMP/instance" BIBI_PI_HOME="$TMP/instance/pi" \
  bash -c '. "$1/scripts/setup-common.sh"; bibi_create_fresh_instance herdr' _ "$ROOT"
[[ -f "$TMP/instance/config/backend" && -f "$TMP/instance/config/crew-harness" ]] || fail "fresh config missing"
[[ ! -e "$TMP/instance/data" && ! -e "$TMP/instance/state" && ! -e "$TMP/instance/projects" ]] \
  || fail "fresh home imported operational records"
for path in auth.json models-store.json trust.json sessions; do
  [[ ! -e "$TMP/instance/pi/$path" && ! -L "$TMP/instance/pi/$path" ]] \
    || fail "fresh installer home created pre-launch Pi state: $path"
done
printf 'keep-me\n' > "$TMP/instance/local-setting"
BIBI_ROOT="$ROOT" BIBI_FM_HOME="$TMP/instance" BIBI_PI_HOME="$TMP/instance/pi" \
  bash -c '. "$1/scripts/setup-common.sh"; bibi_create_fresh_instance herdr' _ "$ROOT"
[[ $(cat "$TMP/instance/local-setting") == keep-me ]] || fail "rerun reset local state"
if BIBI_ROOT="$ROOT" BIBI_FM_HOME="$TMP/instance" BIBI_PI_HOME="$TMP/instance/pi" \
  bash -c '. "$1/scripts/setup-common.sh"; bibi_create_fresh_instance tmux' _ "$ROOT" >"$TMP/backend.out" 2>&1; then
  fail "rerun silently changed backend"
fi
[[ $(cat "$TMP/instance/config/backend") == herdr ]] || fail "differing backend was overwritten"

mkdir -p "$TMP/symlink-instance" "$TMP/redirect-target"
ln -s "$TMP/redirect-target" "$TMP/symlink-instance/pi"
if BIBI_ROOT="$ROOT" BIBI_FM_HOME="$TMP/symlink-instance" BIBI_PI_HOME="$TMP/symlink-instance/pi" \
  bash -c '. "$1/scripts/setup-common.sh"; bibi_create_fresh_instance herdr' _ "$ROOT" >"$TMP/symlink-instance.out" 2>&1; then
  fail "redirected Pi home was accepted"
fi
[[ ! -e "$TMP/redirect-target/extensions" ]] || fail "redirected Pi home target was mutated"

printf '%s\n' 'unmanaged-command' > "$TMP/unmanaged-command"
if BIBI_ROOT="$ROOT" bash -c \
  '. "$1/scripts/setup-common.sh"; bibi_atomic_symlink "$2" "$3"' _ "$ROOT" "$TMP/active" "$TMP/unmanaged-command" \
  >"$TMP/unmanaged-command.out" 2>&1; then
  fail "atomic activation replaced an unmanaged regular command"
fi
[[ $(cat "$TMP/unmanaged-command") == unmanaged-command ]] || fail "unmanaged regular command changed"

mkdir -p "$TMP/launcher-bin" "$TMP/launcher-fm" "$TMP/launcher-pi"
printf '%s\n' 'unmanaged-launcher' > "$TMP/launcher-bin/bibi"
if BIBI_ROOT="$ROOT" BIBI_BIN="$TMP/launcher-bin" BIBI_FM_HOME="$TMP/launcher-fm" \
  BIBI_PI_HOME="$TMP/launcher-pi" BIBI_FIRSTMATE_SOURCE="$ROOT" \
  bash -c '. "$1/scripts/setup-common.sh"; bibi_write_launcher' _ "$ROOT" >"$TMP/launcher.out" 2>&1; then
  fail "launcher writer replaced an unmanaged bibi command"
fi
[[ $(cat "$TMP/launcher-bin/bibi") == unmanaged-launcher ]] || fail "unmanaged bibi command changed"

mkdir -p "$TMP/redirected-root"
ln -s "$TMP/redirected-root" "$TMP/redirected-chain"
if BIBI_ROOT="$ROOT" bash -c \
  '. "$1/scripts/setup-common.sh"; bibi_require_directory_chain "$2/child"' _ "$ROOT" "$TMP/redirected-chain" \
  >"$TMP/redirected-chain.out" 2>&1; then
  fail "redirected installation directory chain was accepted"
fi

# Pin and policy regression assertions.
grep -q '^firstmate_ref: "40c50ea8843c5b6a5351db8352675537252b653e"$' "$ROOT/group_vars/all.yml" || fail "Firstmate pin drift"
grep -q '^pi_version: "0.85.1"$' "$ROOT/group_vars/all.yml" || fail "Pi pin drift"
grep -q '^no_mistakes_version: "1.60.2"$' "$ROOT/group_vars/all.yml" || fail "no-mistakes pin drift"
grep -q 'LimitLoadToSessionType' "$ROOT/templates/dev.bibi.herdr.default.plist" || fail "LaunchAgent lacks Aqua scope"
grep -q '<string>Aqua</string>' "$ROOT/templates/dev.bibi.herdr.default.plist" || fail "LaunchAgent is not Aqua"
if grep -Eiq 'TOKEN|PASSWORD|API_KEY|auth\.json' "$ROOT/templates/dev.bibi.herdr.default.plist"; then fail "LaunchAgent contains secret surface"; fi
grep -q 'PI_CODING_AGENT_DIR="{{ bibi_pi_home }}"' "$ROOT/templates/bibi-pi-extensions-update.j2" || fail "legacy private installer targets global Pi home"
grep -q 'TREEHOUSE_DIR="{{ firstmate_instance_home }}/treehouse"' "$ROOT/templates/bibi-launcher.j2" || fail "launcher does not isolate Treehouse pool"
grep -q "grep -q '\^pi: current '" "$ROOT/scripts/setup-common.sh" || fail "Herdr integration doctor gate missing"
grep -q 'bibi-browser-smoke' "$ROOT/scripts/setup-macos.sh" || fail "macOS browser smoke missing"
grep -q 'bibi-browser-smoke' "$ROOT/site.yml" || fail "Ubuntu browser smoke missing"
grep -q -- "--extra-vars \"\$extra_vars\"" "$ROOT/templates/bibi-machine-update-command.j2" || fail "machine update drops selected profiles"

# The committed example is sanitized and carries no real private inventory.
if grep -Eq 'brancusi|github_pat|ghp_|token=' "$ROOT/examples/private-capabilities.manifest.example"; then
  fail "private example contains personal inventory or credential material"
fi

echo "PASS portable plan, platform, privacy, checksum, rerun, pin, and LaunchAgent checks (mock/static only)"
