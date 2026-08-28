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
- a checksum-pinned Temurin JDK 21 and Clojure CLI in `/opt/bibi/toolchains`
- exact npm pins for Pi, Wrangler 4.x, Firecrawl CLI, and the required AXI tools
- exact public Pi package pins plus the reviewed official `cloudflare/skills`
  `cloudflare` and `wrangler` skills
- a reviewed commit pin and daily-user installer for the private Pi Extensions collection
- SSH key authentication only, no root SSH, no agent forwarding
- the DigitalOcean bootstrap key is removed from root after it is copied to
  `bibi-admin` and `bibi`
- UFW, fail2ban, and unattended security updates
- a bounded 2 GiB swap file and a periodic memory guard that cleans only
  ownership-proven browser-helper leaks
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
The architecture-specific JDK and architecture-independent Clojure CLI are also
checksum-pinned. Stable links and a managed login profile expose only their
shared `/opt/bibi/toolchains` homes; no project checkout supplies Java or
Clojure. Wrangler and the other global npm CLIs are exact version pins installed without
package lifecycle scripts. Public Pi packages are reconciled through Pi's own
package installer with npm lifecycle scripts disabled. The official Cloudflare
skill repository is checked out at a
reviewed commit, and only its `cloudflare` and `wrangler` skills are linked into
the daily user's Pi skill directory. A bounded swap file and the memory guard's
service and timer are installed; provisioning runs the guard only in read-only
report mode and never performs a cleanup. Provisioning does **not** authenticate
Cloudflare or doctl, and it does not fetch the private Pi Extensions repository.
Do not interrupt provisioning midway.

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
wrangler login
wrangler whoami
firecrawl login --browser
firecrawl --status
pi
```

`bibi-pi-extensions-update` checks the GitHub CLI login, configures Git to use
GitHub CLI's credential helper, and invokes Pi's official Git package installer
against the exact reviewed commit over HTTPS. It disables terminal credential
prompts, never copies or forwards a GitHub token, and is safe to rerun to
reconcile the checkout. The private repository is not fetched until this step.
The public Pi packages and official Cloudflare skills were already restored by
Ansible and require no GitHub authentication. `wrangler login` creates local
OAuth state; that generated account file is deliberately not provisioned or
stored in this repository.

Inside Pi, use `/login` for your model provider. Then create and accept a named
DigitalOcean context through the extension's masked local UI:

```text
/digitalocean-login hey-coach
```

Enter a newly created least-privilege DigitalOcean API token only in that masked
prompt and confirm the displayed account/team identity. Never paste the token
into chat, a shell command, Pi settings, Git, or this repository. Provisioning
never runs `doctl auth`, creates a context, or contains a DigitalOcean/GitHub
token. The extension requires Node.js 22+, Pi 0.83.0-compatible
`pi-ai`, `coding-agent`, `pi-tui`, and `typebox` core peers, plus official
doctl 1.165.0+ in the 1.x series. Pi supplies those peers; this repo pins
compatible Pi 0.83.0 and doctl 1.166.0.

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

As the daily user, verify the shared JDK 21/Clojure CLI resolution, command
availability, exact global npm CLI versions, pinned public Pi packages, the
official Cloudflare skill source and links, the exact doctl version and root
ownership/mode, the memory guard version/policy/timer and the bounded swap area,
FirstMate configuration, sudo separation, and (when installed) the private
collection commit/package version. This clean login-shell check is
the authoritative verification command:

```bash
env -i HOME="$HOME" USER="$USER" LOGNAME="$LOGNAME" SHELL=/bin/bash \
  /bin/bash --login -c 'bibi-verify'
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
   architectures and replace both archive checksums. Review exact npm and public
   Pi package versions before changing them. For Cloudflare skills or Pi
   Extensions, review and replace the full commit ref; never provision a moving
   branch. The memory guard's grace periods, per-run cap, and swap size are
   safety bounds, not tuning knobs: `tasks/install-memory-guard.yml` and
   `tasks/install-swap-safety-net.yml` refuse values outside their reviewed
   range, and loosening one needs the same review as any other root change.
2. Run `make lint` locally, review, and push the change.

For a reviewed JDK/Clojure-only repair, the maintenance identity can apply the
narrow playbook without reconciling unrelated host state:

```bash
sudo ansible-playbook -i 'localhost,' shared-clojure-toolchain.yml
```

