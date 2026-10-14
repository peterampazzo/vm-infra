# Running vm-infra

Nothing is done on the VM by hand. There are three ways to apply this repo, and all of them run the same `site.yml`:

* **From your laptop** over SSH (`make diff` / `make deploy`). Needed for the first run, and it always works as a fallback.
* **Auto-deploy (optional, step 4):** the VM runs `ansible-pull` every hour. It fetches `main` from GitHub and applies it,
  so merging a PR deploys it. Nothing connects in, and fork PRs or unmerged branches never reach the VM.
* **GitHub Actions buttons (optional, step 5):** a runner on the VM gives "Run workflow" buttons (status, diff, deploy,
  run, logs), so nobody needs a terminal. Read the security notes in step 5 before enabling it.

GitHub CI (lint + tests) runs on GitHub's own machines and never touches the VM. The repo is public, so real secrets
only live in the encrypted `vault.yml`. Anyone can download that file, so use a long random vault password
(e.g. `openssl rand -base64 32`).

## 0. What you need

* A VM running Ubuntu 22.04/24.04 that you can SSH into with sudo, from a network the VM allows.
  Sizing: 1 vCPU / 2 GB RAM is enough (2 vCPU / 4 GB is comfortable). For disk, aim for 20 GB or more. Docker needs about 1–2 GB
  under `docker_data_root` (default `/var/lib/docker`), so if `/var` is a small separate partition, point it at a bigger one.
