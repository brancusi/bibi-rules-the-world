#!/usr/bin/env bash
# Native Apple Silicon adapter. macOS support is provisional until the manual
# Mac Mini receipt in README.md has been completed.
set -euo pipefail

# shellcheck source=scripts/setup-common.sh
. "$BIBI_ROOT/scripts/setup-common.sh"

BIBI_MODE=${BIBI_MODE:?bibi dispatcher must set BIBI_MODE}
[ "$BIBI_OS" = Darwin ] || bibi_die "macOS adapter received $BIBI_OS"
[ "$BIBI_ARCH" = arm64 ] || bibi_die "phase 1 supports native Apple Silicon (Darwin-arm64) only, found $BIBI_ARCH"

BIBI_TARGET_HOME=${BIBI_TARGET_HOME:-$HOME}
BIBI_BIN="$BIBI_TARGET_HOME/.local/bin"
BIBI_SHARE="$BIBI_TARGET_HOME/.local/share/bibi"
BIBI_STATE="$BIBI_TARGET_HOME/.local/state/bibi"
BIBI_FIRSTMATE_SOURCE_ROOT="$BIBI_TARGET_HOME/.local/share/firstmate/source"
BIBI_FM_HOME="$BIBI_TARGET_HOME/.local/share/firstmate/instances/main"
BIBI_PI_HOME="$BIBI_FM_HOME/pi"
BIBI_RECEIPT="$BIBI_STATE/provisioned-versions"
BIBI_PRIVATE_SOURCES=
BIBI_BREW=${BIBI_BREW:-/opt/homebrew/bin/brew}
export BIBI_BIN BIBI_SHARE BIBI_STATE BIBI_FIRSTMATE_SOURCE_ROOT BIBI_FM_HOME BIBI_PI_HOME

