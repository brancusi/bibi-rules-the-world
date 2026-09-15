#!/usr/bin/env bash
# Backward-compatible read-only verifier dispatcher.
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
case "$(uname -s)-$(uname -m)" in
  Linux-x86_64|Linux-aarch64|Linux-arm64) exec "$root/verify-linux.sh" "$@" ;;
  Darwin-arm64) exec "$root/verify-macos.sh" "$@" ;;
  *) printf 'bibi-verify: unsupported platform %s-%s\n' "$(uname -s)" "$(uname -m)" >&2; exit 1 ;;
esac
