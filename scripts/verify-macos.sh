#!/usr/bin/env bash
# managed-by: bibi-portable-setup
# Read-only verification for a portable Darwin-arm64 installation.
set -euo pipefail

BIBI_VERIFY_HOME=${BIBI_VERIFY_HOME:-$HOME}
BIBI_VERIFY_RECEIPT=${BIBI_VERIFY_RECEIPT:-"$BIBI_VERIFY_HOME/.local/state/bibi/provisioned-versions"}
BIBI_VERIFY_LIB=${BIBI_VERIFY_LIB:-"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"}
BIBI_VERIFY_BIN="$BIBI_VERIFY_HOME/.local/bin"
PATH="$BIBI_VERIFY_BIN:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export BIBI_VERIFY_BIN PATH
# shellcheck source=scripts/verify-common.sh
. "$BIBI_VERIFY_LIB/verify-common.sh"

[ "$(uname -s)" = Darwin ] || bibi_verify_fail "macOS verifier requires Darwin"
[ "$(uname -m)" = arm64 ] || bibi_verify_fail "macOS verifier requires native arm64"

receipt_spec_version() {
  local key=$1 spec
  spec=$(bibi_receipt_value "$key" 2>/dev/null || true)
  [ -n "$spec" ] || return 1
  printf '%s\n' "${spec##*@}"
}

verify_homebrew_command() {
  local command_name=$1 receipt_key=$2 actual expected output
  shift 2
  actual=$(command -v "$command_name" 2>/dev/null || true)
  expected=$(bibi_receipt_value "$receipt_key" 2>/dev/null || true)
  output=$("$command_name" "$@" 2>&1 | awk 'NR == 1 { print }' || true)
  if [ "$actual" = "/opt/homebrew/bin/$command_name" ] && [ -n "$expected" ] && [ "$output" = "$expected" ]; then
    bibi_verify_ok "$command_name at $actual ($output)"
  else
    bibi_verify_fail "$command_name path or version differs (found ${actual:-missing}, ${output:-no version})"
  fi
}

verify_user_command_path() {
  local command_name=$1 actual target
  actual=$(command -v "$command_name" 2>/dev/null || true)
  target=$(readlink "$BIBI_VERIFY_BIN/$command_name" 2>/dev/null || true)
  if [ "$actual" = "$BIBI_VERIFY_BIN/$command_name" ] \
    && [ -L "$BIBI_VERIFY_BIN/$command_name" ] \
    && [[ "$target" == "$BIBI_VERIFY_HOME/.local/share/bibi/"* ]]; then
    bibi_verify_ok "$command_name resolves to the isolated managed tree"
  else
    bibi_verify_fail "$command_name does not resolve to the isolated managed tree"
  fi
}

verify_macos_receipt_scope() {
  local manifest_ref profiles profile old_ifs seen='|'
  manifest_ref=$(bibi_receipt_value firstmate_manifest_ref 2>/dev/null || true)
  [ "$(bibi_receipt_value platform 2>/dev/null || true)" = Darwin-arm64 ] \
    || bibi_verify_fail "receipt platform is not Darwin-arm64"
  [ "$(bibi_receipt_value fm_home 2>/dev/null || true)" = "$BIBI_VERIFY_HOME/.local/share/firstmate/instances/main" ] \
    || bibi_verify_fail "receipt FM_HOME is outside the isolated macOS instance"
  [ "$(bibi_receipt_value pi_home 2>/dev/null || true)" = "$BIBI_VERIFY_HOME/.local/share/firstmate/instances/main/pi" ] \
    || bibi_verify_fail "receipt Pi home is outside the isolated macOS instance"
  [ "$(bibi_receipt_value treehouse_dir 2>/dev/null || true)" = "$BIBI_VERIFY_HOME/.local/share/firstmate/instances/main/treehouse" ] \
    || bibi_verify_fail "receipt Treehouse pool is outside the isolated macOS instance"
  [ "$(bibi_receipt_value firstmate_source 2>/dev/null || true)" = "$BIBI_VERIFY_HOME/.local/share/firstmate/source/$manifest_ref" ] \
    || bibi_verify_fail "receipt Firstmate source is outside the reviewed versioned path"
  profiles=$(bibi_receipt_value profiles 2>/dev/null || true)
  old_ifs=$IFS
  IFS=,
  for profile in $profiles; do
    case "$profile" in
      base|public-pi-extras|private-capabilities|cloudflare|digitalocean|web-research|clojure|browser) ;;
      *) bibi_verify_fail "unsupported macOS profile ${profile:-empty}"; continue ;;
    esac
    case "$seen" in *"|$profile|"*) bibi_verify_fail "duplicate macOS profile $profile" ;; esac
    seen="$seen$profile|"
  done
  IFS=$old_ifs
}