macos_plan() {
  local firstmate_ref node_version pi_version no_mistakes_version
  firstmate_ref=$(bibi_yaml_scalar firstmate_ref)
  node_version=$(bibi_yaml_scalar node_version)
  pi_version=$(bibi_yaml_scalar pi_version)
  no_mistakes_version=$(bibi_yaml_scalar no_mistakes_version)
  cat <<EOF
PLAN ONLY - no files, packages, services, settings, or credentials will change.
Platform: Darwin-arm64 (provisional; real Mac Mini acceptance is still required)
Backend: $BIBI_BACKEND
Profiles: $BIBI_PROFILES
Prerequisites checked on apply: supported macOS, Xcode Command Line Tools, /opt/homebrew/bin/brew
OS packages on apply: git, gh, jq, tmux (Homebrew; interactive authorization may be required)
Node: $node_version, official darwin-arm64 archive, SHA-256 $(bibi_yaml_scalar node_darwin_arm64_sha256)
Pi: @earendil-works/pi-coding-agent@$pi_version, registry integrity $(bibi_yaml_scalar pi_integrity)
Firstmate: $(bibi_yaml_scalar firstmate_repo_url)@$firstmate_ref (unmodified)
Firstmate source: $BIBI_FIRSTMATE_SOURCE_ROOT/$firstmate_ref
Firstmate home: $BIBI_FM_HOME (new, independent; no projects/state/auth/trust/sessions imported)
Pi home: $BIBI_PI_HOME
Herdr/Treehouse: upstream Firstmate pins $(bibi_yaml_scalar herdr_version)/$(bibi_yaml_scalar treehouse_version); official installer hashes and gates
Herdr Pi lifecycle integration: official bundled integration into exactly $BIBI_PI_HOME
no-mistakes: $no_mistakes_version, SHA-256 $(bibi_yaml_scalar no_mistakes_darwin_arm64_sha256)
Launcher: $BIBI_BIN/bibi
Receipt: $BIBI_RECEIPT (non-secret)
EOF
  if bibi_has_profile public-pi-extras; then
    printf 'Optional public Pi extras: %s\n' "$(bibi_yaml_list pi_public_packages | tr '\n' ' ')"
  fi
  if bibi_has_profile cloudflare; then
    printf 'Optional Cloudflare: %s at %s plus Wrangler %s\n' \
      "$(bibi_yaml_scalar cloudflare_skills_repo_url)" "$(bibi_yaml_scalar cloudflare_skills_ref)" \
      "$(bibi_yaml_scalar wrangler_package)"
  fi
  if bibi_has_profile digitalocean; then
    printf 'Optional DigitalOcean: doctl %s, SHA-256 %s; authentication remains interactive\n' \
      "$(bibi_yaml_scalar doctl_version)" "$(bibi_yaml_scalar doctl_darwin_arm64_sha256)"
  fi
  if bibi_has_profile web-research; then
    printf 'Optional web research: %s and npm:pi-web-access@0.24.0; login remains interactive\n' \
      "$(bibi_yaml_scalar firecrawl_cli_package)"
  fi
  if bibi_has_profile clojure; then
    printf 'Optional Clojure: Temurin %s and Clojure CLI %s with reviewed hashes\n' \
      "$(bibi_yaml_scalar jdk_version)" "$(bibi_yaml_scalar clojure_cli_version)"
  fi
  if bibi_has_profile browser; then
    printf '%s\n' 'Optional browser: existing Google Chrome plus a disposable local-page smoke; no floating Homebrew cask.'
  fi
  if bibi_has_profile private-capabilities; then
    printf 'Optional private capabilities: authenticated exact-ref manifest %s\n' "${BIBI_PRIVATE_MANIFEST:-<required>}"
    [ -z "${BIBI_PRIVATE_MANIFEST:-}" ] || bibi_private_manifest_sources "$BIBI_PRIVATE_MANIFEST" | sed 's/^/  source: /'
  fi
  if [ "$BIBI_LAUNCH_AGENT" = true ]; then
    printf '%s\n' "Optional LaunchAgent plist: $BIBI_TARGET_HOME/Library/LaunchAgents/dev.bibi.herdr.default.plist"
    printf '%s\n' 'It starts only after Aqua login; it is not boot persistence and it never starts Pi.'
  fi
  cat <<'EOF'
Apply never changes FileVault, automatic login, Remote Login, accounts, firewall, or energy settings.
After apply: run gh auth login as needed, launch bibi, review this exact Firstmate clone, accept Pi project trust, then use /login. No credentials are copied.
EOF
}

bibi_private_manifest_sources() {
  local file=$1 line schema_seen=false source source_base source_ref size count=0 seen='|'
  if [ ! -f "$file" ] || [ -L "$file" ]; then
    bibi_die "private manifest must be a regular non-symlink file: $file"
  fi
  size=$(wc -c < "$file" | tr -d '[:space:]')
  case "$size" in ''|*[!0-9]*) bibi_die "could not measure private manifest" ;; esac
  [ "$size" -le 65536 ] || bibi_die "private manifest exceeds 65536 bytes"
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    if [ "$line" = schema=bibi-private-capabilities.v1 ]; then
      [ "$schema_seen" = false ] || bibi_die "duplicate private manifest schema"
      schema_seen=true
      continue
    fi
    case "$line" in
      source=git:https://github.com/*.git@*)
        source=${line#source=}
        source_base=${source%@*}
        source_ref=${source##*@}
        if ! [[ "$source_base" =~ ^git:https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git$ ]] \
          || [ "${#source_ref}" -ne 40 ]; then
          bibi_die "private manifest source must be credential-free GitHub HTTPS at a 40-hex commit"
        fi
        case "$source_ref" in *[!0-9a-f]*) bibi_die "private manifest commit must be 40 lowercase hex characters" ;; esac
        count=$((count + 1))
        [ "$count" -le 32 ] || bibi_die "private manifest exceeds 32 sources"
        case "$seen" in *"|$source|"*) bibi_die "duplicate private manifest source" ;; esac
        seen="$seen$source|"
        printf '%s\n' "$source"
        ;;
      *) bibi_die "private manifest accepts only source=git:https://github.com/...@<40-hex-commit> rows" ;;
    esac
  done < "$file"
  [ "$schema_seen" = true ] || bibi_die "private manifest schema must be bibi-private-capabilities.v1"
  [ "$count" -gt 0 ] || bibi_die "private manifest contains no sources"
}

