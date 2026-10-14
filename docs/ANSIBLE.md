# How the Ansible setup works

New to Ansible? It's a tool that connects to a machine (over SSH, or locally) and brings it to the state this repo describes.
Each task describes a desired state ("this package installed", "this file has this content") rather than a command to
run. That makes running it twice safe: the second run reports `changed=0`. `make diff` shows what a run would change
before you apply it.

## Repo layout

```
site.yml                         the playbook: which roles run on which hosts, in order
ansible.cfg                      defaults (inventory path, roles path, output format)
Makefile                         shortcuts: make diff / deploy / run / logs / status / lint / test
inventories/production/
  hosts.yml                      the VM: hostname and SSH user
  group_vars/research_vm/
    main.yml                     YOUR settings (overrides role defaults)
    vault.yml                    secrets, encrypted with ansible-vault (from vault.yml.example)
roles/<role>/
  defaults/main.yml              every setting the role has, documented, with safe defaults
  tasks/main.yml                 what the role does
  templates/                     config files rendered with your settings (Jinja2)
  handlers/                      restarts that run only when a config file changed
files/apps/*.json                app lists to scrape (copied to the share)
molecule/default/                the test: a throwaway Ubuntu 24.04 container stands in for the VM
.github/workflows/ci.yml         lint + molecule on every PR (GitHub-hosted; never touches the VM)
.github/workflows/deploy.yml     "Run workflow" buttons on the VM's own runner (workflow_dispatch only)
scripts/check_workflows.py       part of `make lint`: no pull_request_target, self-hosted jobs only via workflow_dispatch
```

## Roles, in run order

| Role | Always runs? | Does | Key settings |
|---|---|---|---|
| `guardrails` | yes | Read-only checks. The play aborts before any change if the provider's admin account is missing, a required agent isn't running, or no firewall is active. | `guardrails_protected_users`, `guardrails_protected_services`, `guardrails_require_firewall` |
| `common` | yes | Creates the `scraper` service user (uid 1500) that jobs run as and that owns the output. | `service_user`, `service_uid`, `data_dir` |
| `base` | | Packages, timezone, unattended security upgrades with auto-reboot, journald caps, SSH keys, users appended to `AllowUsers`. | `base_ssh_allow_users`, `base_ssh_authorized_keys`, `base_auto_reboot_time`, `base_ssh_disable_password_auth` |
| `storage` | | Mounts the SMB share at `/mnt/data` (automount, so a share outage doesn't block boot). | `storage_smb_share`, `storage_smb_domain`, vault: `vault_smb_username/password` |
| `docker` | | Docker CE that doesn't touch the firewall (`iptables: false`, host networking only), logs to journald. | `docker_data_root` |
| `scraper` | | Builds the image from a pinned commit, syncs app lists, and creates one systemd timer per job. Jobs removed from config are removed from the VM. | `scraper_git_ref`, `scraper_jobs` |
| `monitoring` | | Creates the healthchecks.io checks, installs the heartbeat timer, and optionally Grafana Alloy. | vault: `vault_healthchecks_api_key`; `monitoring_disk_threshold_pct`, `monitoring_alloy_*` |
| `gha_runner` | only if `gha_runner_enabled` | GitHub Actions runner on the VM (user `gha-runner`, passwordless sudo). | `gha_runner_enabled`, `gha_runner_repo_url`, `-e gha_runner_token=...` once |
| `autodeploy` | only if `autodeploy_enabled` | Installs Ansible in a venv on the VM, copies the vault password (root-only), and a `vm-infra-pull.timer` that runs `ansible-pull` of `main`. | `autodeploy_enabled`, `autodeploy_repo_url`, `autodeploy_branch`, `autodeploy_interval_min` |

## Where settings come from

From lowest to highest priority:

1. `roles/<role>/defaults/main.yml`: every setting, with comments. Read these to see what you can change.
2. `inventories/production/group_vars/research_vm/main.yml` and `vault.yml`: your values. **Edit here, not in roles.**
3. `-e name=value` on the command line, e.g. `make deploy EXTRA="-e scraper_git_ref=<commit>"`. Use this for one-offs.

Secrets live only in `vault.yml` as `vault_*` variables. `main.yml` refers to them (`storage_smb_password: "{{ vault_smb_password }}"`),
so you can see what's used without decrypting anything. Edit secrets with
`ansible-vault edit inventories/production/group_vars/research_vm/vault.yml`.

## Scraper jobs

```yaml
scraper_jobs:
  - name: banking-apple          # [a-z0-9-]+ → scraper-job-banking-apple.timer, scraper@banking-apple.service
    list: banking.json           # a file in files/apps/
    store: apple                 # apple | google
    args: [--details, --reviews] # scraper flags, see the app-reviews-scraper README
    schedule: "Sun *-*-* 02:00:00"   # systemd OnCalendar; check with: systemd-analyze calendar "Sun *-*-* 02:00:00"
    healthcheck: {timeout: 604800, grace: 172800}   # optional: alert if no successful run within 7 + 2 days
```

Each run: the timer starts `scraper@<name>.service`. That pings healthchecks.io `/start`, runs the container as
`scraper` with `<data_dir>` mounted, and pings `/<exit code>` with the last 10 KB of output. Runs start up to
`scraper_randomized_delay` (10 min) late, so jobs don't all start at once.

To add a job: add an app list to `files/apps/`, add an entry to `scraper_jobs`, then `make diff` and `make deploy` (or merge to `main`, with auto-deploy). The healthchecks.io check is created automatically.

## Running only part of it

Every role has a tag:

```bash
make deploy EXTRA="--tags scraper"        # only the scraper role (guardrails + common always run)
make diff   EXTRA="--tags base,storage"
```

## What `make diff` can and can't show

`make diff` is Ansible's check mode. It shows file and template changes line by line, plus packages, users and services
that would change. It **can't** predict the result of commands it doesn't run: the image build, healthchecks.io API
calls, and anything that depends on a package that isn't installed yet. So on a fresh VM it may stop early or show
less than a real run would. On a configured VM it's reliable, and the test suite checks this on every PR. Diffs of files
containing secrets (vault values, ping URLs) are hidden.

## Testing changes

```bash
make lint   # yamllint + ansible-lint
make test   # molecule: create container → converge → re-run must change nothing → check mode → verify (real scrape) → destroy
```

The Molecule container fakes the provider's admin account, security agent and firewall, and has no SMB share, so it
uses a local data directory. It does build the real image and run a real Google Play scrape.
