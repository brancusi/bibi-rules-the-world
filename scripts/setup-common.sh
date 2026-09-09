#!/usr/bin/env bash
# Shared, user-scoped primitives for the portable installer. This file is a
# library: OS policy stays in setup-ubuntu.sh and setup-macos.sh.
set -euo pipefail

bibi_die() {
  printf 'bibi-setup: %s\n' "$*" >&2
  exit 1
}

bibi_note() {
  printf '%s\n' "$*"
}

# Refuse redirected installation trees before creating anything beneath them.
bibi_require_directory_chain() {
  local current=${1%/}
  case "$current" in /*) ;; *) bibi_die "installation path must be absolute: $1" ;; esac
  while [ "$current" != / ] && [ -n "$current" ]; do
    if [ -L "$current" ] || { [ -e "$current" ] && [ ! -d "$current" ]; }; then
      bibi_die "installation directory chain is redirected or not a directory: $current"
    fi
    current=${current%/*}
    [ -n "$current" ] || current=/
  done
}

# Atomically replace only a file previously written by this installer.
bibi_activate_managed_file() {
  local staged=$1 target=$2 marker=$3
  if [ -L "$target" ] || { [ -e "$target" ] && [ ! -f "$target" ]; }; then
    rm -f "$staged"
    bibi_die "managed file target must be a direct regular file: $target"
  fi
  if [ -e "$target" ] && ! grep -Fqx "$marker" "$target"; then
    rm -f "$staged"
    bibi_die "refusing to replace unmanaged file $target"
  fi
  mv -f "$staged" "$target"
}

# Read one unindented scalar from group_vars/all.yml without evaluating it as
# shell. Portable setup keys are deliberately plain quoted or bare scalars.
bibi_yaml_scalar() {
  local key=$1 file=${2:-"$BIBI_ROOT/group_vars/all.yml"} value count
  count=$(awk -v key="$key" 'index($0, key ":") == 1 { n++ } END { print n + 0 }' "$file")
  [ "$count" = 1 ] || bibi_die "manifest key '$key' must occur exactly once in $file"
  value=$(awk -v key="$key" 'index($0, key ":") == 1 { sub("^[^:]*:[[:space:]]*", ""); print; exit }' "$file")
  case "$value" in
    \"*\") value=${value#\"}; value=${value%\"} ;;
    \'*\') value=${value#\'}; value=${value%\'} ;;
  esac
  [ -n "$value" ] || bibi_die "manifest key '$key' is empty"
  case "$value" in *$'\n'*|*$'\r'*) bibi_die "manifest key '$key' contains a control character" ;; esac
  printf '%s\n' "$value"
}

# Print a top-level YAML string list, one value per line. No YAML is executed.
bibi_yaml_list() {
  local key=$1 file=${2:-"$BIBI_ROOT/group_vars/all.yml"}
  awk -v key="$key" '
    $0 == key ":" { if (seen++) exit 2; in_list=1; next }
    in_list && /^  - / {
      sub(/^  - /, "")
      if ($0 ~ /^".*"$/ || $0 ~ /^\047.*\047$/) { sub(/^./, ""); sub(/.$/, "") }
      print
      next
    }
    in_list { exit }
  ' "$file"
}

bibi_has_profile() {
  local wanted=$1 item old_ifs=$IFS
  IFS=,
  for item in $BIBI_PROFILES; do
    if [ "$item" = "$wanted" ] || [ "$item" = ubuntu-compat ]; then
      IFS=$old_ifs
      return 0
    fi
  done
  IFS=$old_ifs
  return 1
}

bibi_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    bibi_die "need sha256sum or shasum for artifact verification"
  fi
}

bibi_download_verified() {
  local url=$1 expected=$2 max_bytes=$3 destination=$4 actual size
  if ! "${BIBI_CURL:-curl}" -fsSL --retry 3 --max-filesize "$max_bytes" "$url" -o "$destination"; then
    rm -f "$destination"
    bibi_die "bounded download failed: $url"
  fi
  size=$(wc -c < "$destination" | tr -d '[:space:]')
  case "$size" in
    ''|*[!0-9]*) rm -f "$destination"; bibi_die "could not measure downloaded artifact $destination" ;;
  esac
  if [ "$size" -gt "$max_bytes" ]; then
    rm -f "$destination"
    bibi_die "download exceeded $max_bytes bytes: $url"
  fi
  actual=$(bibi_sha256 "$destination")
  if [ "$actual" != "$expected" ]; then
    rm -f "$destination"
    bibi_die "checksum mismatch for $url (expected $expected, got $actual); active install unchanged"
  fi
}

bibi_atomic_symlink() {
  local target=$1 link=$2 tmp
  mkdir -p "$(dirname "$link")"
  if [ -e "$link" ] || [ -L "$link" ]; then
    [ -L "$link" ] || bibi_die "refusing to replace non-symlink command path $link"
  fi
  tmp="$link.tmp.$$"
  rm -f "$tmp"
  ln -s "$target" "$tmp"
  mv -f "$tmp" "$link"
  if [ ! -L "$link" ] || [ "$(readlink "$link")" != "$target" ]; then
    bibi_die "atomic command activation failed for $link"
  fi
}

bibi_verify_npm_registry_integrity() {
  local spec=$1 expected=$2 actual
  actual=$("${BIBI_NPM:-npm}" view "$spec" dist.integrity 2>/dev/null | tr -d '\r\n') \
    || bibi_die "could not read registry integrity for $spec"
  [ "$actual" = "$expected" ] \
    || bibi_die "registry integrity mismatch for $spec (expected $expected, got ${actual:-empty})"
}

# Stage each npm CLI in its own immutable prefix and switch only its command
# symlink after exact package metadata verification.
bibi_install_npm_cli() {
  local spec=$1 integrity=$2 command_name=$3 package_name version safe root final stage actual
  package_name=${spec%@*}
  version=${spec##*@}
  safe=$(printf '%s' "$package_name" | tr '@/ ' '___')
  root="$BIBI_SHARE/npm/$safe"
  final="$root/$version"
  if [ -L "$final" ] || { [ -e "$final" ] && [ ! -d "$final" ]; }; then
    bibi_die "npm release path must be a direct directory: $final"
  fi
  bibi_verify_npm_registry_integrity "$spec" "$integrity"
  if [ ! -d "$final" ]; then
    stage="$root/.stage-$version-$$"
    rm -rf "$stage"
    mkdir -p "$stage"
    if ! npm_config_ignore_scripts=true "${BIBI_NPM:-npm}" install --prefix "$stage" \
      --ignore-scripts --no-audit --no-fund "$spec" >/dev/null; then
      rm -rf "$stage"
      bibi_die "npm staging failed for $spec; active command unchanged"
    fi
    actual=$("${BIBI_NODE:-node}" -e \
      'const p=require(process.argv[1]); process.stdout.write(String(p.version||""))' \
      "$stage/node_modules/$package_name/package.json" 2>/dev/null || true)
    if [ "$actual" != "$version" ] || [ ! -x "$stage/node_modules/.bin/$command_name" ]; then
      rm -rf "$stage"
      bibi_die "staged npm package $spec failed exact version/bin verification"
    fi
    mkdir -p "$root"
    mv "$stage" "$final"
  fi
  actual=$("${BIBI_NODE:-node}" -e \
    'const p=require(process.argv[1]); process.stdout.write(String(p.version||""))' \
    "$final/node_modules/$package_name/package.json" 2>/dev/null || true)
  [ "$actual" = "$version" ] || bibi_die "existing npm release $final is not $spec; refusing overwrite"
  [ -x "$final/node_modules/.bin/$command_name" ] || bibi_die "missing $command_name in $final"
  bibi_atomic_symlink "$final/node_modules/.bin/$command_name" "$BIBI_BIN/$command_name"
}

bibi_install_node_darwin() {
  local version sha archive url root final stage actual command_name
  version=$(bibi_yaml_scalar node_version)
  sha=$(bibi_yaml_scalar node_darwin_arm64_sha256)
  root="$BIBI_SHARE/node"
  final="$root/$version"
  if [ -L "$final" ] || { [ -e "$final" ] && [ ! -d "$final" ]; }; then
    bibi_die "Node release path must be a direct directory: $final"
  fi
  if [ ! -e "$final" ]; then
    stage="$root/.stage-$version-$$"
    archive="${TMPDIR:-/tmp}/node-$version-$$.tar.gz"
    rm -rf "$stage" "$archive"
    url="https://nodejs.org/dist/v$version/node-v$version-darwin-arm64.tar.gz"
    bibi_download_verified "$url" "$sha" 80000000 "$archive"
    mkdir -p "$stage"
    if ! "${BIBI_TAR:-tar}" -xzf "$archive" --strip-components=1 -C "$stage"; then
      rm -rf "$stage" "$archive"
      bibi_die "could not extract $url; active Node unchanged"
    fi
    rm -f "$archive"
    actual=$("$stage/bin/node" --version 2>/dev/null || true)
    [ "$actual" = "v$version" ] || { rm -rf "$stage"; bibi_die "staged Node reports ${actual:-nothing}, expected v$version"; }
    mkdir -p "$root"
    mv "$stage" "$final"
  fi
  [ "$("$final/bin/node" --version 2>/dev/null || true)" = "v$version" ] \
    || bibi_die "existing Node release $final differs from v$version"
  for command_name in node npm npx corepack; do
    [ -e "$final/bin/$command_name" ] && bibi_atomic_symlink "$final/bin/$command_name" "$BIBI_BIN/$command_name"
  done
  BIBI_NODE="$BIBI_BIN/node"
  BIBI_NPM="$BIBI_BIN/npm"
  export BIBI_NODE BIBI_NPM
}

bibi_install_firstmate_source() {
  local repo ref root final stage origin head dirty
  repo=$(bibi_yaml_scalar firstmate_repo_url)
  ref=$(bibi_yaml_scalar firstmate_ref)
  root=${BIBI_FIRSTMATE_SOURCE_ROOT:-"$BIBI_SHARE/firstmate/source"}
  if [ -L "$root" ] || { [ -e "$root" ] && [ ! -d "$root" ]; }; then
    bibi_die "Firstmate source root must be a direct directory: $root"
  fi
  final="$root/$ref"
  if [ -L "$final" ] || [ -L "$final/.git" ]; then
    bibi_die "Firstmate source must be a direct checkout, not a symlink: $final"
  fi
  if [ ! -d "$final/.git" ]; then
    [ ! -e "$final" ] || bibi_die "partial Firstmate path exists without a Git checkout: $final"
    stage="$root/.stage-$ref-$$"
    rm -rf "$stage"
    mkdir -p "$root"
    "${BIBI_GIT:-git}" init -q "$stage"
    "${BIBI_GIT:-git}" -C "$stage" remote add origin "$repo"
    if ! "${BIBI_GIT:-git}" -C "$stage" fetch -q --depth 1 origin "$ref" \
      || ! "${BIBI_GIT:-git}" -C "$stage" checkout -q --detach FETCH_HEAD; then
      rm -rf "$stage"
      bibi_die "could not stage reviewed Firstmate commit $ref"
    fi
    head=$("${BIBI_GIT:-git}" -C "$stage" rev-parse HEAD)
    [ "$head" = "$ref" ] || { rm -rf "$stage"; bibi_die "Firstmate staged $head instead of $ref"; }
    mv "$stage" "$final"
  fi
  origin=$("${BIBI_GIT:-git}" -C "$final" remote get-url origin 2>/dev/null || true)
  head=$("${BIBI_GIT:-git}" -C "$final" rev-parse HEAD 2>/dev/null || true)
  dirty=$("${BIBI_GIT:-git}" -C "$final" status --porcelain --untracked-files=all 2>/dev/null || true)
  [ "$origin" = "$repo" ] || bibi_die "Firstmate checkout source differs at $final"
  [ -z "$dirty" ] || bibi_die "Firstmate checkout has local changes at $final"
  if [ "$head" != "$ref" ]; then
    "${BIBI_GIT:-git}" -C "$final" merge-base --is-ancestor "$ref" "$head" 2>/dev/null \
      || bibi_die "Firstmate checkout $head is not a clean guarded advance from $ref; refusing reset"
    bibi_note "Preserving guarded Firstmate advance $head (manifest floor $ref)."
  fi
  BIBI_FIRSTMATE_SOURCE=$final
  BIBI_FIRSTMATE_ACTIVE_REF=$head
  export BIBI_FIRSTMATE_SOURCE BIBI_FIRSTMATE_ACTIVE_REF
}

bibi_install_upstream_runtime() {
  local name=$1 script=$2 version=$3 stage actual final
  final="$BIBI_SHARE/runtime/$name/$version/$name"
  if [ -L "$final" ] || { [ -e "$final" ] && [ ! -f "$final" ]; }; then
    bibi_die "$name release path must be a direct regular file: $final"
  fi
  if [ -e "$final" ]; then
    [ -x "$final" ] || bibi_die "existing $name release is not executable; refusing overwrite: $final"
    actual=$("$final" --version 2>/dev/null | head -n 1 || true)
    case "$actual" in
      *"$version"*) bibi_atomic_symlink "$final" "$BIBI_BIN/$name"; return ;;
      *) bibi_die "existing $name release at $final does not report $version; refusing overwrite" ;;
    esac
  fi
  stage="$BIBI_SHARE/runtime/.stage-$name-$$"
  rm -rf "$stage"
  mkdir -p "$stage"
  if ! "$BIBI_FIRSTMATE_SOURCE/bin/$script" "$stage" >/dev/null; then
    rm -rf "$stage"
    bibi_die "upstream $name installer failed; active command unchanged"
  fi
  actual=$("$stage/$name" --version 2>/dev/null | head -n 1)
  case "$actual" in *"$version"*) ;; *) rm -rf "$stage"; bibi_die "staged $name reports '${actual:-nothing}', expected $version" ;; esac
  mkdir -p "$(dirname "$final")"
  mv "$stage/$name" "$final.new.$$"
  chmod 0755 "$final.new.$$"
  mv -f "$final.new.$$" "$final"
  rm -rf "$stage"
  bibi_atomic_symlink "$final" "$BIBI_BIN/$name"
}

bibi_safe_config_line() {
  local path=$1 value=$2 current tmp
  if [ -L "$path" ]; then
    bibi_die "refusing symlinked config path $path"
  fi
  if [ -e "$path" ]; then
    if [ ! -f "$path" ]; then
      bibi_die "refusing non-regular config path $path"
    fi
    current=$(head -n 1 "$path")
    [ "$current" = "$value" ] || bibi_die "preserving existing $path value '$current'; requested '$value' needs review"
    return
  fi
  mkdir -p "$(dirname "$path")"
  tmp="$path.tmp.$$"
  (umask 077; printf '%s\n' "$value" > "$tmp")
  mv "$tmp" "$path"
}

bibi_create_fresh_instance() {
  local backend=$1 path
  for path in "$BIBI_FM_HOME" "$BIBI_FM_HOME/config" "$BIBI_PI_HOME" "$BIBI_PI_HOME/extensions"; do
    if [ -L "$path" ] || { [ -e "$path" ] && [ ! -d "$path" ]; }; then
      bibi_die "instance path must be a real directory: $path"
    fi
  done
  if [ ! -e "$BIBI_FM_HOME" ]; then
    mkdir -p "$BIBI_FM_HOME"
    chmod 0700 "$BIBI_FM_HOME"
    BIBI_FRESH_HOME_CREATED=true
  else
    BIBI_FRESH_HOME_CREATED=false
  fi
  mkdir -p "$BIBI_FM_HOME/config" "$BIBI_PI_HOME/extensions"
  chmod 0700 "$BIBI_FM_HOME" "$BIBI_FM_HOME/config" "$BIBI_PI_HOME" "$BIBI_PI_HOME/extensions"
  bibi_safe_config_line "$BIBI_FM_HOME/config/backend" "$backend"
  bibi_safe_config_line "$BIBI_FM_HOME/config/crew-harness" pi
  export BIBI_FRESH_HOME_CREATED
}

bibi_assert_fresh_runtime_state_absent() {
  local path
  [ "${BIBI_FRESH_HOME_CREATED:-false}" = true ] || return 0
  for path in auth.json models-store.json trust.json sessions; do
    if [ -e "$BIBI_PI_HOME/$path" ] || [ -L "$BIBI_PI_HOME/$path" ]; then
      bibi_die "fresh installer created forbidden pre-launch Pi state: $BIBI_PI_HOME/$path"
    fi
  done
}

bibi_install_herdr_pi_integration() {
  local extension="$BIBI_PI_HOME/extensions/herdr-agent-state.ts" status
  if [ -L "$extension" ]; then
    bibi_die "preserving unsafe existing Herdr Pi integration path $extension"
  elif [ ! -e "$extension" ]; then
    PI_CODING_AGENT_DIR="$BIBI_PI_HOME" "$BIBI_BIN/herdr" integration install pi >/dev/null \
      || bibi_die "Herdr Pi integration install failed"
  elif [ ! -f "$extension" ]; then
    bibi_die "preserving unsafe existing Herdr Pi integration path $extension"
  fi
  status=$(PI_CODING_AGENT_DIR="$BIBI_PI_HOME" "$BIBI_BIN/herdr" integration status 2>/dev/null || true)
  printf '%s\n' "$status" | grep -q '^pi: current ' \
    || bibi_die "preserving absent/outdated/differing Pi integration; run doctor and review migration"
}

bibi_write_launcher() {
  local launcher="$BIBI_BIN/bibi" tmp
  tmp="$launcher.tmp.$$"
  umask 077
  {
    printf '%s\n' '#!/usr/bin/env bash' '# managed-by: bibi-portable-setup' 'set -euo pipefail'
    printf 'export FM_HOME=%q\n' "$BIBI_FM_HOME"
    printf 'export PI_CODING_AGENT_DIR=%q\n' "$BIBI_PI_HOME"
    printf 'export TREEHOUSE_DIR=%q\n' "$BIBI_FM_HOME/treehouse"
    printf '%s\n' 'export TREEHOUSE_NO_UPDATE_CHECK=1'
    printf 'export PATH=%q:%s\n' "$BIBI_BIN" "\"\$PATH\""
    printf 'cd %q\n' "$BIBI_FIRSTMATE_SOURCE"
    printf '%s\n' 'exec pi "$@"'
  } > "$tmp"
  chmod 0755 "$tmp"
  bibi_activate_managed_file "$tmp" "$launcher" '# managed-by: bibi-portable-setup'
}

bibi_write_receipt() {
  local receipt=$1 setup_ref tmp git_version gh_version jq_version tmux_version
  setup_ref=$(git -C "$BIBI_ROOT" rev-parse HEAD 2>/dev/null || printf unknown)
  git_version=$(git --version 2>&1 | awk 'NR == 1 { print }')
  gh_version=$(gh --version 2>&1 | awk 'NR == 1 { print }')
  jq_version=$(jq --version 2>&1 | awk 'NR == 1 { print }')
  tmux_version=$(tmux -V 2>&1 | awk 'NR == 1 { print }')
  if [ -z "$git_version" ] || [ -z "$gh_version" ] || [ -z "$jq_version" ] || [ -z "$tmux_version" ]; then
    bibi_die "could not record Homebrew prerequisite versions"
  fi
  mkdir -p "$(dirname "$receipt")"
  tmp="$receipt.tmp.$$"
  umask 077
  {
    printf '%s\n' 'schema=bibi-portable-receipt.v1'
    printf 'setup_repo_commit=%s\n' "$setup_ref"
    printf 'firstmate_manifest_ref=%s\n' "$(bibi_yaml_scalar firstmate_ref)"
    printf 'firstmate_repo=%s\n' "$(bibi_yaml_scalar firstmate_repo_url)"
    printf 'firstmate_active_ref=%s\n' "$BIBI_FIRSTMATE_ACTIVE_REF"
    printf 'firstmate_source=%s\n' "$BIBI_FIRSTMATE_SOURCE"
    printf 'fm_home=%s\n' "$BIBI_FM_HOME"
    printf 'pi_home=%s\n' "$BIBI_PI_HOME"
    printf 'treehouse_dir=%s\n' "$BIBI_FM_HOME/treehouse"
    printf 'backend=%s\n' "$BIBI_BACKEND"
    printf 'profiles=%s\n' "$BIBI_PROFILES"
    printf 'platform=%s-%s\n' "$BIBI_OS" "$BIBI_ARCH"
    printf 'fresh_home_created=%s\n' "$BIBI_FRESH_HOME_CREATED"
    printf 'git_version=%s\n' "$git_version"
    printf 'gh_version=%s\n' "$gh_version"
    printf 'jq_version=%s\n' "$jq_version"
    printf 'tmux_version=%s\n' "$tmux_version"
    printf 'node_version=%s\n' "$(bibi_yaml_scalar node_version)"
    printf 'node_archive_sha256=%s\n' "$(bibi_yaml_scalar node_darwin_arm64_sha256)"
    printf 'pi_version=%s\n' "$(bibi_yaml_scalar pi_version)"
    printf 'pi_registry_integrity=%s\n' "$(bibi_yaml_scalar pi_integrity)"
    printf 'herdr_version=%s\n' "$(bibi_yaml_scalar herdr_version)"
    printf 'treehouse_version=%s\n' "$(bibi_yaml_scalar treehouse_version)"
    printf 'no_mistakes_version=%s\n' "$(bibi_yaml_scalar no_mistakes_version)"
    printf 'no_mistakes_archive_sha256=%s\n' "$(bibi_yaml_scalar no_mistakes_darwin_arm64_sha256)"
    printf 'wrangler_package=%s\n' "$(bibi_yaml_scalar wrangler_package)"
    printf 'wrangler_registry_integrity=%s\n' "$(bibi_yaml_scalar wrangler_integrity)"
    printf 'firecrawl_cli_package=%s\n' "$(bibi_yaml_scalar firecrawl_cli_package)"
    printf 'firecrawl_registry_integrity=%s\n' "$(bibi_yaml_scalar firecrawl_cli_integrity)"
    printf 'doctl_version=%s\n' "$(bibi_yaml_scalar doctl_version)"
    printf 'doctl_archive_sha256=%s\n' "$(bibi_yaml_scalar doctl_darwin_arm64_sha256)"
    printf 'jdk_runtime_version=%s\n' "$(bibi_yaml_scalar jdk_runtime_version)"
    printf 'jdk_archive_sha256=%s\n' "$(bibi_yaml_scalar jdk_darwin_arm64_sha256)"
    printf 'clojure_cli_version=%s\n' "$(bibi_yaml_scalar clojure_cli_version)"
    printf 'clojure_archive_sha256=%s\n' "$(bibi_yaml_scalar clojure_cli_sha256)"
    printf 'axi_packages=%s\n' "$(bibi_yaml_list axi_packages | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    printf 'pi_public_packages=%s\n' "$(bibi_yaml_list pi_public_packages | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    printf 'cloudflare_skills_repo=%s\n' "$(bibi_yaml_scalar cloudflare_skills_repo_url)"
    printf 'cloudflare_skills_ref=%s\n' "$(bibi_yaml_scalar cloudflare_skills_ref)"
    printf 'cloudflare_skills_checkout=%s\n' "$BIBI_SHARE/cloudflare-skills/$(bibi_yaml_scalar cloudflare_skills_ref)"
    printf 'cloudflare_skill_names=%s\n' "$(bibi_yaml_list portable_cloudflare_skill_names | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
    printf '%s\n' 'credentials_recorded=false'
  } > "$tmp"
  chmod 0600 "$tmp"
  bibi_activate_managed_file "$tmp" "$receipt" 'schema=bibi-portable-receipt.v1'
}