macos_browser_smoke() {
  local browser profile output errors pid count=0 status=0
  browser='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  [ -x "$browser" ] || bibi_die "browser profile requires an existing reviewed Google Chrome application"
  profile=$(mktemp -d "${TMPDIR:-/tmp}/bibi-browser-smoke.XXXXXX") \
    || bibi_die "could not create disposable browser profile"
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
    bibi_die "browser smoke exceeded 30 seconds"
  fi
  wait "$pid" || status=$?
  if [ "$status" -ne 0 ] || ! grep -q 'bibi-browser-smoke' "$output"; then
    rm -rf "$profile"
    bibi_die "browser profile failed its disposable local-page smoke"
  fi
  rm -rf "$profile"
}

macos_preflight() {
  local path
  [ "$(id -u)" -ne 0 ] || bibi_die "run the macOS installer as the standard daily user, never root"
  for path in \
    "$BIBI_TARGET_HOME" "$BIBI_TARGET_HOME/.local" "$BIBI_BIN" \
    "$BIBI_TARGET_HOME/.local/share" "$BIBI_SHARE" "$BIBI_SHARE/node" \
    "$BIBI_SHARE/npm" "$BIBI_SHARE/runtime" "$BIBI_SHARE/toolchains" \
    "$BIBI_SHARE/cloudflare-skills" "$BIBI_SHARE/verify" \
    "$BIBI_TARGET_HOME/.local/state" "$BIBI_STATE" \
    "$BIBI_FIRSTMATE_SOURCE_ROOT" "$(dirname "$BIBI_FM_HOME")" "$BIBI_FM_HOME" "$BIBI_PI_HOME"; do
    bibi_require_directory_chain "$path"
  done
  if [ "$BIBI_LAUNCH_AGENT" = true ]; then
    for path in "$BIBI_TARGET_HOME/Library" "$BIBI_TARGET_HOME/Library/LaunchAgents" \
      "$BIBI_TARGET_HOME/Library/Logs" "$BIBI_TARGET_HOME/Library/Logs/Firstmate"; do
      bibi_require_directory_chain "$path"
    done
  fi
  command -v curl >/dev/null 2>&1 || bibi_die "curl is required"
  command -v tar >/dev/null 2>&1 || bibi_die "tar is required"
  command -v shasum >/dev/null 2>&1 || bibi_die "shasum is required"
  if ! "${BIBI_XCODE_SELECT:-xcode-select}" -p >/dev/null 2>&1; then
    bibi_die "Xcode Command Line Tools are required. Run 'xcode-select --install', complete Apple's UI, then rerun"
  fi
  [ -x "$BIBI_BREW" ] \
    || bibi_die "Homebrew is required at /opt/homebrew on Apple Silicon. Follow https://docs.brew.sh/Installation and rerun"
  if bibi_has_profile private-capabilities; then
    [ -n "${BIBI_PRIVATE_MANIFEST:-}" ] || bibi_die "--profile private-capabilities requires --private-manifest PATH"
    BIBI_PRIVATE_SOURCES=$(bibi_private_manifest_sources "$BIBI_PRIVATE_MANIFEST")
    command -v gh >/dev/null 2>&1 || [ -x "$BIBI_BIN/gh" ] || bibi_die "GitHub CLI is required for private capabilities"
    "${BIBI_GH:-gh}" auth status >/dev/null 2>&1 \
      || bibi_die "private profile requires a fresh 'gh auth login' before any installer mutation"
  fi
  if bibi_has_profile browser; then
    macos_browser_smoke
  fi
  [ "$BIBI_LAUNCH_AGENT" = false ] || [ "$BIBI_BACKEND" = herdr ] \
    || bibi_die "--launch-agent requires --backend herdr"
}

