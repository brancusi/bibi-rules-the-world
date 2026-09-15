#!/usr/bin/env bash
set -euo pipefail

versions_file=${BIBI_VERSIONS_FILE:-"$HOME/.local/state/bibi/provisioned-versions"}
failed=0
if [[ -z ${BIBI_VERSIONS_FILE:-} ]]; then
  receipt_mode=$(stat -c '%a' "$versions_file" 2>/dev/null || true)
  if [[ ! -f "$versions_file" || -L "$versions_file" || ! -O "$versions_file" || "$receipt_mode" != 600 ]]; then
    echo "invalid  daily-user receipt must be a current-user mode-0600 regular file: $versions_file" >&2
    failed=1
  fi
fi

read_pin() {
  local key=$1
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; found=1; exit } END { if (!found) exit 1 }' "$versions_file"
}

profile_enabled() {
  local profiles
  profiles=$(read_pin profiles 2>/dev/null || printf 'ubuntu-compat\n')
  [[ ",$profiles," == *",$1,"* || ",$profiles," == *",ubuntu-compat,"* ]]
}

profile_selected_exact() {
  local profiles
  profiles=$(read_pin profiles 2>/dev/null || printf 'ubuntu-compat\n')
  [[ ",$profiles," == *",$1,"* ]]
}

