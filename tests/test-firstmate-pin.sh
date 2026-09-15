#!/usr/bin/env bash
# Validate the selected Firstmate source against its own declared floors and
# load its tracked Pi resources with isolated, synthetic, offline Pi homes.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
SOURCE=${BIBI_FIRSTMATE_TEST_SOURCE:-/home/bibi/firstmate}
PIN=$(awk -F': ' '$1 == "firstmate_ref" {gsub(/"/, "", $2); print $2}' "$ROOT/group_vars/all.yml")
if [[ ! -d "$SOURCE/.git" || $(git -C "$SOURCE" rev-parse HEAD 2>/dev/null || true) != "$PIN" ]]; then
  echo "SKIP Firstmate pin load test (provide BIBI_FIRSTMATE_TEST_SOURCE at $PIN)"
  exit 0
fi

fail() { echo "FAIL: $*" >&2; exit 1; }

# Pi legitimately creates these stores on first runtime construction. Accept
# only the proven empty, owner-only shape; links, hard links, foreign owners,
# loose modes, non-objects, and nonempty objects remain import failures.
assert_empty_owner_store() {
  local path=$1
  python3 - "$path" <<'PY'
import json
import os
from pathlib import Path
import stat
import sys

path = Path(sys.argv[1])
try:
    metadata = path.lstat()
except OSError as error:
    print(f"missing first-start store: {error}", file=sys.stderr)
    raise SystemExit(1)
if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
    print("first-start store is not a direct regular file", file=sys.stderr)
    raise SystemExit(1)
if metadata.st_nlink != 1:
    print("first-start store has redirected hard links", file=sys.stderr)
    raise SystemExit(1)
if metadata.st_uid != os.geteuid():
    print("first-start store is not owned by the current user", file=sys.stderr)
    raise SystemExit(1)
if stat.S_IMODE(metadata.st_mode) != 0o600:
    print("first-start store mode is not 0600", file=sys.stderr)
    raise SystemExit(1)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as error:
    print(f"first-start store is not valid JSON: {error}", file=sys.stderr)
    raise SystemExit(1)
if type(data) is not dict or data:
    print("first-start store is not exactly an empty JSON object", file=sys.stderr)
    raise SystemExit(1)
PY
}

assert_absent_store() {
  [[ ! -e $1 && ! -L $1 ]]
}

assert_no_session_state() {
  local path=$1
  [[ ! -L $path ]] || return 1
  [[ ! -e $path || -d $path ]] || return 1
  if [[ -d $path ]]; then
    [[ -z $(find "$path" \( -type f -o -type l \) -print -quit) ]] || return 1
  fi
}

value_for() { awk -F': ' -v key="$1" '$1 == key {gsub(/"/, "", $2); print $2; exit}' "$ROOT/group_vars/all.yml"; }
list_version() { awk -v name="$1@" 'index($0, "- \"" name) {line=$0; sub(/^.*@/, "", line); sub(/".*$/, "", line); print line; exit}' "$ROOT/group_vars/all.yml"; }
version_ge() {
  awk -v actual="$1" -v floor="$2" 'BEGIN {
    split(actual,a,"."); split(floor,f,".");
    for(i=1;i<=3;i++){a[i]+=0;f[i]+=0;if(a[i]>f[i])exit 0;if(a[i]<f[i])exit 1} exit 0
  }'
}

no_mistakes_floor=$(awk -F= '$1 == "NO_MISTAKES_MIN" {print $2; exit}' "$SOURCE/bin/fm-bootstrap.sh")
gh_axi_floor=$(awk -F= '$1 == "GH_AXI_MIN" {print $2; exit}' "$SOURCE/bin/fm-bootstrap.sh")
lavish_floor=$(awk -F= '$1 == "LAVISH_AXI_MIN" {print $2; exit}' "$SOURCE/bin/fm-bootstrap.sh")
herdr_pin=$(awk -F= '$1 == "FM_HERDR_CI_VERSION" {print $2; exit}' "$SOURCE/bin/fm-install-herdr.sh")
treehouse_pin=$(awk -F= '$1 == "FM_TREEHOUSE_CI_VERSION" {print $2; exit}' "$SOURCE/bin/fm-install-treehouse.sh")
version_ge "$(value_for no_mistakes_version)" "$no_mistakes_floor" || { echo "no-mistakes pin is below Firstmate floor" >&2; exit 1; }
version_ge "$(list_version gh-axi)" "$gh_axi_floor" || { echo "gh-axi pin is below Firstmate floor" >&2; exit 1; }
version_ge "$(list_version lavish-axi)" "$lavish_floor" || { echo "lavish-axi pin is below Firstmate floor" >&2; exit 1; }
[[ $(value_for herdr_version) == "$herdr_pin" ]] || { echo "Herdr pin differs from Firstmate's installer" >&2; exit 1; }
[[ $(value_for treehouse_version) == "$treehouse_pin" ]] || { echo "Treehouse pin differs from Firstmate's installer" >&2; exit 1; }
[[ $(pi --version) == "$(value_for pi_version)" ]] || { echo "installed Pi does not match reviewed compatibility pin" >&2; exit 1; }

