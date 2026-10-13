# vm-infra

Ansible config for a small, provider-managed research VM that runs
[app-reviews-scraper](https://github.com/peterampazzo/app-reviews-scraper) in Docker. Output goes to an SMB/CIFS
research-data share, so the VM itself holds no state: to rebuild it, get a new VM and run one command.

## What it sets up

| Role | What it does |
|---|---|
| `guardrails` | Stops the play before any change if the provider's requirements aren't met: the admin account exists, the required security agent(s) are running, and a firewall is active. |
| `base` | Unattended security upgrades with auto-reboot (04:30), journald size caps, SSH keys. Admin users are **added to** the existing `AllowUsers` line, never replacing it. |
| `storage` | Mounts the SMB share at `/mnt/data` (systemd automount, credentials file 0600). |
| `docker` | Docker CE with `iptables: false` and no bridge network. Containers use host networking, so the provider's firewall is left untouched. Logs go to journald. |
| `scraper` | Builds the scraper image from a **pinned git commit**. Each job in `scraper_jobs` gets a systemd timer that runs `docker run --rm` and writes to the share. App lists in `files/apps/` are synced to the share. Jobs removed from config are removed from the VM. |
| `gha_runner` | Optional (`gha_runner_enabled`): a self-hosted GitHub Actions runner on the VM, so PRs get a dry-run diff and merges to `main` deploy (`.github/workflows/deploy.yml`). |
| `monitoring` | Uses the healthchecks.io API to create checks: one per job, plus a 5-minute host heartbeat (disk, share writable, Docker, failed units). Optionally installs Grafana Alloy to ship logs and metrics to Grafana Cloud. |

### Alerts

* **Job didn't run, failed, or hung**: each run pings `/start` and then its exit code, with the last 10 KB of logs attached. healthchecks.io alerts you if no successful run arrives within `timeout + grace`.
* **VM down, disk full, share mount broken (e.g. password changed), Docker down**: the heartbeat check fails or goes silent.
* Alert channels (email, Slack, Telegram, ...) are set up once in the healthchecks.io project. Every check is attached to all of them (`healthchecks_channels: "*"`).
* With Alloy enabled, Grafana Cloud also gets the full journal (`{unit="scraper@<job>.service"}`) and host metrics.

**Step-by-step guide, including deploying from GitHub Actions: [docs/RUNNING.md](docs/RUNNING.md).**

## First-time setup

1. Get a VM (Ubuntu 24.04) with SSH allowed from your network/VPN, and an SMB share for the data.
2. On your laptop:
   ```bash
   make deps
   cp inventories/production/group_vars/research_vm/vault.yml.example inventories/production/group_vars/research_vm/vault.yml
   $EDITOR inventories/production/group_vars/research_vm/vault.yml   # share creds, healthchecks API key
   ansible-vault encrypt inventories/production/group_vars/research_vm/vault.yml
   echo 'your-vault-password' > .vault-pass && chmod 600 .vault-pass   # git-ignored
   ```
3. Edit `inventories/production/hosts.yml` (host, username) and `group_vars/research_vm/main.yml` (protected account and agent, share path, jobs).
4. `make diff EXTRA=-K` to preview, then `make deploy EXTRA=-K`.

## Day-to-day

```bash
make diff                 # what would change (like terraform plan)
make deploy               # apply
make status               # timers, last/next runs
make run JOB=example-apple
make logs JOB=example-apple
```

* **Add or change a job**: edit `scraper_jobs` and/or `files/apps/*.json`, then `make deploy`.
* **Upgrade the scraper**: set `scraper_git_ref` to the new commit SHA, then `make deploy`. The image is rebuilt and old images are removed.
* **Restore or replace the VM**: get a new VM, update `ansible_host`, then `make deploy`. The data is on the share and nothing else needs restoring.

## Testing

`make lint` runs yamllint and ansible-lint. `make test` runs Molecule: an Ubuntu 24.04 systemd container with stand-ins for the provider admin account, security agent and ufw. It converges, checks idempotence, then runs a real Google Play scrape job end to end. CI runs both on every PR.