* An SMB share for the data: path, username and password.
* A [healthchecks.io](https://healthchecks.io) project. Copy its API key (Settings > API Access > read-write key) and add
  your alert channels (email, Slack, ...).
* On your laptop: `git`, Python 3.11+, `make`, and an SSH key. `make deps` installs Ansible.
* On the VM: **nothing to install.** Ansible is agentless. It logs in over SSH and installs everything (Docker, SMB client,
  scraper, timers, monitoring). The VM only needs SSH access, `sudo` and Python 3, and stock Ubuntu ships all three. No git on the VM.

## 1. Look at the VM (once, about 5 minutes, read-only)

The only time you log in by hand. Don't install or change anything; just collect the values the config needs.

```bash
ssh <you>@<vm>

sudo -v && echo "sudo OK"          # if it asks for a password, add EXTRA=-K to make commands later
lsb_release -ds; python3 --version  # Ubuntu 22.04/24.04, Python 3.8+

# Provider's admin account (keep it!)  -> guardrails_protected_users
getent passwd | awk -F: '$3>=1000 && $3<65000 {print $1}'
# Security agent(s) the provider runs  -> guardrails_protected_services
systemctl list-units --type=service --state=running --no-pager | grep -iE 'nessus|tenable|qualys|falcon|crowdstrike|sentinel|defender|wazuh'
# Existing SSH allow-list (we only append to it)
sudo grep -ri '^AllowUsers' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ 2>/dev/null
# Firewall must be active (one of these)
sudo ufw status; sudo firewall-cmd --state; systemctl is-active nftables

# Disk: if /var is a separate partition under ~5 GB, set docker_data_root to a path on a bigger one
df -h / /var
# Outbound access the setup needs
curl -sSI https://github.com -o /dev/null && echo "github OK"
curl -sSI https://hc-ping.com -o /dev/null && echo "healthchecks OK"
timeout 3 bash -c '</dev/tcp/<smb-server>/445' && echo "SMB OK"   # <smb-server> = host part of your share path

exit
```

If any check fails (no sudo, no outbound HTTPS, SMB unreachable), ask the VM provider before going further.

## 2. Configure (once, on your laptop)

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

## 3. First run (from your laptop)

```bash
ssh <user>@<vm> true                 # check SSH works
make diff EXTRA=-K                   # dry run (-K asks for your sudo password); the first dry run on a fresh VM may stop early, that's expected
make deploy EXTRA=-K                 # apply
make status                          # timers and next run times
make run JOB=example-apple           # optional: run a job now
make logs JOB=example-apple
```

After this you should have green checks in healthchecks.io: one heartbeat and one check per job, which turns green after the job's first run.

## 4. Auto-deploy from `main` (optional)

1. **Settings > Branches**: add a rule for `main` that requires a pull request and the `lint` and `molecule` checks.
   With auto-deploy, whoever can push to `main` (only you) can change the VM.
2. In `group_vars/research_vm/main.yml`, set `autodeploy_enabled: true` (`autodeploy_repo_url` already points at this repo).
   Commit and merge.
3. Run `make deploy EXTRA=-K` once from your laptop. This installs Ansible in a venv on the VM, copies your
   `.vault-pass` to `/etc/vm-infra/vault-pass` (root-only), and starts `vm-infra-pull.timer`.

From then on the VM fetches `main` every hour (`autodeploy_interval_min`) and applies it. It runs even when nothing changed,
so a failed run is retried and manual changes on the VM are undone. The healthchecks.io check `autodeploy@<vm>` gets each
run's exit code and the last 10 KB of output.

* Changes reach the VM through `main` only. Preview them with `make diff` from your branch (or the `diff` button, step 5), then merge.
* A laptop `make deploy` of an unmerged branch is undone at the next hourly run.
* `make pull` applies `main` now; `make pull-logs` shows the output of the last runs.
* Changing the vault password (`ansible-vault rekey`): update `.vault-pass`, then run `make deploy` from your laptop
  so the VM gets the new one. Until then auto-deploy fails (and alerts).

## 5. GitHub Actions buttons (optional)

A runner on the VM gives **Actions > VM > Run workflow** buttons. It only makes outbound HTTPS connections.

> **Security (public repo).** The runner can become root on the VM. `deploy.yml` only has a `workflow_dispatch` trigger,
> which needs write access, but anyone can open a fork PR that adds its own workflow targeting the runner. The only thing
> that stops it is GitHub's fork approval setting, so:
> * set **Require approval for all external contributors** (item 1 below). The "first-time contributor" levels stop asking
>   once someone has had any PR merged;
> * **never click "Approve and run" on a fork PR.** You're the only writer: if a PR is useful, read it, close it, and
>   re-apply the change on your own branch;
> * don't add `pull_request_target` workflows (they skip approval). `make lint` fails if any workflow uses it, or if a
>   workflow with a self-hosted job has any trigger other than `workflow_dispatch`. That check only catches our own
>   mistakes; a fork can edit it.
>
> GitHub recommends self-hosted runners only for private repos. If you don't need the buttons, skip this step:
> auto-deploy (step 4) doesn't need a runner.

1. **Settings > Actions > General > Approval for running fork pull request workflows from contributors**:
   choose **Require approval for all external contributors**.
2. **Settings > Environments > New environment** named `production`. Add yourself as a **required reviewer**, so every
   `deploy` waits for your approval.
3. **Settings > Secrets and variables > Actions > New repository secret**: `ANSIBLE_VAULT_PASSWORD` = the vault password.
4. Branch protection for `main` as in step 4.1.
5. In `group_vars/research_vm/main.yml`, set `gha_runner_enabled: true` and commit.
6. **Settings > Actions > Runners > New self-hosted runner**: copy the token from the `./config.sh --token XXXX` line
   (it expires after 1 hour), then run:
   ```bash
   make deploy EXTRA="-K -e gha_runner_token=XXXX"
   ```
   The runner should show as *Idle* with the label `research-vm`.
7. **Settings > Secrets and variables > Actions > Variables**: create `VM_RUNNER` = `true`. Until then every job is skipped.

| Button (`action`) | What it does |
|---|---|
| `status` | Timers and last/next runs. |
| `diff` | Dry run of the branch picked in "Use workflow from": a safe preview before merging. Output in the run summary. |
| `deploy` | Applies `main` (refuses other branches), after your approval in the `production` environment. |
| `run` / `logs` | Start a job now / last 200 log lines (needs `job`). |

`deploy` and `diff` share a lock with auto-deploy, so they never run at the same time.

## Common changes

| Change | Edit | Then |
|---|---|---|
| Add/remove a scraping job | `scraper_jobs`, `files/apps/*.json` | PR + `make diff`, then merge (auto-deploy) or `make deploy` |
| New scraper version | `scraper_git_ref` (commit SHA) | PR + `make diff`, then merge (auto-deploy) or `make deploy` |
| Rotate share password | `ansible-vault edit .../vault.yml` | PR + `make diff`, then merge (auto-deploy) or `make deploy` |
| Add an admin | `base_ssh_allow_users`, `base_ssh_authorized_keys` | PR + `make diff`, then merge (auto-deploy) or `make deploy` |

## Rebuilding the VM

1. Get a new VM and update `ansible_host` in `inventories/production/hosts.yml`.
2. Run step 3 from your laptop. That also sets up auto-deploy if it's enabled. If you use the Actions buttons, run step 5.6
   with a new token and remove the old runner under Settings > Actions > Runners.

Data lives on the share, so there's nothing to restore.

## Troubleshooting

* `guardrails` fails: an account, agent or firewall the provider requires is missing. Fix that first. The play
  refuses to change anything until it's fixed.
* Heartbeat check red: its last ping body lists the problem (disk, share not writable, Docker down, failed units).
* Job check red: the ping body has the last 10 KB of job output. Run `make logs JOB=<name>` for more.
* `autodeploy` check red: the ping body has the end of the `ansible-pull` output. Run `make pull-logs` for more.
* Runner offline: `ansible research_vm -b -a "systemctl status actions-runner --no-pager"`.
