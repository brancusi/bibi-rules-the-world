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
- checksum-pinned Herdr, Treehouse, and no-mistakes binaries
- exact npm pins for Pi and the required AXI tools
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
Do not interrupt it midway.

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

## 6. Authenticate once on the remote VM

Everything in this section runs after `ssh bibi`:

```bash
gh auth login
gh auth setup-git
pi
```

Inside Pi, use `/login` for your model provider, then exit. Approve Pi's trust
prompt the first time you launch it from `~/firstmate`; that allows
FirstMate's tracked Pi extensions to load.

Credentials stay on the remote VM. Do not forward your Mac's SSH agent.

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

As the daily user:

```bash
bibi-verify
```

The provisioned version record is:

```bash
cat /etc/bibi-provisioned-versions
```

## Update or reconcile the VM

1. Change pins or tasks in this repository.
2. Run `make lint` locally, review, and push the change.
3. Enter through the maintenance identity:

```bash
ssh bibi-admin
sudo bibi-machine-update
```

The daily `bibi` account cannot run the update command. Treat changes to this
repository as root-level changes and protect its default branch accordingly.

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

- infrastructure and tools come from this repo
- source repositories come from Git remotes
- GitHub and Pi credentials are restored interactively
- FirstMate's private `data/`, `state/`, and project worktrees require backup
  or deliberate recreation

Enable DigitalOcean backups, but remember that snapshots contain credentials
and source code. Before destroying a VM, use FirstMate's `/stow`, push all
needed branches, and confirm important state is backed up.

## Creative chaos policy

Bibi is encouraged to explore strange architectures, challenge assumptions,
run parallel scouts, and make unexpected connections. The non-negotiable rules
are the containment rules: no local Mac mounts, no SSH-agent forwarding, no
daily-user sudo, no secrets in Git, and no public development ports.
