# Local developer ergonomics for bibi-rules-the-world.
#
#   make lint                 run every check the CI workflow runs
#   make render REPO=<url>    render cloud-init user-data for DigitalOcean

SHELL := bash
YAMLLINT_CONFIG := {extends: default, rules: {line-length: {max: 160}, document-start: disable, comments: disable}}

.PHONY: lint yaml ansible shell render

lint: yaml ansible shell

yaml:
	yamllint -d '$(YAMLLINT_CONFIG)' cloud-init.yaml group_vars/all.yml site.yml

ansible:
	ansible-lint site.yml
	ansible-playbook --syntax-check -i 'localhost,' site.yml

shell:
	bash -n scripts/render-cloud-init.sh scripts/verify.sh
	shellcheck scripts/render-cloud-init.sh scripts/verify.sh
	shellcheck templates/bibi-launcher.j2 templates/bibi-machine-update.j2 templates/bibi-motd.j2

render:
ifndef REPO
	$(error Usage: make render REPO=https://github.com/YOU/bibi-rules-the-world.git [REF=main])
endif
	./scripts/render-cloud-init.sh "$(REPO)" $(REF) > cloud-init.rendered.yaml
	@echo "Wrote cloud-init.rendered.yaml"