install_no_mistakes() {
  local version sha archive url stage extracted actual final
  version=$(bibi_yaml_scalar no_mistakes_version)
  sha=$(bibi_yaml_scalar no_mistakes_darwin_arm64_sha256)
  final="$BIBI_SHARE/runtime/no-mistakes/$version/no-mistakes"
  if [ -L "$final" ] || { [ -e "$final" ] && [ ! -f "$final" ]; }; then
    bibi_die "no-mistakes release path must be a direct regular file: $final"
  fi
  if [ -e "$final" ]; then
    if [ ! -x "$final" ] || ! "$final" --version 2>/dev/null | head -n 1 | grep -q "$version"; then
      bibi_die "existing no-mistakes release does not report $version; refusing overwrite"
    fi
    bibi_atomic_symlink "$final" "$BIBI_BIN/no-mistakes"
    return
  fi
  archive="${TMPDIR:-/tmp}/no-mistakes-$version-$$.tar.gz"
  stage="$BIBI_SHARE/runtime/.stage-no-mistakes-$$"
  rm -rf "$stage" "$archive"
  url="https://github.com/kunchenguid/no-mistakes/releases/download/v$version/no-mistakes-v$version-darwin-arm64.tar.gz"
  bibi_download_verified "$url" "$sha" 30000000 "$archive"
  mkdir -p "$stage"
  "${BIBI_TAR:-tar}" -xzf "$archive" -C "$stage" || { rm -rf "$stage" "$archive"; bibi_die "no-mistakes extraction failed"; }
  rm -f "$archive"
  extracted=$(find "$stage" -type f -name no-mistakes | head -n 1)
  [ -n "$extracted" ] || { rm -rf "$stage"; bibi_die "no-mistakes archive has no binary"; }
  chmod 0755 "$extracted"
  actual=$("$extracted" --version 2>/dev/null | head -n 1 || true)
  case "$actual" in *"$version"*) ;; *) rm -rf "$stage"; bibi_die "staged no-mistakes reports '${actual:-nothing}'" ;; esac
  mkdir -p "$(dirname "$final")"
  mv "$extracted" "$final.new.$$"
  mv -f "$final.new.$$" "$final"
  rm -rf "$stage"
  bibi_atomic_symlink "$final" "$BIBI_BIN/no-mistakes"
}

install_doctl() {
  local version sha archive url stage extracted final actual
  version=$(bibi_yaml_scalar doctl_version)
  sha=$(bibi_yaml_scalar doctl_darwin_arm64_sha256)
  final="$BIBI_SHARE/runtime/doctl/$version/doctl"
  if [ -L "$final" ] || { [ -e "$final" ] && [ ! -f "$final" ]; }; then
    bibi_die "doctl release path must be a direct regular file: $final"
  fi
  if [ -e "$final" ]; then
    if [ ! -x "$final" ] || ! "$final" version 2>/dev/null | grep -q "$version"; then
      bibi_die "existing doctl release does not report $version; refusing overwrite"
    fi
    bibi_atomic_symlink "$final" "$BIBI_BIN/doctl"
    return
  fi
  archive="${TMPDIR:-/tmp}/doctl-$version-$$.tar.gz"
  stage="$BIBI_SHARE/runtime/.stage-doctl-$$"
  rm -rf "$stage" "$archive"
  url="https://github.com/digitalocean/doctl/releases/download/v$version/doctl-$version-darwin-arm64.tar.gz"
  bibi_download_verified "$url" "$sha" 100000000 "$archive"
  mkdir -p "$stage"
  "${BIBI_TAR:-tar}" -xzf "$archive" -C "$stage" || { rm -rf "$stage" "$archive"; bibi_die "doctl extraction failed"; }
  rm -f "$archive"
  extracted=$(find "$stage" -type f -name doctl | head -n 1)
  [ -n "$extracted" ] || { rm -rf "$stage"; bibi_die "doctl archive has no binary"; }
  chmod 0755 "$extracted"
  actual=$("$extracted" version 2>/dev/null | head -n 1 || true)
  case "$actual" in *"$version"*) ;; *) rm -rf "$stage"; bibi_die "staged doctl reports '${actual:-nothing}'" ;; esac
  mkdir -p "$(dirname "$final")"; mv "$extracted" "$final.new.$$"; mv -f "$final.new.$$" "$final"; rm -rf "$stage"
  bibi_atomic_symlink "$final" "$BIBI_BIN/doctl"
}

