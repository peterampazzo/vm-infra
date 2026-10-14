# Running vm-infra

Everything runs from your laptop: Ansible logs into the VM over SSH and applies `site.yml`. Nothing is done on the VM
by hand, and nothing deploys automatically. GitHub CI (lint + tests) runs on GitHub's own machines and never touches the VM,
so the repo can stay public as long as real secrets only live in the encrypted `vault.yml`. Anyone can download that file,
so use a long random vault password (e.g. `openssl rand -base64 32`).

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

## Common changes

| Change | Edit | Then |
|---|---|---|
| Add/remove a scraping job | `scraper_jobs`, `files/apps/*.json` | commit, `make diff`, `make deploy` |
| New scraper version | `scraper_git_ref` (commit SHA) | commit, `make diff`, `make deploy` |
| Rotate share password | `ansible-vault edit .../vault.yml` | commit, `make diff`, `make deploy` |
| Add an admin | `base_ssh_allow_users`, `base_ssh_authorized_keys` | commit, `make diff`, `make deploy` |

## Rebuilding the VM

1. Get a new VM and update `ansible_host` in `inventories/production/hosts.yml`.
2. Run step 3 from your laptop.

Data lives on the share, so there's nothing to restore.

## Troubleshooting

* `guardrails` fails: an account, agent or firewall the provider requires is missing. Fix that first. The play
  refuses to change anything until it's fixed.
* Heartbeat check red: its last ping body lists the problem (disk, share not writable, Docker down, failed units).
* Job check red: the ping body has the last 10 KB of job output. Run `make logs JOB=<name>` for more.
