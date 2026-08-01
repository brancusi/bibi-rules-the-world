# bibi-rules-the-world

> A remote laboratory for creative, exploratory, mind-bending agentic coding.
> We bend assumptions, not isolation boundaries.

This repository turns a clean DigitalOcean Ubuntu 24.04 Droplet into a
persistent FirstMate machine. Your Mac runs only WezTerm and OpenSSH. Node,
Pi, Herdr, FirstMate, source code, builds, and credentials stay on the Droplet.

## What this builds

- `bibi`: the unprivileged daily agent account; it cannot use `sudo`
- `bibi-admin`: a separate key-only maintenance account with `sudo`
- Pi as primary and crew harness
- Herdr as the persistent FirstMate backend
- FirstMate pinned to a reviewed Git commit
- checksum-pinned Herdr, Treehouse, no-mistakes, and official `doctl` binaries
- exact npm pins for Pi, Firecrawl CLI, and the required AXI tools
- a reviewed commit pin and daily-user installer for the private Pi Extensions collection
- SSH key authentication only, no root SSH, no agent forwarding
- the DigitalOcean bootstrap key is removed from root after it is copied to
  `bibi-admin` and `bibi`
- UFW, fail2ban, and unattended security updates
- GitHub Actions validation for YAML, Ansible, and cloud-init rendering

Herdr is an experimental FirstMate backend. That choice is explicit in
`firstmate/config/backend` and can be changed later.

## 1. Publish this repository

Create a GitHub repository named `bibi-rules-the-world` and push these files.
The simplest and safest first-boot path is a **public configuration repo with
no secrets**. A private repo would require placing a bootstrap credential in
DigitalOcean user-data, which this design intentionally refuses to do.

Before pushing, edit `group_vars/all.yml` and replace:

```yaml
bibi_config_repo_url: "https://github.com/YOUR_GITHUB_USERNAME/bibi-rules-the-world.git"
```

Do not add API keys, GitHub tokens, Pi credentials, private SSH keys, `.env`
files, or rendered cloud-init to this repository.

This delivered directory is already initialized as a Git repository. After
creating the empty public repository on GitHub, publish it with:

```bash
git remote add origin \
  https://github.com/YOUR_GITHUB_USERNAME/bibi-rules-the-world.git
git push -u origin main
```

## 2. Render DigitalOcean user-data

From this repository:

```bash
chmod +x scripts/*.sh
./scripts/render-cloud-init.sh \
  https://github.com/YOUR_GITHUB_USERNAME/bibi-rules-the-world.git \
  > cloud-init.rendered.yaml
```

Or, with make:

```bash
make render REPO=https://github.com/YOUR_GITHUB_USERNAME/bibi-rules-the-world.git
```

The rendered file is ignored by Git. Inspect it before using it:

```bash
less cloud-init.rendered.yaml
```

## 3. Create the DigitalOcean Droplet

In the DigitalOcean control panel:

1. Create a project for Bibi.
2. Add your Mac's **public** SSH key to DigitalOcean.
3. Create an Ubuntu 24.04 LTS Droplet.
4. Start with 8 dedicated vCPUs, 16 GB RAM, and at least 160 GB disk when you
   expect several concurrent agents. A 4 vCPU / 8 GB machine is a cheaper trial.
5. Pick the region that tests best from your location.
6. Enable monitoring and backups.
7. Open **Advanced Options → Add Initialization scripts** and paste the entire
   contents of `cloud-init.rendered.yaml`.
8. Create the Droplet.

Then create a DigitalOcean Cloud Firewall:

- inbound TCP 22 from your current public IP when practical
- otherwise inbound TCP 22 from all IPv4/IPv6, relying on key-only SSH
- no other inbound rules
- allow all outbound traffic

The playbook also enables UFW inside the VM. Both layers are intentional.

## 4. Wait for provisioning

Use DigitalOcean's web console to inspect progress if necessary:

```bash
cloud-init status --wait
sudo tail -n 200 /var/log/cloud-init-output.log
```

Provisioning downloads and verifies several tools and can take a few minutes.
The official DigitalOcean CLI is selected for the machine architecture, checked
against the reviewed release archive SHA-256, required to report the pinned
version, and installed as root-owned mode `0555` at `/usr/local/bin/doctl`.
Provisioning does **not** authenticate doctl or fetch the private Pi Extensions
repository. Do not interrupt provisioning midway.

## 5. Configure plain SSH for WezTerm

Add this to the Mac's `~/.ssh/config`:

```sshconfig
Host bibi
    HostName YOUR_DROPLET_IP
    User bibi
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ForwardAgent no
    ServerAliveInterval 30
    ServerAliveCountMax 3

Host bibi-admin
    HostName YOUR_DROPLET_IP
    User bibi-admin
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ForwardAgent no
```

Your daily entry point in WezTerm is simply:

```bash
ssh bibi
```

Before accepting the host key the first time, use DigitalOcean's web console
to compare the shown fingerprint with:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

## 6. Authenticate and install the private Pi package

Public machine provisioning ends before this section. Everything below runs as
the daily user after `ssh bibi`; none of it belongs in cloud-init or Ansible.
First authenticate GitHub, then install the reviewed private collection pin:

```bash
gh auth login
bibi-pi-extensions-update
firecrawl login --browser
firecrawl --status
pi
```