3. After the reviewed change lands, enter through the maintenance identity and
   reconcile public/system state:

```bash
ssh bibi-admin
sudo /usr/local/sbin/bibi-machine-update
```

The machine update automatically reconciles Wrangler, public Pi packages, and
the official Cloudflare skill checkout for `bibi` without touching credentials.

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

## Memory safety net

An 8 GiB Droplet with no swap ran out of headroom because browser QA left its
helper processes behind. This section documents what the safety net does, what
it refuses to do, and how to drive it.

### What actually happened

Repeated browser QA finished without tearing down its Chrome DevTools tooling.
Each abandoned session left a five-process tree alive: a `chrome-devtools-axi`
bridge, an `npm exec chrome-devtools-mcp` launcher, an `sh -c` shim, the MCP
server, and the MCP telemetry watchdog - plus any headless Chromium the session
had opened. 125 such processes held about 5.1 GiB.

The causal chain has three separate links, and conflating them is what makes
naive cleanup dangerous:

| Link | What it is |
| --- | --- |
| Initiating trigger | A browser QA task ends without stopping its bridge. Nothing in task teardown stops it. |
| Masking condition | The bridge is a daemon, so it reparents to PID 1 by design and leaves the agent's process group. The watchdog moves into its *own* process group. With no swap, the resident anonymous pages can never be evicted. |
| Visible symptom | MemAvailable falls monotonically; the host approaches OOM. |

High RSS is the symptom, not the evidence. On the live host, a leaked tree and
two perfectly healthy ones were **identical** on every cheap signal:

| Signal | Leaked (`mm-a1`) | Live (`ara8`, `ara8b`) |
| --- | --- | --- |
| Reparented to PID 1 | yes | yes |
| Age | ~21 h | ~17 h |
| Tree RSS | ~393 MB | ~396 MB / ~400 MB |
| Bridge port listening | yes | yes |
| Client connected to that port | no | no |

The one difference: the `artist-archiver` worktree still had a live `claude`
agent and a non-terminal FirstMate task; the `money-monk` worktree had neither.
That is the smallest counterfactual, and it is the only thing the guard treats
as authorisation. Anything that would kill on RSS, age, name, or reparenting
would have killed two live browser sessions.

### The policy

`bibi-memory-guard` runs from a systemd timer every 15 minutes. Observation and
destruction are deliberately separate.

**Observation** (raises alerts, never authorises anything):

| Threshold | Default |
| --- | --- |
| `mem_available_warn_percent` | 20 |
| `mem_available_critical_percent` | 10 |
| `swap_used_warn_percent` | 25 |
| `swap_used_critical_percent` | 60 |

**Eligibility** (all must hold; none of them mention memory):

| Requirement | Default |
| --- | --- |
| Root matches the leak family structurally, by installed script path | - |
| Root is reparented to PID 1 | - |
| Root age | >= 3600 s |
| Every member's age | >= 300 s |
| Tree RSS | >= 256 MB |
| Tree size | <= 60 processes |
| Every member owned by the agent account | - |
| A bridge is claimed by a `bridge.pid` session file | - |
| No connected client on that session's port | - |
| **Positive ownership proof** | see below |
| Trees cleaned per run | <= 2 |

Positive ownership proof is exactly one of:

- the tree's working directory was **deleted**, so its worktree provably no
  longer exists; or
- the worktree is claimed by a FirstMate task whose last status line is `done:`
  or `failed:`, whose busy flag is not set *and fresh*, whose status file is at
  least 900 s old, and in which no agent harness process is still running.

A busy flag protects only while it is under `busy_state_fresh_seconds` (3600 s)
old. An agent that died mid-turn leaves its flag set forever, and treating that
as permanent protection would make the guard useless against the exact leak it
exists for. A stale flag stops protecting; it never authorises anything on its
own, because the terminal-status and live-owner checks still have to pass.

### Protected cases

The guard refuses, alerts, and moves on when any of these hold. Every refusal
is named in the journal.

