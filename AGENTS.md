# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Run the complete local gate with `make lint`; CI mirrors its YAML, Ansible, shell, cloud-init, architecture, mismatch, and idempotency checks.
- `group_vars/all.yml` is authoritative for reviewed tool/package pins and checksums. Keep public system reconciliation in `site.yml`; the private Pi collection boundary and daily-user flow are documented in `README.md`.
- Reuse `tasks/install-doctl.yml` and its hermetic harness rather than duplicating doctl installation logic.
- `scripts/bibi_memory_guard.py` may only kill a process tree it can prove is orphaned. Name matching is protective-only, ambiguity refuses, and memory pressure never authorises a kill; `tests/test_memory_guard.py` and the README's "Memory safety net" section hold the full policy. Change either only together.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
