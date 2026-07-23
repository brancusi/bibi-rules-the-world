#!/usr/bin/env bash
set -euo pipefail

failed=0
for command_name in node npm git gh jq pi herdr treehouse no-mistakes firecrawl \
  gh-axi chrome-devtools-axi lavish-axi tasks-axi quota-axi; do
  if command -v "$command_name" >/dev/null 2>&1; then
    printf 'ok       %s\n' "$command_name"
  else
    printf 'missing  %s\n' "$command_name" >&2
    failed=1
  fi
done

printf '\nVersions\n'
node --version || true
npm --version || true
pi --version || true
herdr --version || true
treehouse --version || true
no-mistakes --version || true
if command -v firecrawl >/dev/null 2>&1; then
  firecrawl --version 2>/dev/null || firecrawl --help 2>/dev/null | head -n 1 || true
else
  echo "firecrawl not installed"
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

