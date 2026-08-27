#!/usr/bin/env bash
set -euo pipefail

versions_file=${BIBI_VERSIONS_FILE:-/etc/bibi-provisioned-versions}
failed=0

read_pin() {
  local key=$1
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; found=1; exit } END { if (!found) exit 1 }' "$versions_file"
}

verify_global_npm_spec() {
  local spec=$1 package_name expected_version npm_root package_json actual_version
  package_name=${spec%@*}
  expected_version=${spec##*@}
  npm_root=${BIBI_NPM_GLOBAL_ROOT:-$(npm root --global)}
  package_json="$npm_root/$package_name/package.json"

  if [[ ! -r "$package_json" ]]; then
    printf 'missing  global npm package %s\n' "$spec" >&2
    failed=1
  elif ! actual_version=$(jq -er '.version | strings' "$package_json" 2>/dev/null); then
    printf 'invalid  global npm package metadata for %s\n' "$package_name" >&2
    failed=1
  elif [[ "$actual_version" != "$expected_version" ]]; then
    printf 'invalid  %s version: expected %s, found %s\n' \
      "$package_name" "$expected_version" "$actual_version" >&2
    failed=1
  else
    printf 'ok       %s %s\n' "$package_name" "$actual_version"
  fi
}

verify_public_pi_packages() {
  local settings_file source spec package_name expected_version package_json actual_version
  local -a sources
  settings_file=${BIBI_PI_SETTINGS_FILE:-"$HOME/.pi/agent/settings.json"}
  read -r -a sources <<< "$(read_pin pi_public_packages)"

  if [[ ! -r "$settings_file" ]] \
    || ! jq -e '(.packages // []) | type == "array"' "$settings_file" >/dev/null 2>&1; then
    printf 'invalid  Pi settings package list in %s\n' "$settings_file" >&2
    failed=1
    return
  fi

  for source in "${sources[@]}"; do
    if ! jq -e --arg source "$source" \
      'any((.packages // [])[]; (if type == "string" then . else .source end) == $source)' \
      "$settings_file" >/dev/null; then
      printf 'missing  pinned public Pi package %s\n' "$source" >&2
      failed=1
      continue
    fi

    spec=${source#npm:}
    package_name=${spec%@*}
    expected_version=${spec##*@}
    package_json="$HOME/.pi/agent/npm/node_modules/$package_name/package.json"
    actual_version=$(jq -r '.version // empty' "$package_json" 2>/dev/null || true)
    if [[ "$actual_version" != "$expected_version" ]]; then
      printf 'invalid  Pi package %s: expected %s, found %s\n' \
        "$package_name" "$expected_version" "${actual_version:-missing}" >&2
      failed=1
    else
      printf 'ok       Pi package %s %s\n' "$package_name" "$actual_version"
    fi
  done
}

verify_cloudflare_skills() {
  local checkout expected_repo expected_ref actual_repo link_dir skill actual_target expected_target status
  local -a skills
  checkout=$(read_pin cloudflare_skills_checkout)
  expected_repo=$(read_pin cloudflare_skills_repo)
  expected_ref=$(read_pin cloudflare_skills_ref)
  link_dir=$(read_pin cloudflare_skill_link_dir)
  read -r -a skills <<< "$(read_pin cloudflare_skill_names)"

  actual_repo=$(git -C "$checkout" remote get-url origin 2>/dev/null || true)
  if [[ "$actual_repo" != "$expected_repo" ]]; then
    echo "invalid  official Cloudflare skills checkout has the wrong source" >&2
    failed=1
    return
  elif [[ $(git -C "$checkout" rev-parse HEAD 2>/dev/null || true) != "$expected_ref" ]]; then
    echo "invalid  official Cloudflare skills checkout is not at its configured pin" >&2
    failed=1
    return
  fi

  status=$(git -C "$checkout" status --porcelain --untracked-files=all -- \
    skills/cloudflare skills/wrangler 2>/dev/null || true)
  if [[ -n "$status" ]]; then
    echo "invalid  managed Cloudflare skills contain local changes" >&2
    failed=1
  fi

  for skill in "${skills[@]}"; do
    expected_target="$checkout/skills/$skill"
    actual_target=$(readlink -f "$link_dir/$skill" 2>/dev/null || true)
    if [[ "$actual_target" != "$expected_target" || ! -r "$actual_target/SKILL.md" ]]; then
      printf 'invalid  Pi skill %s is not linked to the reviewed checkout\n' "$skill" >&2
      failed=1
    else
      printf 'ok       Pi skill %s at %s\n' "$skill" "$expected_ref"
    fi
  done
}

verify_shared_clojure_toolchain() {
  local expected_root expected_jdk_version expected_jdk_runtime_version expected_jdk_home expected_clojure_version expected_clojure_home
  local java_command clojure_command java_target clojure_target java_full_version clojure_version environment_value
  expected_root=$(read_pin shared_toolchain_root)
  expected_jdk_version=$(read_pin jdk_version)
  expected_jdk_runtime_version=$(read_pin jdk_runtime_version)
  expected_jdk_home=$(read_pin jdk_home)
  expected_clojure_version=$(read_pin clojure_cli_version)
  expected_clojure_home=$(read_pin clojure_home)

  if [[ "$expected_jdk_home" != "$expected_root/jdk-21" \
    || "$expected_clojure_home" != "$expected_root/clojure" \
    || ( "$expected_root" != /opt/* && ${BIBI_VERIFY_ALLOW_TEST_ROOT:-0} != 1 ) ]]; then
    echo "invalid  shared toolchain metadata is not rooted in host-managed /opt" >&2
    failed=1
    return
  fi

  if [[ ${JAVA_HOME:-} != "$expected_jdk_home" ]]; then
    printf 'invalid  JAVA_HOME: expected %s, found %s\n' \
      "$expected_jdk_home" "${JAVA_HOME:-unset}" >&2
    failed=1
  else
    printf 'ok       JAVA_HOME %s\n' "$JAVA_HOME"
  fi

  java_command=$(command -v java 2>/dev/null || true)
  clojure_command=$(command -v clojure 2>/dev/null || true)
  java_target=$(readlink -f "$java_command" 2>/dev/null || true)
  clojure_target=$(readlink -f "$clojure_command" 2>/dev/null || true)
  if [[ "$java_target" != "$(readlink -f "$expected_jdk_home/bin/java" 2>/dev/null || true)" ]]; then
    printf 'invalid  java does not resolve to %s/bin/java\n' "$expected_jdk_home" >&2
    failed=1
  elif ! java_full_version=$("$java_command" -fullversion 2>&1) \
    || [[ "$java_full_version" != "openjdk full version \"$expected_jdk_runtime_version\"" ]]; then
    printf 'invalid  JDK runtime version: expected %s, found %s\n' \
      "$expected_jdk_runtime_version" "${java_full_version:-unavailable}" >&2
    failed=1
  else
    printf 'ok       JDK %s (%s) at %s\n' \
      "$expected_jdk_version" "$expected_jdk_runtime_version" "$java_target"
  fi

  if [[ "$clojure_target" != "$(readlink -f "$expected_clojure_home/bin/clojure" 2>/dev/null || true)" ]]; then
    printf 'invalid  clojure does not resolve to %s/bin/clojure\n' "$expected_clojure_home" >&2
    failed=1
  elif ! clojure_version=$("$clojure_command" --version 2>/dev/null) \
    || [[ "$clojure_version" != "Clojure CLI version $expected_clojure_version" ]]; then
    printf 'invalid  Clojure CLI version: expected %s, found %s\n' \
      "$expected_clojure_version" "${clojure_version:-unavailable}" >&2
    failed=1
  else
    printf 'ok       Clojure CLI %s at %s\n' "$expected_clojure_version" "$clojure_target"
  fi

  if [[ ${BIBI_VERIFY_ALLOW_TEST_ROOT:-0} != 1 ]]; then
    for environment_value in "${JAVA_HOME:-}" "$PATH" "$java_target" "$clojure_target"; do
      if [[ "${environment_value,,}" == *'.treehouse'* \
        || "${environment_value,,}" == *'study-walk'* \
        || "${environment_value,,}" == *'study_walk'* \
        || "${environment_value,,}" == *'study walk'* ]]; then
        echo "invalid  shared toolchain environment contains a project-local path" >&2
        failed=1
        break
      fi
    done
  fi
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

if [[ ${BIBI_VERIFY_TOOLCHAIN_ONLY:-0} == 1 ]]; then
  verify_shared_clojure_toolchain
  exit "$failed"
elif [[ ${BIBI_VERIFY_DOCTL_ONLY:-0} == 1 ]]; then
  verify_doctl
  exit "$failed"
elif [[ ${BIBI_VERIFY_TOOLING_ONLY:-0} == 1 ]]; then
  verify_doctl
else
  verify_shared_clojure_toolchain
  verify_doctl
fi

verify_global_npm_spec "$(read_pin pi_package)"
verify_global_npm_spec "$(read_pin wrangler_package)"
verify_global_npm_spec "$(read_pin firecrawl_cli_package)"
read -r -a axi_package_specs <<< "$(read_pin axi_packages)"
for axi_package_spec in "${axi_package_specs[@]}"; do
  verify_global_npm_spec "$axi_package_spec"
done
verify_public_pi_packages
verify_cloudflare_skills

if [[ ${BIBI_VERIFY_TOOLING_ONLY:-0} == 1 ]]; then
  exit "$failed"
fi

for command_name in java javac jar clojure clj node npm git gh jq pi wrangler herdr treehouse \
  no-mistakes firecrawl gh-axi chrome-devtools-axi lavish-axi tasks-axi quota-axi; do
  if command -v "$command_name" >/dev/null 2>&1; then
    printf 'ok       %s\n' "$command_name"
  else
    printf 'missing  %s\n' "$command_name" >&2
    failed=1
  fi
done

printf '\nVersions\n'
java -fullversion || true
clojure --version || true
node --version || true
npm --version || true
pi --version || true
wrangler --version || true
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
