# Local developer ergonomics for bibi-rules-the-world.
#
#   make lint                 run every check the CI workflow runs
#   make render REPO=<url>    render cloud-init user-data for DigitalOcean

SHELL := bash
YAMLLINT_CONFIG := {extends: default, rules: {line-length: {max: 160}, document-start: disable, comments: disable}}

.PHONY: lint yaml ansible shell python portable test render

lint: yaml ansible shell python test

yaml:
	yamllint -d '$(YAMLLINT_CONFIG)' hey-cli.yml tasks/install-mise.yml tasks/install-hey-cli.yml tests/fixtures/hey-cli-install.yml
	yamllint -d '$(YAMLLINT_CONFIG)' cloud-init.yaml group_vars/all.yml site.yml shared-clojure-toolchain.yml tasks/install-axi-tools.yml tasks/install-doctl.yml tasks/install-cloudflare-skills.yml tasks/install-shared-clojure-toolchain.yml tasks/install-memory-guard.yml tasks/install-swap-safety-net.yml tests/fixtures/axi-tools-install.yml tests/fixtures/doctl-install.yml tests/fixtures/cloudflare-skills-install.yml tests/fixtures/shared-clojure-toolchain-install.yml tests/fixtures/memory-guard-install.yml tests/fixtures/swap-safety-net-install.yml

ansible:
	ansible-lint hey-cli.yml tests/fixtures/hey-cli-install.yml
	ansible-playbook --syntax-check -i 'localhost,' hey-cli.yml
	ansible-playbook --syntax-check -i 'localhost,' tests/fixtures/hey-cli-install.yml
	ansible-lint site.yml shared-clojure-toolchain.yml tests/fixtures/axi-tools-install.yml tests/fixtures/doctl-install.yml tests/fixtures/cloudflare-skills-install.yml tests/fixtures/shared-clojure-toolchain-install.yml tests/fixtures/memory-guard-install.yml tests/fixtures/swap-safety-net-install.yml
	ansible-playbook --syntax-check -i 'localhost,' site.yml
	ansible-playbook --syntax-check -i 'localhost,' shared-clojure-toolchain.yml
	ansible-playbook --syntax-check -i 'localhost,' tests/fixtures/axi-tools-install.yml
	ansible-playbook --syntax-check -i 'localhost,' tests/fixtures/doctl-install.yml
	ansible-playbook --syntax-check -i 'localhost,' tests/fixtures/cloudflare-skills-install.yml
	ansible-playbook --syntax-check -i 'localhost,' tests/fixtures/shared-clojure-toolchain-install.yml
	ansible-playbook --syntax-check -i 'localhost,' tests/fixtures/memory-guard-install.yml
	ansible-playbook --syntax-check -i 'localhost,' tests/fixtures/swap-safety-net-install.yml

shell:
	bash -n bin/bibi-setup scripts/render-cloud-init.sh scripts/bibi-admin-update.sh scripts/test-provisioning.sh scripts/setup-common.sh scripts/setup-macos.sh scripts/setup-ubuntu.sh scripts/verify.sh scripts/verify-common.sh scripts/verify-linux.sh scripts/verify-macos.sh tests/test-portable-setup.sh tests/test-firstmate-pin.sh tests/test-herdr-pi-lifecycle.sh
	shellcheck bin/bibi-setup scripts/render-cloud-init.sh scripts/bibi-admin-update.sh scripts/test-provisioning.sh scripts/setup-common.sh scripts/setup-macos.sh scripts/setup-ubuntu.sh scripts/verify.sh scripts/verify-common.sh scripts/verify-linux.sh scripts/verify-macos.sh tests/test-portable-setup.sh tests/test-firstmate-pin.sh tests/test-herdr-pi-lifecycle.sh
	shellcheck templates/bibi-launcher.j2 templates/bibi-machine-update.j2 templates/bibi-machine-update-command.j2 templates/bibi-motd.j2 templates/bibi-pi-extensions-update.j2 templates/bibi-pi-public-packages-update.j2 templates/bibi-private-capabilities-update.j2

python:
	python3 -m py_compile scripts/bibi_record_update.py scripts/bibi_memory_guard.py tests/test_memory_guard.py tests/test_launch_admission.py
	python3 -m unittest discover --start-directory tests --pattern 'test_*.py'

portable:
	./tests/test-portable-setup.sh
	./tests/test-firstmate-pin.sh
	./tests/test-herdr-pi-lifecycle.sh

test: portable
	./scripts/test-provisioning.sh

render:
ifndef REPO
	$(error Usage: make render REPO=https://github.com/YOU/bibi-rules-the-world.git [REF=main])
endif
	./scripts/render-cloud-init.sh "$(REPO)" $(REF) > cloud-init.rendered.yaml
	@echo "Wrote cloud-init.rendered.yaml"