PI_BIN=$(command -v pi)
[[ $PI_BIN == /* ]] || fail "Pi executable did not resolve to an absolute path"
SANITIZED_PATH=$(dirname "$PI_BIN"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
TMP=$(mktemp -d "${TMPDIR:-/tmp}/bibi-firstmate-pin.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# A synthetic default Pi home carries fake state as a negative control. The
# explicit PI_CODING_AGENT_DIR cases must neither import nor alter it.
DECOY="$TMP/home/.pi/agent"
mkdir -p "$DECOY"
chmod 0700 "$TMP/home" "$TMP/home/.pi" "$DECOY"
python3 - "$DECOY" "$SOURCE" <<'PY'
import json
import os
from pathlib import Path
import sys

agent = Path(sys.argv[1])
source = sys.argv[2]
(agent / "auth.json").write_text(json.dumps({
    "anthropic": {"type": "api_key", "key": "test-only-placeholder"}
}), encoding="utf-8")
(agent / "trust.json").write_text(json.dumps({source: True}), encoding="utf-8")
for name in ("auth.json", "trust.json"):
    os.chmod(agent / name, 0o600)
PY
fingerprint_decoy() {
  python3 - "$DECOY" <<'PY'
import hashlib
from pathlib import Path
import sys

for path in sorted(Path(sys.argv[1]).iterdir()):
    metadata = path.lstat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(path.name, digest, metadata.st_uid, metadata.st_gid, metadata.st_mode)
PY
}
DECOY_BEFORE=$(fingerprint_decoy)

prepare_case() {
  local case_root=$1
  mkdir -p "$case_root/pi" "$case_root/fm/config" "$case_root/fm/state" \
    "$case_root/tmp" "$case_root/cache" "$case_root/config" "$case_root/data"
  chmod 0700 "$case_root/pi" "$case_root/fm" "$case_root/fm/config" \
    "$case_root/fm/state" "$case_root/tmp" "$case_root/cache" \
    "$case_root/config" "$case_root/data"
  assert_absent_store "$case_root/pi/auth.json" || fail "pre-launch auth state exists"
  assert_absent_store "$case_root/pi/models-store.json" || fail "pre-launch model store exists"
  assert_absent_store "$case_root/pi/trust.json" || fail "pre-launch trust state exists"
  assert_no_session_state "$case_root/pi/sessions" || fail "pre-launch session state exists"
}

run_case() {
  local case_root=$1 approval=$2
  if ! (
    cd "$SOURCE"
    env -i \
      HOME="$TMP/home" USER=synthetic LOGNAME=synthetic SHELL=/bin/bash \
      LC_ALL=C TERM=dumb NO_COLOR=1 PATH="$SANITIZED_PATH" \
      TMPDIR="$case_root/tmp" XDG_CACHE_HOME="$case_root/cache" \
      XDG_CONFIG_HOME="$case_root/config" XDG_DATA_HOME="$case_root/data" \
      PI_CODING_AGENT_DIR="$case_root/pi" \
      PI_CODING_AGENT_SESSION_DIR="$case_root/pi/sessions" \
      PI_OFFLINE=1 PI_SKIP_VERSION_CHECK=1 \
      FM_HOME="$case_root/fm" FM_ROOT_OVERRIDE="$SOURCE" \
      FM_STATE_OVERRIDE="$case_root/fm/state" \
      FM_CONFIG_OVERRIDE="$case_root/fm/config" \
      "$PI_BIN" --offline "$approval" --no-session --list-models
  ) >"$case_root/models.out" 2>"$case_root/load.err"; then
    cat "$case_root/load.err" >&2
    return 1
  fi
  [[ ! -s $case_root/load.err ]] || { cat "$case_root/load.err" >&2; return 1; }
  assert_empty_owner_store "$case_root/pi/auth.json" || return 1
  assert_empty_owner_store "$case_root/pi/models-store.json" || return 1
  assert_absent_store "$case_root/pi/trust.json" || return 1
  assert_no_session_state "$case_root/pi/sessions" || return 1
  grep -q '^No models available\.' "$case_root/models.out" || return 1
  ! grep -q 'anthropic' "$case_root/models.out" || return 1
}

NEGATIVE="$TMP/negative"
POSITIVE="$TMP/positive"
prepare_case "$NEGATIVE"
prepare_case "$POSITIVE"
run_case "$NEGATIVE" --no-approve || fail "isolated negative Pi load failed"
[[ -z $(find "$NEGATIVE/fm/state" -type f -print -quit) ]] \
  || fail "synthetic saved trust was accepted by the negative control"
run_case "$POSITIVE" --approve || fail "isolated approved Pi resource load failed"
[[ -f $POSITIVE/fm/state/.pi-watch-extension-loaded ]] \
  || fail "approved compatibility load did not activate Firstmate Pi resources"
[[ -f $POSITIVE/fm/state/.pi-turnend-extension-loaded ]] \
  || fail "approved compatibility load omitted the Firstmate turn-end resource"
[[ $(fingerprint_decoy) == "$DECOY_BEFORE" ]] \
  || fail "synthetic default-home auth or trust decoy changed"

# Prove the shape guard rejects each unsafe state rather than merely accepting
# Pi's happy path. Owner rejection runs when the test process can create a
# foreign-owned fixture.
REJECTIONS="$TMP/rejections"
mkdir -p "$REJECTIONS"
printf '%s\n' '{}' > "$REJECTIONS/valid.json"
chmod 0600 "$REJECTIONS/valid.json"
assert_empty_owner_store "$REJECTIONS/valid.json" || fail "valid empty store fixture was rejected"
printf '%s\n' '{"synthetic-provider":{}}' > "$REJECTIONS/nonempty.json"
chmod 0600 "$REJECTIONS/nonempty.json"
if assert_empty_owner_store "$REJECTIONS/nonempty.json" >/dev/null 2>&1; then
  fail "nonempty auth store passed the empty-store guard"
fi
cp "$REJECTIONS/valid.json" "$REJECTIONS/wrong-mode.json"
chmod 0644 "$REJECTIONS/wrong-mode.json"
if assert_empty_owner_store "$REJECTIONS/wrong-mode.json" >/dev/null 2>&1; then
  fail "wrong-mode auth store passed the owner-only guard"
fi
ln -s "$REJECTIONS/valid.json" "$REJECTIONS/auth-link.json"
if assert_empty_owner_store "$REJECTIONS/auth-link.json" >/dev/null 2>&1; then
  fail "symlinked auth store passed the direct-file guard"
fi
ln -s "$REJECTIONS/valid.json" "$REJECTIONS/models-link.json"
if assert_empty_owner_store "$REJECTIONS/models-link.json" >/dev/null 2>&1; then
  fail "symlinked model store passed the direct-file guard"
fi
cp "$REJECTIONS/valid.json" "$REJECTIONS/hard-target.json"
ln "$REJECTIONS/hard-target.json" "$REJECTIONS/hard-link.json"
if assert_empty_owner_store "$REJECTIONS/hard-link.json" >/dev/null 2>&1; then
  fail "hard-linked auth store passed the direct-file guard"
fi
printf '%s\n' '{"synthetic-project":true}' > "$REJECTIONS/trust.json"
chmod 0600 "$REJECTIONS/trust.json"
if assert_absent_store "$REJECTIONS/trust.json"; then
  fail "saved trust passed the absence guard"
fi
ln -s "$REJECTIONS/missing-trust-target" "$REJECTIONS/trust-link.json"
if assert_absent_store "$REJECTIONS/trust-link.json"; then
  fail "redirected trust store passed the absence guard"
fi
mkdir "$REJECTIONS/session-target"
ln -s "$REJECTIONS/session-target" "$REJECTIONS/sessions-link"
if assert_no_session_state "$REJECTIONS/sessions-link"; then
  fail "redirected session store passed the no-session guard"
fi
if [[ $(id -u) -eq 0 ]]; then
  cp "$REJECTIONS/valid.json" "$REJECTIONS/wrong-owner.json"
  chown 1 "$REJECTIONS/wrong-owner.json"
  foreign_owner_path="$REJECTIONS/wrong-owner.json"
else
  foreign_owner_path=/etc/passwd
fi
if assert_empty_owner_store "$foreign_owner_path" >"$REJECTIONS/owner.out" 2>"$REJECTIONS/owner.err"; then
  fail "foreign-owned auth store passed the current-owner guard"
fi
grep -q 'not owned by the current user' "$REJECTIONS/owner.err" \
  || fail "foreign-owner fixture did not exercise the ownership rejection"

echo "PASS Firstmate pin floors and isolated Pi resource load"