| Refusal | Meaning |
| --- | --- |
| `owner-process-alive` | An agent harness is still running in that worktree. |
| `owning-task-busy` | The FirstMate task's busy flag is set. |
| `owning-task-not-terminal` | The task is `working:`, `blocked:`, `paused:`, or `needs-decision:`. |
| `owning-task-recently-finished` | The task finished less than 900 s ago. |
| `owning-task-status-unreadable` | The status file could not be read. |
| `unclaimed-worktree` | No FirstMate task claims the worktree, so the owner is unknown. |
| `unresolved-worktree` | The tree runs outside the worktree pool entirely. |
| `unregistered-bridge` | No `bridge.pid` names this bridge, so its session cannot be identified. |
| `active-client-connection` | Something is connected to the bridge's port right now. |
| `root-still-attached` | The root still has a live parent, so it is not orphaned. |
| `root-below-age-grace` / `member-below-age-grace` | Too young to call abandoned. |
| `below-rss-floor` | Too small to be worth any risk. |
| `tree-too-large` | Unexpected shape; refusing is cheaper than mis-scoping. |
| `foreign-uid-member` | Something not owned by the agent account is inside the tree. |
| `self-or-ancestor-in-tree` | The guard would be signalling itself. |
| `pressure-without-eligible-leak` | Memory is low and nothing is provable. Alert only. |
| `stranded-browser` | An old Chromium with no live bridge or MCP root left in its worktree. Reported, never killed. |

Chromium double-forks, so a browser process reparented to PID 1 is the *normal*
shape for a healthy session. The guard therefore reports a stranded browser only
once it is past the leak grace period and no live bridge or MCP root remains in
its worktree - otherwise the alert would fire on every browser session and train
operators to ignore it.

Two design rules make this hold:

- **Every name-based match is protective only.** Matching a process name can
  stop a kill; it can never cause one. Destructive matching is structural (the
  installed script path) plus ownership proof.
- **Ambiguity always refuses.** If the guard cannot name a tree's session or
  find its owner, it reports and alerts rather than guessing.

### Exact kill eligibility and blast radius

An eligible tree is terminated as one exact set: the root plus its transitive
children by parent link, computed at decision time. Members are signalled
leaves-first, so the watchdog never sees its parent vanish first. Immediately
before each signal the guard re-reads `/proc` and compares the process start
identity, so a recycled PID is skipped rather than hit. `SIGTERM` goes to every
verified member, then a bounded wait, then `SIGKILL` only to survivors. The
guard then verifies absence and remeasures MemAvailable.

The tree is walked by parent link, **not** by process group, because the MCP
watchdog calls `setsid` - a `kill -TERM -PGID` silently leaves it running.

There is no `pkill`, no `killall`, no `killpg`, and no pattern kill anywhere in
the guard. `os.kill` appears exactly once, always with a single verified PID;
the provisioning tests assert both facts.

### Why this cannot clean arbitrary memory consumers

The guard's authority comes entirely from being able to *prove* who owned a
process. That proof exists for this one family because a `chrome-devtools-axi`
bridge registers itself in a `bridge.pid` file, runs from a known installed
script path, and holds the owning worktree as its working directory, which maps
back to a FirstMate task record.

A generic memory hog has none of that. A large `java`, a runaway build, or a
Chromium that escaped its MCP parent cannot be tied to a finished owner, so
killing it would be a guess about someone else's live work. The guard reports
those and refuses. Reclaiming memory from an unproven process is an operator
decision, not an automated one - which is also why low memory never widens
eligibility. When memory is critical and nothing is provable, the guard says so
loudly and does nothing.

### Swap: why a file, not zram

The Droplet had no swap at all, so cold anonymous pages could never leave RAM.
Both options were considered:

| | Bounded swap file (chosen) | zram |
| --- | --- | --- |
| Effect on a leaked idle tree | Pages leave RAM entirely | Pages stay in RAM, compressed |
| Headroom from 2 GiB | ~2 GiB of real RAM freed | Roughly 0.7-1.3 GiB net, depending on ratio |
| Cost | Disk writes to local NVMe, paid by pages nobody is touching | CPU on every page in and out |
| Fit for this incident | The leak profile is cold and idle - ideal swap candidates | Weakest exactly where the leak is largest |

2 GiB (25% of RAM) is deliberately **too small** to absorb a repeat of the
5.1 GiB leak. It is a margin that keeps the host responsive long enough for the
guard's next run and for an operator to read the alert; it is not capacity.
`vm.swappiness=10` keeps hot agent working sets resident, and swap usage above
25% is itself an alert, so swap makes a leak more visible rather than hiding it.
`swap_file_max_size_mb` refuses any reviewed size above 4 GiB.