verify_managed_script() {
  local path=$1 mode
  mode=$(stat -f '%Lp' "$path" 2>/dev/null || true)
  if [ -f "$path" ] && [ ! -L "$path" ] && [ -O "$path" ] \
    && [ "$mode" = 755 ] && grep -Fqx '# managed-by: bibi-portable-setup' "$path"; then
    bibi_verify_ok "managed script $path"
  else
    bibi_verify_fail "managed script differs at $path"
  fi
}

verify_pi_package_source() {
  local source=$1 pi_home settings_file spec package_name expected_version package_json actual_version
  pi_home=$(bibi_receipt_value pi_home 2>/dev/null || true)
  settings_file="$pi_home/settings.json"
  if [ ! -f "$settings_file" ] || [ -L "$settings_file" ] \
    || ! jq -e --arg source "$source" \
      'any((.packages // [])[]; (if type == "string" then . else .source end) == $source)' \
      "$settings_file" >/dev/null 2>&1; then
    bibi_verify_fail "missing pinned Pi package $source"
    return
  fi
  spec=${source#npm:}
  package_name=${spec%@*}
  expected_version=${spec##*@}
  package_json="$pi_home/npm/node_modules/$package_name/package.json"
  actual_version=$(jq -r '.version // empty' "$package_json" 2>/dev/null || true)
  if [ "$actual_version" = "$expected_version" ]; then
    bibi_verify_ok "Pi package $package_name $expected_version"
  else
    bibi_verify_fail "Pi package $package_name expected $expected_version, found ${actual_version:-missing}"
  fi
}

verify_public_pi_profile() {
  local sources source
  local -a source_list
  sources=$(bibi_receipt_value pi_public_packages 2>/dev/null || true)
  read -r -a source_list <<< "$sources"
  [ "${#source_list[@]}" -gt 0 ] || { bibi_verify_fail "receipt has no public Pi package pins"; return; }
  for source in "${source_list[@]}"; do verify_pi_package_source "$source"; done
}

verify_web_research_pi_package() {
  local sources source
  local -a source_list
  sources=$(bibi_receipt_value pi_public_packages 2>/dev/null || true)
  read -r -a source_list <<< "$sources"
  for source in "${source_list[@]}"; do
    case "$source" in npm:pi-web-access@*) verify_pi_package_source "$source"; return ;; esac
  done
  bibi_verify_fail "receipt has no pi-web-access pin"
}

verify_browser_profile() {
  local browser profile output errors pid count=0 status=0
  browser='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  if [ ! -x "$browser" ]; then bibi_verify_fail "browser profile requires Google Chrome"; return; fi
  profile=$(mktemp -d "${TMPDIR:-/tmp}/bibi-browser-verify.XXXXXX") \
    || { bibi_verify_fail "could not create disposable browser profile"; return; }
  output="$profile/output"
  errors="$profile/errors"
  "$browser" --headless --disable-gpu --disable-background-networking --disable-component-update \
    --disable-sync --metrics-recording-only --no-first-run --no-default-browser-check \
    --safebrowsing-disable-auto-update --use-mock-keychain --user-data-dir="$profile/user-data" \
    --dump-dom 'data:text/html,<title>bibi-browser-smoke</title><p>bibi-browser-smoke</p>' \
    >"$output" 2>"$errors" &
  pid=$!
  while kill -0 "$pid" 2>/dev/null && [ "$count" -lt 30 ]; do
    sleep 1
    count=$((count + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    rm -rf "$profile"
    bibi_verify_fail "browser smoke exceeded 30 seconds"
    return
  fi
  wait "$pid" || status=$?
  if [ "$status" -eq 0 ] && grep -q 'bibi-browser-smoke' "$output"; then
    bibi_verify_ok "browser disposable local-page smoke"
  else
    bibi_verify_fail "browser disposable local-page smoke failed"
  fi
  rm -rf "$profile"
}

verify_cloudflare_profile() {
  local checkout expected_repo expected_ref actual_repo actual_ref status pi_home skills skill target
  local -a skill_list
  checkout=$(bibi_receipt_value cloudflare_skills_checkout 2>/dev/null || true)
  expected_repo=$(bibi_receipt_value cloudflare_skills_repo 2>/dev/null || true)
  expected_ref=$(bibi_receipt_value cloudflare_skills_ref 2>/dev/null || true)
  pi_home=$(bibi_receipt_value pi_home 2>/dev/null || true)
  actual_repo=$(git -C "$checkout" remote get-url origin 2>/dev/null || true)
  actual_ref=$(git -C "$checkout" rev-parse HEAD 2>/dev/null || true)
  status=$(git -C "$checkout" status --porcelain --untracked-files=all 2>/dev/null || true)
  if [ "$actual_repo" != "$expected_repo" ] || [ "$actual_ref" != "$expected_ref" ] || [ -n "$status" ]; then
    bibi_verify_fail "Cloudflare skill checkout source, pin, or cleanliness differs"
    return
  fi
  skills=$(bibi_receipt_value cloudflare_skill_names 2>/dev/null || true)
  read -r -a skill_list <<< "$skills"
  [ "${#skill_list[@]}" -gt 0 ] || { bibi_verify_fail "receipt has no Cloudflare skill pins"; return; }
  for skill in "${skill_list[@]}"; do
    target="$checkout/skills/$skill"
    if [ -L "$pi_home/skills/$skill" ] \
      && [ "$(readlink "$pi_home/skills/$skill")" = "$target" ] \
      && [ -r "$target/SKILL.md" ]; then
      bibi_verify_ok "Cloudflare skill $skill at $expected_ref"
    else
      bibi_verify_fail "Cloudflare skill $skill is not linked to the reviewed checkout"
    fi
  done
}

bibi_verify_common
verify_macos_receipt_scope
if [ -f "$BIBI_VERIFY_RECEIPT" ] && [ ! -L "$BIBI_VERIFY_RECEIPT" ]; then
  receipt_mode=$(stat -f '%Lp' "$BIBI_VERIFY_RECEIPT" 2>/dev/null || true)
  [ -O "$BIBI_VERIFY_RECEIPT" ] || bibi_verify_fail "receipt is not owned by the current user"
  [ "$receipt_mode" = 600 ] || bibi_verify_fail "receipt mode is ${receipt_mode:-unknown}, expected 600"
fi

verify_homebrew_command git git_version --version
verify_homebrew_command gh gh_version --version
verify_homebrew_command jq jq_version --version
verify_homebrew_command tmux tmux_version -V
verify_managed_script "$BIBI_VERIFY_BIN/bibi"
verify_managed_script "$BIBI_VERIFY_BIN/bibi-verify"
for command_name in npm node pi no-mistakes treehouse gh-axi chrome-devtools-axi lavish-axi tasks-axi quota-axi; do
  verify_user_command_path "$command_name"
done
node_version=$(bibi_receipt_value node_version 2>/dev/null || true)
pi_version=$(bibi_receipt_value pi_version 2>/dev/null || true)
no_mistakes_version=$(bibi_receipt_value no_mistakes_version 2>/dev/null || true)
treehouse_version=$(bibi_receipt_value treehouse_version 2>/dev/null || true)
bibi_verify_command_contains node "v$node_version"
bibi_verify_command_contains pi "$pi_version"
bibi_verify_command_contains no-mistakes "$no_mistakes_version"
bibi_verify_command_contains treehouse "$treehouse_version"
bibi_verify_command_contains gh-axi "$(bibi_receipt_npm_version gh-axi 2>/dev/null || true)"
bibi_verify_command_contains chrome-devtools-axi "$(bibi_receipt_npm_version chrome-devtools-axi 2>/dev/null || true)"
bibi_verify_command_contains lavish-axi "$(bibi_receipt_npm_version lavish-axi 2>/dev/null || true)"
bibi_verify_command_contains tasks-axi "$(bibi_receipt_npm_version tasks-axi 2>/dev/null || true)"
bibi_verify_command_contains quota-axi "$(bibi_receipt_npm_version quota-axi 2>/dev/null || true)"
if [ "$(bibi_receipt_value backend 2>/dev/null || true)" = herdr ]; then
  verify_user_command_path herdr
  bibi_verify_command_contains herdr "$(bibi_receipt_value herdr_version 2>/dev/null || true)"
fi
if bibi_profile_selected cloudflare; then
  verify_user_command_path wrangler
  bibi_verify_command_contains wrangler "$(receipt_spec_version wrangler_package 2>/dev/null || true)"
  verify_cloudflare_profile
fi
if bibi_profile_selected digitalocean; then
  verify_user_command_path doctl
  bibi_verify_command_contains doctl "$(bibi_receipt_value doctl_version 2>/dev/null || true)" version
fi
if bibi_profile_selected web-research; then
  verify_user_command_path firecrawl
  bibi_verify_command_contains firecrawl "$(receipt_spec_version firecrawl_cli_package 2>/dev/null || true)"
  verify_web_research_pi_package
fi
if bibi_profile_selected public-pi-extras; then verify_public_pi_profile; fi
if bibi_profile_selected private-capabilities; then
  printf '%s\n' 'manual   private source inventory is intentionally excluded from the non-secret receipt; review pi list against the local manifest'
fi
if bibi_profile_selected clojure; then
  for command_name in clojure clj java javac jar; do verify_user_command_path "$command_name"; done
  bibi_verify_command_contains clojure "$(bibi_receipt_value clojure_cli_version 2>/dev/null || true)"
  bibi_verify_command_contains java "$(bibi_receipt_value jdk_runtime_version 2>/dev/null || true)" -fullversion
fi
if bibi_profile_selected browser; then verify_browser_profile; fi

plist="$BIBI_VERIFY_HOME/Library/LaunchAgents/dev.bibi.herdr.default.plist"
if [ -e "$plist" ] || [ -L "$plist" ]; then
  plist_mode=$(stat -f '%Lp' "$plist" 2>/dev/null || true)
  if [ -f "$plist" ] && [ ! -L "$plist" ] && [ -O "$plist" ] && [ "$plist_mode" = 600 ] \
    && grep -Fqx '<!-- managed-by: bibi-portable-setup -->' "$plist" \
    && plutil -lint "$plist" >/dev/null 2>&1; then
    bibi_verify_ok "LaunchAgent plist syntax, ownership, and mode"
  else
    bibi_verify_fail "invalid LaunchAgent plist $plist"
  fi
  if grep -Eq 'TOKEN|PASSWORD|SECRET|API_KEY|auth\.json' "$plist"; then
    bibi_verify_fail "LaunchAgent contains credential-like material"
  fi
  grep -A1 '<key>LimitLoadToSessionType</key>' "$plist" | grep -q '<string>Aqua</string>' \
    || bibi_verify_fail "LaunchAgent is not Aqua-scoped"
  grep -Fq "<string>$BIBI_VERIFY_BIN/herdr</string>" "$plist" \
    || bibi_verify_fail "LaunchAgent Herdr path differs"
  if launchctl print "gui/$(id -u)/dev.bibi.herdr.default" >/dev/null 2>&1; then
    bibi_verify_ok "LaunchAgent loaded in current Aqua user domain"
  else
    printf '%s\n' 'pending  LaunchAgent is installed but not loaded in the current Aqua user domain'
  fi
fi

exit "$BIBI_VERIFY_FAILED"