pi_home_path() {
  read_pin pi_home 2>/dev/null || printf '%s/.pi/agent\n' "$HOME"
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
  settings_file=${BIBI_PI_SETTINGS_FILE:-"$(pi_home_path)/settings.json"}
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
    package_json="$(pi_home_path)/npm/node_modules/$package_name/package.json"
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

verify_browser_profile() {
  local browser profile output
  browser=$(command -v google-chrome-stable 2>/dev/null \
    || command -v google-chrome 2>/dev/null \
    || command -v chromium 2>/dev/null \
    || command -v chromium-browser 2>/dev/null \
    || true)
  if [[ -z "$browser" ]]; then
    echo "invalid  browser profile has no supported Chrome/Chromium command" >&2
    failed=1
    return
  fi
  profile=$(mktemp -d /tmp/bibi-browser-verify.XXXXXX) || {
    echo "invalid  could not create disposable browser profile" >&2
    failed=1
    return
  }
  output="$profile/output"
  if timeout 30s "$browser" --headless --disable-gpu --disable-background-networking \
    --disable-component-update --disable-sync --metrics-recording-only --no-first-run \
    --no-default-browser-check --safebrowsing-disable-auto-update \
    --user-data-dir="$profile/user-data" --dump-dom \
    'data:text/html,<title>bibi-browser-smoke</title><p>bibi-browser-smoke</p>' \
    >"$output" 2>"$profile/errors" \
    && grep -q bibi-browser-smoke "$output"; then
    echo "ok       browser disposable local-page smoke"
  else
    echo "invalid  browser disposable local-page smoke failed" >&2
    failed=1
  fi
  rm -rf "$profile"
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

verify_memory_safety_net() {
  local guard_path expected_version config_path state_file swap_path expected_swap_mb
  local actual_version guard_metadata timer_state level decision_summary swap_kb swap_mb
  guard_path=$(read_pin memory_guard_install_path)
  expected_version=$(read_pin memory_guard_version)
  config_path=$(read_pin memory_guard_config_path)
  state_file=$(read_pin memory_guard_state_file)
  swap_path=$(read_pin swap_file_path)
  expected_swap_mb=$(read_pin swap_file_size_mb)

  if [[ ! -f "$guard_path" || -L "$guard_path" || ! -x "$guard_path" ]]; then
    printf 'invalid  memory guard must be a regular executable at %s\n' "$guard_path" >&2
    failed=1
    return
  fi

  guard_metadata=$(stat -c '%U:%G %a' "$guard_path")
  if [[ "$guard_metadata" != "$(read_pin memory_guard_owner):$(read_pin memory_guard_group) 755" ]]; then
    printf 'invalid  memory guard ownership/mode: found %s\n' "$guard_metadata" >&2
    failed=1
  fi

  if ! actual_version=$("$guard_path" --version 2>/dev/null) \
    || [[ "$actual_version" != "$expected_version" ]]; then
    printf 'invalid  memory guard version: expected %s, found %s\n' \
      "$expected_version" "${actual_version:-unavailable}" >&2
    failed=1
  else
    printf 'ok       memory guard %s\n' "$actual_version"
  fi

  if [[ ! -r "$config_path" ]]; then
    printf 'missing  memory guard policy %s\n' "$config_path" >&2
    failed=1
  elif ! grep -q "^leak_min_age_seconds=$(read_pin memory_guard_leak_min_age_seconds)$" "$config_path"; then
    echo "invalid  memory guard policy does not carry its reviewed grace period" >&2
    failed=1
  else
    printf 'ok       memory guard policy %s\n' "$config_path"
  fi

  # The guard's own read-only mode is the authoritative health check: it proves
  # the policy parses and every ownership oracle is reachable on this host.
  if ! "$guard_path" report --config "$config_path" >/dev/null 2>&1; then
    echo "invalid  memory guard cannot complete a read-only observation" >&2
    failed=1
  else
    echo "ok       memory guard observation"
  fi

  if [[ $(read_pin memory_guard_enabled) == true ]]; then
    timer_state=$(${BIBI_SYSTEMCTL:-systemctl} is-enabled bibi-memory-guard.timer 2>/dev/null || true)
    if [[ "$timer_state" != enabled ]]; then
      printf 'invalid  memory guard timer is %s, expected enabled\n' "${timer_state:-unknown}" >&2
      failed=1
    else
      printf 'ok       memory guard timer %s every %s\n' "$timer_state" "$(read_pin memory_guard_interval)"
    fi
  fi

  if [[ -r "$state_file" ]]; then
    level=$(jq -r '.level // "unknown"' "$state_file" 2>/dev/null || echo unknown)
    decision_summary=$(jq -r '[.trees[]? | .decision] | group_by(.) | map("\(.[0])=\(length)") | join(" ")' \
      "$state_file" 2>/dev/null || true)
    printf 'ok       memory guard last run: level=%s %s\n' "$level" "${decision_summary:-no-trees}"
  else
    echo "pending  memory guard has not recorded a run yet"
  fi

  swap_kb=$(awk -v path="$swap_path" '$1 == path { print $3 }' "${BIBI_PROC_SWAPS:-/proc/swaps}" 2>/dev/null || true)
  if [[ -z "$swap_kb" ]]; then
    printf 'invalid  swap safety net %s is not active\n' "$swap_path" >&2
    failed=1
  else
    swap_mb=$((swap_kb / 1024))
    # A swap area more than a few MB off the reviewed size means someone grew
    # the margin into a substitute for cleaning leaks.
    if (( swap_mb < expected_swap_mb - 8 || swap_mb > expected_swap_mb + 8 )); then
      printf 'invalid  swap safety net size: expected %s MB, found %s MB\n' \
        "$expected_swap_mb" "$swap_mb" >&2
      failed=1
    else
      printf 'ok       swap safety net %s %s MB (swappiness %s)\n' \
        "$swap_path" "$swap_mb" "$(read_pin swap_swappiness)"
    fi
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
elif [[ ${BIBI_VERIFY_MEMORY_ONLY:-0} == 1 ]]; then
  verify_memory_safety_net
  exit "$failed"
elif [[ ${BIBI_VERIFY_TOOLING_ONLY:-0} == 1 ]]; then
  profile_enabled digitalocean && verify_doctl
else
  profile_enabled clojure && verify_shared_clojure_toolchain
  profile_enabled digitalocean && verify_doctl
  verify_memory_safety_net
fi

verify_global_npm_spec "$(read_pin pi_package)"
profile_enabled cloudflare && verify_global_npm_spec "$(read_pin wrangler_package)"
profile_enabled web-research && verify_global_npm_spec "$(read_pin firecrawl_cli_package)"
read -r -a axi_package_specs <<< "$(read_pin axi_packages)"
for axi_package_spec in "${axi_package_specs[@]}"; do
  verify_global_npm_spec "$axi_package_spec"
done
if profile_enabled public-pi-extras || profile_enabled web-research; then verify_public_pi_packages; fi
profile_enabled cloudflare && verify_cloudflare_skills

if [[ ${BIBI_VERIFY_TOOLING_ONLY:-0} == 1 ]]; then
  exit "$failed"
fi

required_commands=(node npm git gh jq pi treehouse no-mistakes gh-axi chrome-devtools-axi lavish-axi tasks-axi quota-axi tmux)
[[ $(read_pin backend) == herdr ]] && required_commands+=(herdr)
profile_enabled cloudflare && required_commands+=(wrangler)
profile_enabled web-research && required_commands+=(firecrawl)
profile_enabled digitalocean && required_commands+=(doctl)
profile_enabled clojure && required_commands+=(java javac jar clojure clj)
for command_name in "${required_commands[@]}"; do
  if command -v "$command_name" >/dev/null 2>&1; then
    printf 'ok       %s\n' "$command_name"
  else
    printf 'missing  %s\n' "$command_name" >&2
    failed=1
  fi
done

printf '\nVersions\n'
if command -v java >/dev/null 2>&1; then java -fullversion || true; fi
if command -v clojure >/dev/null 2>&1; then clojure --version || true; fi
node --version || true
npm --version || true
pi --version || true
if command -v wrangler >/dev/null 2>&1; then wrangler --version || true; fi
if command -v herdr >/dev/null 2>&1; then herdr --version || true; fi
treehouse --version || true
no-mistakes --version || true
if command -v firecrawl >/dev/null 2>&1; then
  firecrawl --version 2>/dev/null || firecrawl --help 2>/dev/null | head -n 1 || true
else
  echo "firecrawl not installed"
fi

if profile_selected_exact ubuntu-compat; then
  printf '\nLegacy private Pi collection\n'
  pi_extensions_repo=$(read_pin pi_extensions_repo)
  pi_extensions_ref=$(read_pin pi_extensions_ref)
  pi_extensions_version=$(read_pin pi_extensions_package_version)
  pi_extensions_source=$(read_pin pi_extensions_source)
  printf 'expected source: %s\n' "$pi_extensions_source"
  printf 'expected DigitalOcean package: %s\n' "$pi_extensions_version"

  settings_file="$(pi_home_path)/settings.json"
  if [[ ! -e "$settings_file" ]]; then
    echo "pending  authenticate GitHub and run a private capability update command"
  elif ! jq -e '(.packages // []) | type == "array"' "$settings_file" >/dev/null 2>&1; then
    printf 'invalid  Pi settings package list in %s\n' "$settings_file" >&2
    failed=1
  elif jq -e --arg source "$pi_extensions_source" \
    'any((.packages // [])[]; (if type == "string" then . else .source end) == $source)' \
    "$settings_file" >/dev/null; then
    checkout_rel=${pi_extensions_repo#https://}
    checkout_rel=${checkout_rel%.git}
    checkout="$(pi_home_path)/git/$checkout_rel"
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
    failed=1
  else
    echo "pending  authenticate GitHub and run a private capability update command"
  fi
fi

if profile_selected_exact browser; then
  printf '\nBrowser\n'
  verify_browser_profile
fi

printf '\nFirstmate\n'
firstmate_source=$(read_pin firstmate_source)
firstmate_repo=$(read_pin firstmate_repo)
firstmate_ref=$(read_pin firstmate_ref)
firstmate_head=$(git -C "$firstmate_source" rev-parse HEAD 2>/dev/null || true)
firstmate_origin=$(git -C "$firstmate_source" remote get-url origin 2>/dev/null || true)
firstmate_status=$(git -C "$firstmate_source" status --porcelain --untracked-files=all 2>/dev/null || true)
if [[ -L "$firstmate_source" || ! -d "$firstmate_source/.git" || -L "$firstmate_source/.git" ]]; then
  echo "invalid  Firstmate source is not a direct Git checkout" >&2
  failed=1
elif [[ "$firstmate_origin" != "$firstmate_repo" || -n "$firstmate_status" ]]; then
  echo "invalid  Firstmate checkout source or cleanliness differs" >&2
  failed=1
elif [[ "$firstmate_head" == "$firstmate_ref" ]] \
  || git -C "$firstmate_source" merge-base --is-ancestor "$firstmate_ref" "$firstmate_head" 2>/dev/null; then
  printf 'ok       Firstmate %s (manifest floor %s)\n' "$firstmate_head" "$firstmate_ref"
else
  echo "invalid  Firstmate checkout is neither the pin nor its guarded descendant" >&2
  failed=1
fi
firstmate_home=$(read_pin firstmate_home)
if [[ -L "$firstmate_home" || ! -d "$firstmate_home" || -L "$(pi_home_path)" || ! -d "$(pi_home_path)" ]]; then
  echo "invalid  Firstmate or Pi home is redirected or missing" >&2
  failed=1
fi
backend_file="$firstmate_home/config/backend"
crew_file="$firstmate_home/config/crew-harness"
if [[ ! -f "$backend_file" || -L "$backend_file" || ! -f "$crew_file" || -L "$crew_file" ]]; then
  echo "invalid  Firstmate selections must be direct regular files" >&2
  failed=1
else
  printf 'backend: '
  head -n 1 "$backend_file" || failed=1
  printf 'crew harness: '
  head -n 1 "$crew_file" || failed=1
fi
if [[ $(read_pin backend) == herdr ]]; then
  herdr_integration="$(pi_home_path)/extensions/herdr-agent-state.ts"
  if [[ -f "$herdr_integration" && ! -L "$herdr_integration" ]] \
    && PI_CODING_AGENT_DIR="$(pi_home_path)" herdr integration status 2>/dev/null | grep -q '^pi: current '; then
    echo "ok       Herdr Pi lifecycle integration"
  else
    echo "invalid  Herdr Pi lifecycle integration is absent or mismatched" >&2
    failed=1
  fi
fi

if sudo -n true 2>/dev/null; then
  echo "security error: the daily agent user unexpectedly has passwordless sudo" >&2
  failed=1
else
  echo "ok       daily agent has no passwordless sudo"
fi

exit "$failed"