An OOM killer daemon such as `earlyoom` was rejected: it selects victims by
size and score, which is precisely the unproven, name-and-RSS-based killing this
design exists to avoid.

### Alerting

The repository has no external operator channel, and this change does not
invent one. Alerts land on the surfaces that already exist:

- **journald**, under `SyslogIdentifier=bibi-memory-guard`, bounded to 200 lines
  per run
- **`/var/lib/bibi-memory-guard/status.json`**, the last run's machine-readable
  record
- **the login banner**, which prints MemAvailable, swap usage, and the guard's
  last verdict
- **`bibi-verify`**, which fails when the guard, its policy, its timer, or the
  swap area drifts from the reviewed pins

Nothing recorded on any of these surfaces contains a command line or an
environment value. Command lines are read once for structural classification
and discarded before any reporting type is constructed, because agent command
lines carry task briefs, URLs, and occasionally credentials.

### Operating it

Everything read-only runs as any user; anything that changes state runs as
`bibi-admin`.

```bash
# What did the last run decide?
/usr/local/sbin/bibi-memory-guard status

# Observe now without acting; also refreshes the recorded status.
sudo /usr/local/sbin/bibi-memory-guard report

# Show the exact tree that would be terminated, and why every other tree is not.
/usr/local/sbin/bibi-memory-guard dry-run

# Run a real cleanup pass now, out of schedule.
sudo systemctl start bibi-memory-guard.service

# Timer schedule and recent runs.
systemctl list-timers bibi-memory-guard.timer
systemctl status bibi-memory-guard.timer
journalctl -u bibi-memory-guard.service -n 200

# Swap and tuning.
swapon --show
sysctl vm.swappiness vm.vfs_cache_pressure
```

Always read `dry-run` before starting a real pass. It runs the identical
decision pipeline and prints a `planned:` line for each tree it would clean.

### Disabling and rolling back

To stop the guard without removing it, set `memory_guard_enabled: false` in
`group_vars/all.yml` and reconcile; the timer is disabled and stopped, and the
guard and its policy stay in place for `dry-run` inspection. In an emergency,
before that change lands:

```bash
ssh bibi-admin
sudo systemctl disable --now bibi-memory-guard.timer
```

To remove the swap safety net, set `swap_manage_system: false` and reconcile,
then retire the area by hand:

```bash
sudo swapoff /swapfile
sudo sed -i '\|^/swapfile |d' /etc/fstab
sudo rm -f /swapfile /etc/sysctl.d/60-bibi-memory.conf
```

Reconciliation itself is never destructive: applying `site.yml` installs the
guard and runs it only in read-only report mode.

### Expected overhead

One run reads `/proc` once for the inventory - plus one more scan per cleanup
wait poll, and only when it is actually cleaning - together with
`/proc/net/tcp`, the `bridge.pid` files, and the FirstMate `*.meta`, `*.status`,
and `*.busy-state` files. On this host that is well under a second
of CPU at `Nice=10` and idle I/O priority, capped by `MemoryMax=192M` and
`TimeoutStartSec=120`. At a 15-minute interval the steady-state cost is
negligible. Runs cannot overlap: systemd refuses a concurrent start of the unit,
and the guard also holds an `flock` so a manual one-shot cannot race a scheduled
one.

## Forward a development port without exposing it

If a remote app listens on `127.0.0.1:3000`, open a second WezTerm tab:

```bash
ssh -N -L 3000:127.0.0.1:3000 bibi
```

Then visit `http://localhost:3000`. Do not add public firewall rules for normal
development servers.

## Rebuild and recovery

The rebuild boundary is deliberate:

- public infrastructure, the shared JDK/Clojure toolchain, doctl, exact npm
  CLIs, public Pi packages, and the pinned official Cloudflare skills come from this repo
- unauthenticated cloud-init never fetches `brancusi/pi-extensions`
- after `gh auth login`, `bibi-pi-extensions-update` restores its exact reviewed pin
- `/digitalocean-login hey-coach` restores the named doctl context interactively
- GitHub, Cloudflare, DigitalOcean, Firecrawl, and Pi credentials are never
  restored from user-data or this repository
- FirstMate's private `data/`, `state/`, and project worktrees require backup
  or deliberate recreation
- the swap file and the memory guard's recorded status are rebuilt from this
  repository; the guard's ownership evidence comes from FirstMate state, so a
  host restored without it will refuse every cleanup until tasks run again

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
