#!/usr/bin/env bash
# Run Bibi's recorded full machine update from the administrator's own computer:
#
#   bash bibi-admin-update.sh
#
# This saves the README's administrator command as a file, so it never has to be
# copied out of a terminal. It needs the `bibi-admin` SSH alias and asks for that
# account's sudo password on the server. It does nothing until you run it.
set -euo pipefail

if (($#)); then
  echo "usage: bash bibi-admin-update.sh (takes no arguments)" >&2
  exit 2
fi

# Once a full update has installed the recorded wrapper, run it. Until then,
# record the old wrapper through the reviewed recorder at its pinned commit and
# SHA-256, exactly as the README's first-rerun bootstrap does; repeating that
# bootstrap after the wrapper exists would nest one record inside another.
# shellcheck disable=SC2016 # Both expand on the server, not here.
installed='if grep -qs -- bibi-record-update /usr/local/sbin/bibi-machine-update; then exec /usr/local/sbin/bibi-machine-update; fi'
# shellcheck disable=SC2016
bootstrap='set -euo pipefail; umask 077; d=$(mktemp -d /root/bibi-update-bootstrap.XXXXXX); curl --fail --silent --show-error --proto "=https" --tlsv1.2 https://raw.githubusercontent.com/brancusi/bibi-rules-the-world/add07d95b2c81e1ee482777aa7ca4f890b1a41aa/scripts/bibi_record_update.py -o "$d/recorder.py"; printf "%s  %s\n" 57bd265d62c28e63dbe3957b1ab6d8d28fa50b799272c197b39c4e1eb67b35e3 "$d/recorder.py" | sha256sum --check --status; exec /usr/bin/python3 "$d/recorder.py" -- /usr/local/sbin/bibi-machine-update'

# The update's own exit status is this script's exit status.
exec ssh -t bibi-admin "sudo bash -c $(printf '%q' "$installed; $bootstrap")"
