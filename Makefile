# Usage: make deploy            apply site.yml to the VM
#        make diff              dry run, show what would change
#        make run JOB=<name>    start a scraper job now
#        make logs JOB=<name>   last 200 log lines of a job
#        make status            list timers / last runs
# Pass extra ansible flags with EXTRA, e.g. EXTRA="-K --limit scraper-vm".
VAULT := $(if $(wildcard .vault-pass),--vault-password-file .vault-pass,--ask-vault-pass)
EXTRA ?=
AP := ansible-playbook site.yml $(VAULT) $(EXTRA)
AH := ansible research_vm -b $(VAULT) $(EXTRA)

.PHONY: deps lint test diff deploy run logs status check-vault

deps:
	pip install -r requirements-dev.txt
	ansible-galaxy collection install -r requirements.yml
	pre-commit install

lint: check-vault
	yamllint .
	ansible-lint

test:
	molecule test

diff:
	$(AP) --check --diff

deploy:
	$(AP) --diff

run:
	@test -n "$(JOB)" || (echo "JOB=<name> required" && exit 1)
	$(AH) -m ansible.builtin.systemd_service -a "name=scraper@$(JOB).service state=started no_block=true"

logs:
	@test -n "$(JOB)" || (echo "JOB=<name> required" && exit 1)
	$(AH) -m ansible.builtin.command -a "journalctl -u scraper@$(JOB).service -n 200 --no-pager"

status:
	$(AH) -m ansible.builtin.command -a "systemctl list-timers scraper-job-* vm-heartbeat.timer --all --no-pager"

check-vault:
	@for f in $$(find inventories -name vault.yml); do \
	  head -1 $$f | grep -q '^\$$ANSIBLE_VAULT' || { echo "$$f is not encrypted"; exit 1; }; \
	done
