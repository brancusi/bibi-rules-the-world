# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Run the complete local gate with `make lint`; CI mirrors its YAML, Ansible, shell, cloud-init, architecture, mismatch, and idempotency checks.
- `group_vars/all.yml` is authoritative for reviewed tool/package pins and checksums. Keep public system reconciliation in `site.yml`; the private Pi collection boundary and daily-user flow are documented in `README.md`.
- Reuse `tasks/install-doctl.yml` and its hermetic harness rather than duplicating doctl installation logic.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