`bibi-pi-extensions-update` checks the GitHub CLI login, configures Git to use
GitHub CLI's credential helper, and invokes Pi's official Git package installer
against the exact reviewed commit over HTTPS. It disables terminal credential
prompts, never copies or forwards a GitHub token, and is safe to rerun to
reconcile the checkout. The private repository is not fetched until this step.

Inside Pi, use `/login` for your model provider. Then create and accept a named
DigitalOcean context through the extension's masked local UI:

```text
/digitalocean-login hey-coach
```

Enter a newly created least-privilege DigitalOcean API token only in that masked
prompt and confirm the displayed account/team identity. Never paste the token
into chat, a shell command, Pi settings, Git, or this repository. Provisioning
never runs `doctl auth`, creates a context, or contains a DigitalOcean/GitHub
token. The extension requires Node.js 22+, Pi 0.81.1-compatible
`pi-ai`, `coding-agent`, `pi-tui`, and `typebox` core peers, plus official
doctl 1.165.0+ in the 1.x series. Pi supplies those peers; this repo pins
compatible Pi 0.81.1 and doctl 1.166.0.

If you prefer non-interactive Firecrawl setup, set `FIRECRAWL_API_KEY` in the
remote VM environment instead of running `firecrawl login --browser`. Firecrawl
can use its keyless free tier for supported commands, but a logged-in API key is
preferred for usable limits. To disable Firecrawl telemetry, optionally add this
to the remote shell environment:

```bash
export FIRECRAWL_NO_TELEMETRY=1
```

Approve Pi's trust prompt the first time you launch it from `~/firstmate`; that
allows FirstMate's tracked Pi extensions to load. Credentials stay on the
remote VM. Do not forward your Mac's SSH agent.

## 7. Launch the persistent flight deck

After `ssh bibi`:

```bash
herdr
```

Inside Herdr, run:

```bash
bibi
```

`bibi` is a small launcher that enters `~/firstmate` and starts Pi. Herdr keeps
the PTY session alive when WezTerm closes or SSH disconnects. Reconnect with
`ssh bibi`, run `herdr`, and reattach.

## Verify the installation

As the daily user, verify command availability, the exact doctl version and
root ownership/mode, FirstMate configuration, sudo separation, and (when
installed) the private collection commit/package version:

```bash
bibi-verify
```

Before the interactive GitHub step, the private collection is reported as
`pending` without invalidating the public system build. After
`bibi-pi-extensions-update`, it must report the reviewed commit. A different or
unpinned collection ref is an error. The authoritative provisioned pins are:

```bash
cat /etc/bibi-provisioned-versions
```

## Update or reconcile the VM

1. Change pins or tasks in this repository. For doctl, review both declared
   architectures and replace both archive checksums. For Pi Extensions, review
   and replace `pi_extensions_ref`; never use a moving branch as the package pin.
2. Run `make lint` locally, review, and push the change.
3. After the reviewed change lands, enter through the maintenance identity and
   reconcile public/system state:

```bash
ssh bibi-admin
sudo /usr/local/sbin/bibi-machine-update
```

4. Return as `bibi`. If the private collection pin changed—or merely to repair
   its checkout—rerun the authentication-gated daily-user reconciliation:

```bash
bibi-pi-extensions-update
bibi-verify
```

The daily `bibi` account cannot run the machine update command, and the admin
reconciliation intentionally cannot fetch the private collection. Treat changes
to this repository as root-level changes and protect its default branch.

FirstMate also has its own `/updatefirstmate` workflow. If you use it, the live
checkout can move beyond this repo's pin; the next Ansible reconciliation may
return it to the configured commit. Prefer reviewing and bumping `firstmate_ref`
here so rebuilds remain deterministic.

## Forward a development port without exposing it

If a remote app listens on `127.0.0.1:3000`, open a second WezTerm tab:

```bash
ssh -N -L 3000:127.0.0.1:3000 bibi
```

Then visit `http://localhost:3000`. Do not add public firewall rules for normal
development servers.

## Rebuild and recovery

The rebuild boundary is deliberate:

- public infrastructure, doctl, and other system/user tools come from this repo
- unauthenticated cloud-init never fetches `brancusi/pi-extensions`
- after `gh auth login`, `bibi-pi-extensions-update` restores its exact reviewed pin
- `/digitalocean-login hey-coach` restores the named doctl context interactively
- GitHub, DigitalOcean, Firecrawl, and Pi credentials are never restored from
  user-data or this repository
- FirstMate's private `data/`, `state/`, and project worktrees require backup
  or deliberate recreation

On a clean rebuild, first wait for cloud-init, run `bibi-verify` (the private
package will be `pending`), perform section 6, then run `bibi-verify` again. To
recover drift on an existing machine, reconcile as `bibi-admin`, then rerun the
daily-user private package command; do not copy doctl configuration or tokens
through this repo.

Enable DigitalOcean backups, but remember that snapshots contain credentials
and source code. Before destroying a VM, use FirstMate's `/stow`, push all
needed branches, and confirm important state is backed up.

## Creative chaos policy

Bibi is encouraged to explore strange architectures, challenge assumptions,
run parallel scouts, and make unexpected connections. The non-negotiable rules
are the containment rules: no local Mac mounts, no SSH-agent forwarding, no
daily-user sudo, no secrets in Git, and no public development ports.
