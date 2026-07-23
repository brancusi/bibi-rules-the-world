#!/usr/bin/env bash
set -euo pipefail

repo_url=${1:-}
repo_ref=${2:-main}
root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [[ ! "$repo_url" =~ ^https://github\.com/[A-Za-z0-9_.-]+/bibi-rules-the-world(\.git)?$ ]]; then
  echo "Usage: $0 https://github.com/YOU/bibi-rules-the-world.git [git-ref]" >&2
  echo "The first-boot repository must be public and must not contain secrets." >&2
  exit 2
fi

if [[ ! "$repo_ref" =~ ^[A-Za-z0-9._/-]+$ ]] || [[ "$repo_ref" == *..* ]]; then
  echo "Unsafe git ref: $repo_ref" >&2
  exit 2
fi

sed \
  -e "s|__BIBI_CONFIG_REPO__|${repo_url}|g" \
  -e "s|__BIBI_CONFIG_REF__|${repo_ref}|g" \
  "$root_dir/cloud-init.yaml"

