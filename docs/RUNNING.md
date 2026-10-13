# Running vm-infra

There are two ways to apply this repo to the VM. Both run the same `site.yml`:

* **From your laptop** over SSH. You need this for the first run, and it stays useful as a fallback.
* **From GitHub Actions** through a runner installed on the VM. After setup, PRs show a dry run and merging to `main`
  deploys. Nothing has to run on your laptop.

## 0. What you need

* A VM running Ubuntu 22.04/24.04 that you can SSH into with sudo, from a network the VM allows.
  Sizing: 1 vCPU / 2 GB RAM is enough (2 vCPU / 4 GB is comfortable). For disk, aim for 20 GB or more. Docker needs about 1–2 GB
  under `docker_data_root` (default `/var/lib/docker`), so if `/var` is a small separate partition, point it at a bigger one.
* An SMB share for the data: path, username and password.
* A [healthchecks.io](https://healthchecks.io) project. Copy its API key (Settings > API Access > read-write key) and add
  your alert channels (email, Slack, ...).
* On your laptop: Python 3.11+, `make`, and an SSH key.

## 1. Configure (once)

```bash
git clone git@github.com:peterampazzo/vm-infra.git && cd vm-infra
python3 -m venv .venv && . .venv/bin/activate
make deps

# Secrets (stay encrypted in git)
cp inventories/production/group_vars/research_vm/vault.yml.example inventories/production/group_vars/research_vm/vault.yml
$EDITOR inventories/production/group_vars/research_vm/vault.yml
ansible-vault encrypt inventories/production/group_vars/research_vm/vault.yml
echo 'the-vault-password' > .vault-pass && chmod 600 .vault-pass   # git-ignored; keep a copy in your password manager
```

Then edit these two files:

* `inventories/production/hosts.yml`: `ansible_host` (the VM's hostname or IP) and `ansible_user` (your SSH user).
* `inventories/production/group_vars/research_vm/main.yml`:
  * `guardrails_protected_users` / `guardrails_protected_services`: the provider's admin account and agents that must never
    be removed. Check with `getent passwd` and `systemctl list-units --type=service` on the VM.
  * `base_ssh_allow_users`, `base_ssh_authorized_keys`: who may SSH in.
  * `storage_smb_share` (and `storage_smb_domain` if needed).
  * `scraper_jobs`: what to scrape and when. App lists go in `files/apps/*.json`.

Commit and push. `vault.yml` is encrypted, and the pre-commit hook refuses to commit it unencrypted.

## 2. First run (from your laptop)

```bash
ssh <user>@<vm> true                 # check SSH works
make diff EXTRA=-K                   # dry run (-K asks for your sudo password); the first dry run on a fresh VM may stop early, that's expected
make deploy EXTRA=-K                 # apply
make status                          # timers and next run times
make run JOB=example-apple           # optional: run a job now
make logs JOB=example-apple
```

After this you should have green checks in healthchecks.io: one heartbeat and one check per job, which turns green after the job's first run.

## 3. Deploy from GitHub Actions (optional, once)

This puts a GitHub Actions runner on the VM. It only makes outbound HTTPS connections, so no inbound access is needed.

> **Security:** anyone who can push to the repo or run its workflows effectively gets root on the VM. Keep the repo
> **private**, protect `main`, and only add collaborators you trust.

1. On GitHub, open **Settings > Environments > New environment** and name it `production`. Add yourself as a **required reviewer**,
   so every deploy waits for your approval.
2. Open **Settings > Secrets and variables > Actions > New repository secret** and create `ANSIBLE_VAULT_PASSWORD` with the vault password as its value.
3. Open **Settings > Branches** and add a rule for `main` that requires a pull request and the `lint` and `molecule` checks.
4. In `group_vars/research_vm/main.yml`, set `gha_runner_enabled: true` and commit.
5. Open **Settings > Actions > Runners > New self-hosted runner** and copy the token from the `./config.sh --token XXXX` line.
   The token expires after 1 hour. Then run:
   ```bash
   make deploy EXTRA="-K -e gha_runner_token=XXXX"
   ```
   The runner should now show as *Idle* under Settings > Actions > Runners, with the label `research-vm`.
6. Under **Settings > Secrets and variables > Actions > Variables**, create the repository variable `VM_RUNNER` = `true`.
   Until it's set, the Deploy workflow skips every job instead of waiting for a runner that doesn't exist.

From then on:

| You do | What happens |
|---|---|
| Open a PR | `Deploy / plan` runs `make diff` on the VM. The diff is shown in the run summary. |
| Merge to `main` | `Deploy / deploy` waits for your approval, then runs `make deploy`. |
| **Actions > Deploy > Run workflow** | Choose `status`, `diff`, `deploy`, `run` or `logs` (plus a job name). No terminal needed. |

The laptop commands from step 2 keep working.

## Common changes

| Change | Edit | Then |
|---|---|---|
| Add/remove a scraping job | `scraper_jobs`, `files/apps/*.json` | PR, merge |
| New scraper version | `scraper_git_ref` (commit SHA) | PR, merge |
| Rotate share password | `ansible-vault edit .../vault.yml` | PR, merge |
| Add an admin | `base_ssh_allow_users`, `base_ssh_authorized_keys` | PR, merge |

## Rebuilding the VM

1. Get a new VM and update `ansible_host` in `inventories/production/hosts.yml`.
2. Run step 2 from your laptop. If you use Actions, also run step 3.5 with a new token, and remove the old runner under
   Settings > Actions > Runners.

Data lives on the share, so there's nothing to restore.

## Troubleshooting

* `guardrails` fails: an account, agent or firewall the provider requires is missing. Fix that first. The play
  refuses to change anything until it's fixed.
* Heartbeat check red: its last ping body lists the problem (disk, share not writable, Docker down, failed units).
* Job check red: the ping body has the last 10 KB of job output. Run `make logs JOB=<name>` for more.
* Runner offline: `ssh` in and check `systemctl status actions-runner`.