install_clojure_profile() {
  local jdk_version jdk_sha jdk_archive jdk_url jdk_stage jdk_final actual
  local clj_version clj_sha clj_archive clj_stage clj_final
  jdk_version=$(bibi_yaml_scalar jdk_version)
  jdk_sha=$(bibi_yaml_scalar jdk_darwin_arm64_sha256)
  jdk_final="$BIBI_SHARE/toolchains/jdk-$jdk_version"
  if [ -L "$jdk_final" ] || { [ -e "$jdk_final" ] && [ ! -d "$jdk_final" ]; }; then
    bibi_die "Temurin release path must be a direct directory: $jdk_final"
  fi
  if [ ! -e "$jdk_final" ]; then
    jdk_archive="${TMPDIR:-/tmp}/temurin-$jdk_version-$$.tar.gz"
    jdk_stage="$BIBI_SHARE/toolchains/.stage-jdk-$$"
    rm -rf "$jdk_stage" "$jdk_archive"
    jdk_url="https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12%2B8/OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.12_8.tar.gz"
    bibi_download_verified "$jdk_url" "$jdk_sha" 300000000 "$jdk_archive"
    mkdir -p "$jdk_stage"
    "${BIBI_TAR:-tar}" -xzf "$jdk_archive" --strip-components=1 -C "$jdk_stage" || { rm -rf "$jdk_stage" "$jdk_archive"; bibi_die "Temurin extraction failed"; }
    rm -f "$jdk_archive"
    [ -x "$jdk_stage/Contents/Home/bin/java" ] || { rm -rf "$jdk_stage"; bibi_die "unexpected Temurin archive layout"; }
    actual=$("$jdk_stage/Contents/Home/bin/java" -fullversion 2>&1 || true)
    case "$actual" in *"$(bibi_yaml_scalar jdk_runtime_version)"*) ;; *) rm -rf "$jdk_stage"; bibi_die "staged Temurin version mismatch" ;; esac
    mkdir -p "$(dirname "$jdk_final")"; mv "$jdk_stage/Contents/Home" "$jdk_final"; rm -rf "$jdk_stage"
  fi
  actual=$("$jdk_final/bin/java" -fullversion 2>&1 || true)
  case "$actual" in *"$(bibi_yaml_scalar jdk_runtime_version)"*) ;; *) bibi_die "existing Temurin release version mismatch" ;; esac
  for actual in java javac jar; do bibi_atomic_symlink "$jdk_final/bin/$actual" "$BIBI_BIN/$actual"; done

  clj_version=$(bibi_yaml_scalar clojure_cli_version)
  clj_sha=$(bibi_yaml_scalar clojure_cli_sha256)
  clj_final="$BIBI_SHARE/toolchains/clojure-$clj_version"
  if [ -L "$clj_final" ] || { [ -e "$clj_final" ] && [ ! -d "$clj_final" ]; }; then
    bibi_die "Clojure release path must be a direct directory: $clj_final"
  fi
  if [ ! -e "$clj_final" ]; then
    clj_archive="${TMPDIR:-/tmp}/clojure-$clj_version-$$.tar.gz"
    clj_stage="$BIBI_SHARE/toolchains/.stage-clojure-$$"
    rm -rf "$clj_stage" "$clj_archive"
    bibi_download_verified "https://download.clojure.org/install/clojure-tools-$clj_version.tar.gz" "$clj_sha" 50000000 "$clj_archive"
    mkdir -p "$clj_stage"
    "${BIBI_TAR:-tar}" -xzf "$clj_archive" --strip-components=1 -C "$clj_stage" || { rm -rf "$clj_stage" "$clj_archive"; bibi_die "Clojure extraction failed"; }
    rm -f "$clj_archive"
    sed "s|PREFIX|$clj_final|g" "$clj_stage/clojure" > "$clj_stage/clojure.rendered"
    sed "s|BINDIR|$clj_final/bin|g" "$clj_stage/clj" > "$clj_stage/clj.rendered"
    mv "$clj_stage/clojure.rendered" "$clj_stage/clojure"; mv "$clj_stage/clj.rendered" "$clj_stage/clj"
    mkdir -p "$clj_stage/bin" "$clj_stage/libexec"
    cp "$clj_stage/clojure" "$clj_stage/clj" "$clj_stage/bin/"
    cp "$clj_stage"/*.jar "$clj_stage/libexec/"
    chmod 0755 "$clj_stage/bin/clojure" "$clj_stage/bin/clj"
    mkdir -p "$(dirname "$clj_final")"; mv "$clj_stage" "$clj_final"
  fi
  actual=$("$clj_final/bin/clojure" --version 2>/dev/null || true)
  case "$actual" in *"$clj_version"*) ;; *) bibi_die "existing Clojure release version mismatch" ;; esac
  bibi_atomic_symlink "$clj_final/bin/clojure" "$BIBI_BIN/clojure"
  bibi_atomic_symlink "$clj_final/bin/clj" "$BIBI_BIN/clj"
}

install_cloudflare_profile() {
  local repo ref checkout stage skill
  repo=$(bibi_yaml_scalar cloudflare_skills_repo_url)
  ref=$(bibi_yaml_scalar cloudflare_skills_ref)
  checkout="$BIBI_SHARE/cloudflare-skills/$ref"
  if [ -L "$checkout" ] || [ -L "$checkout/.git" ] \
    || { [ -e "$checkout" ] && [ ! -d "$checkout/.git" ]; }; then
    bibi_die "Cloudflare skill release path must be a direct Git checkout: $checkout"
  fi
  if [ ! -d "$checkout/.git" ]; then
    stage="$BIBI_SHARE/cloudflare-skills/.stage-$ref-$$"
    rm -rf "$stage"; mkdir -p "$(dirname "$stage")"
    "${BIBI_GIT:-git}" init -q "$stage"
    "${BIBI_GIT:-git}" -C "$stage" remote add origin "$repo"
    if ! "${BIBI_GIT:-git}" -C "$stage" fetch -q --depth 1 origin "$ref" \
      || ! "${BIBI_GIT:-git}" -C "$stage" checkout -q --detach FETCH_HEAD; then
      rm -rf "$stage"
      bibi_die "could not stage reviewed Cloudflare skills"
    fi
    [ "$("${BIBI_GIT:-git}" -C "$stage" rev-parse HEAD)" = "$ref" ] || { rm -rf "$stage"; bibi_die "Cloudflare skill checkout pin mismatch"; }
    mv "$stage" "$checkout"
  fi
  [ "$("${BIBI_GIT:-git}" -C "$checkout" remote get-url origin 2>/dev/null || true)" = "$repo" ] \
    || bibi_die "reviewed Cloudflare checkout source differs"
  [ "$("${BIBI_GIT:-git}" -C "$checkout" rev-parse HEAD 2>/dev/null || true)" = "$ref" ] \
    || bibi_die "reviewed Cloudflare checkout pin differs"
  [ -z "$("${BIBI_GIT:-git}" -C "$checkout" status --porcelain --untracked-files=all)" ] \
    || bibi_die "reviewed Cloudflare checkout has local changes"
  while IFS= read -r skill; do
    [ -r "$checkout/skills/$skill/SKILL.md" ] || bibi_die "reviewed Cloudflare skill missing: $skill"
    bibi_atomic_symlink "$checkout/skills/$skill" "$BIBI_PI_HOME/skills/$skill"
  done < <(bibi_yaml_list portable_cloudflare_skill_names)
}

install_pi_source() {
  local source=$1 ignore_scripts=${2:-false}
  if [ "$ignore_scripts" = true ]; then
    GIT_TERMINAL_PROMPT=0 NPM_CONFIG_IGNORE_SCRIPTS=true PI_CODING_AGENT_DIR="$BIBI_PI_HOME" \
      "$BIBI_BIN/pi" install "$source" || bibi_die "Pi package installation failed for $source"
  else
    GIT_TERMINAL_PROMPT=0 PI_CODING_AGENT_DIR="$BIBI_PI_HOME" "$BIBI_BIN/pi" install "$source" \
      || bibi_die "Pi package installation failed for $source"
  fi
}

install_public_pi_source() {
  local source=$1 spec integrity
  spec=${source#npm:}
  case "$spec" in
    @tmustier/pi-files-widget@0.2.0) integrity=$(bibi_yaml_scalar pi_files_widget_integrity) ;;
    pi-web-access@0.24.0) integrity=$(bibi_yaml_scalar pi_web_access_integrity) ;;
    *) bibi_die "public Pi source is not mapped to reviewed integrity: $source" ;;
  esac
  bibi_verify_npm_registry_integrity "$spec" "$integrity"
  install_pi_source "$source" true
}

install_launch_agent_plist() {
  local dir logs plist tmp home_xml bin_xml logs_xml
  dir="$BIBI_TARGET_HOME/Library/LaunchAgents"
  logs="$BIBI_TARGET_HOME/Library/Logs/Firstmate"
  plist="$dir/dev.bibi.herdr.default.plist"
  mkdir -p "$dir" "$logs"
  home_xml=$(printf '%s' "$BIBI_TARGET_HOME" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g')
  bin_xml=$(printf '%s' "$BIBI_BIN" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g')
  logs_xml=$(printf '%s' "$logs" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g')
  tmp="$plist.tmp.$$"
  sed -e "s|@@HOME@@|$home_xml|g" -e "s|@@BIN@@|$bin_xml|g" -e "s|@@LOG_DIR@@|$logs_xml|g" \
    "$BIBI_ROOT/templates/dev.bibi.herdr.default.plist" > "$tmp"
  "${BIBI_PLUTIL:-plutil}" -lint "$tmp" >/dev/null || { rm -f "$tmp"; bibi_die "generated LaunchAgent failed plutil -lint"; }
  chmod 0600 "$tmp"
  bibi_activate_managed_file "$tmp" "$plist" '<!-- managed-by: bibi-portable-setup -->'
  bibi_note "Installed $plist; it was not loaded. After Aqua login, review it and run: launchctl bootstrap gui/$(id -u) '$plist'"
}

macos_apply() {
  local pi_version spec source verify_source verify_target verify_tmp
  macos_preflight
  "$BIBI_BREW" install git gh jq tmux
  mkdir -p "$BIBI_BIN" "$BIBI_SHARE" "$BIBI_STATE"
  chmod 0700 "$BIBI_STATE"
  export PATH="$BIBI_BIN:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

  bibi_install_node_darwin
  pi_version=$(bibi_yaml_scalar pi_version)
  bibi_install_npm_cli "@earendil-works/pi-coding-agent@$pi_version" "$(bibi_yaml_scalar pi_integrity)" pi
  bibi_install_npm_cli gh-axi@0.1.30 "$(bibi_yaml_scalar gh_axi_integrity)" gh-axi
  bibi_install_npm_cli chrome-devtools-axi@0.1.31 "$(bibi_yaml_scalar chrome_devtools_axi_integrity)" chrome-devtools-axi
  bibi_install_npm_cli lavish-axi@0.1.82 "$(bibi_yaml_scalar lavish_axi_integrity)" lavish-axi
  bibi_install_npm_cli tasks-axi@0.2.6 "$(bibi_yaml_scalar tasks_axi_integrity)" tasks-axi
  bibi_install_npm_cli quota-axi@0.1.58 "$(bibi_yaml_scalar quota_axi_integrity)" quota-axi

  bibi_install_firstmate_source
  bibi_install_upstream_runtime treehouse fm-install-treehouse.sh "$(bibi_yaml_scalar treehouse_version)"
  if [ "$BIBI_BACKEND" = herdr ]; then
    bibi_install_upstream_runtime herdr fm-install-herdr.sh "$(bibi_yaml_scalar herdr_version)"
  fi
  install_no_mistakes
  bibi_create_fresh_instance "$BIBI_BACKEND"
  bibi_assert_fresh_runtime_state_absent
  if [ "$BIBI_BACKEND" = herdr ]; then
    bibi_install_herdr_pi_integration
  fi

  if bibi_has_profile public-pi-extras; then
    while IFS= read -r source; do install_public_pi_source "$source"; done < <(bibi_yaml_list pi_public_packages)
  fi
  if bibi_has_profile cloudflare; then
    spec=$(bibi_yaml_scalar wrangler_package)
    bibi_install_npm_cli "$spec" "$(bibi_yaml_scalar wrangler_integrity)" wrangler
    install_cloudflare_profile
  fi
  if bibi_has_profile digitalocean; then install_doctl; fi
  if bibi_has_profile web-research; then
    spec=$(bibi_yaml_scalar firecrawl_cli_package)
    bibi_install_npm_cli "$spec" "$(bibi_yaml_scalar firecrawl_cli_integrity)" firecrawl
    install_public_pi_source npm:pi-web-access@0.24.0
  fi
  if bibi_has_profile clojure; then install_clojure_profile; fi
  if bibi_has_profile private-capabilities; then
    "${BIBI_GH:-gh}" auth setup-git >/dev/null
    while IFS= read -r source; do install_pi_source "$source"; done <<< "$BIBI_PRIVATE_SOURCES"
  fi

  # AXI ambient hooks target other harnesses' global homes, so this existing-user
  # adapter installs the CLIs but deliberately does not run `setup hooks`.
  bibi_assert_fresh_runtime_state_absent
  bibi_write_launcher
  mkdir -p "$BIBI_SHARE/verify"
  for verify_source in "$BIBI_ROOT/scripts/verify-common.sh" "$BIBI_ROOT/scripts/verify-macos.sh"; do
    verify_target="$BIBI_SHARE/verify/${verify_source##*/}"
    verify_tmp="$verify_target.tmp.$$"
    cp "$verify_source" "$verify_tmp"
    chmod 0755 "$verify_tmp"
    bibi_activate_managed_file "$verify_tmp" "$verify_target" '# managed-by: bibi-portable-setup'
  done
  verify_target="$BIBI_BIN/bibi-verify"
  verify_tmp="$verify_target.tmp.$$"
  cat > "$verify_tmp" <<EOF
#!/usr/bin/env bash
# managed-by: bibi-portable-setup
exec '$BIBI_SHARE/verify/verify-macos.sh' '\$@'
EOF
  chmod 0755 "$verify_tmp"
  bibi_activate_managed_file "$verify_tmp" "$verify_target" '# managed-by: bibi-portable-setup'
  bibi_write_receipt "$BIBI_RECEIPT"
  [ "$BIBI_LAUNCH_AGENT" = false ] || install_launch_agent_plist
  bibi_note "Applied portable setup. No credentials, trust decisions, projects, or sessions were imported."
  bibi_note "Next: authenticate with gh as needed, run '$BIBI_BIN/bibi', approve only the reviewed clone, then use Pi /login."
}

if [ "$BIBI_MODE" = plan ]; then macos_plan; else macos_apply; fi
