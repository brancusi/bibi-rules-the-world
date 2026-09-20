# bibi-rules-the-world

> A remote laboratory for creative, exploratory, mind-bending agentic coding.
> We bend assumptions, not isolation boundaries.

This repository provisions a reusable, isolated Firstmate installation on
Ubuntu 24.04 or native Apple Silicon macOS. The long-standing DigitalOcean
cloud-init path remains supported. Native macOS support is **provisional**
until the real Mac Mini acceptance receipt below is completed; Linux mocks and
hosted CI do not establish login, FileVault, sleep/wake, or launchd behavior.

## Portable entry point

Plan is read-only and is the default. Apply must be explicit:

```bash
git clone https://github.com/brancusi/bibi-rules-the-world.git
cd bibi-rules-the-world
./bin/bibi-setup --plan --backend herdr
./bin/bibi-setup --apply --backend herdr
```

The dispatcher accepts Ubuntu 24.04+ on x86_64/aarch64 and native
`Darwin-arm64`. It clearly refuses Intel macOS and unknown platforms. On Ubuntu,
an omitted profile selects `ubuntu-compat`, preserving the repository's former
complete tool set. On macOS, an omitted profile selects the smaller `base`.
Repeat `--profile` to opt into capabilities:

| Profile | Contents |
| --- | --- |
| `base` | Node, Git/GitHub CLI, Pi, Firstmate, no-mistakes, required AXI tools, Treehouse, tmux, and the selected backend |
| `ubuntu-compat` | Historical Ubuntu defaults: base plus all formerly installed public/provider/language tools and host safety policy |
| `public-pi-extras` | Pinned files widget and pi-web-access packages |
| `cloudflare` | Pinned Wrangler and the selected skills from the reviewed official Cloudflare checkout |
| `digitalocean` | Checksum-pinned doctl; authentication is separate |
| `web-research` | Pinned Firecrawl CLI and pi-web-access |
| `clojure` | Checksum-pinned Temurin 21 and Clojure CLI |
| `browser` | Ubuntu x86_64: checksum-pinned official Chrome Stable, pinned MCP and sandboxed AXI preflight. macOS: existing reviewed native Chrome and its existing smoke. Linux ARM: manual prerequisite, not auto-installed |
| `private-capabilities` | Caller-owned authenticated exact-ref manifest; never credentials or copied package contents |

The setup creates separate code and operating roots:

```text
Firstmate code  ~/.local/share/firstmate/source/<reviewed-commit>/
FM_HOME         ~/.local/share/firstmate/instances/main/
Pi home         ~/.local/share/firstmate/instances/main/pi/
Treehouse pool  ~/.local/share/firstmate/instances/main/treehouse/
user commands   ~/.local/bin/
receipt         ~/.local/state/bibi/provisioned-versions
```

`bibi` exports the exact `FM_HOME`, `PI_CODING_AGENT_DIR`, and isolated
`TREEHOUSE_DIR`, changes to the reviewed Firstmate source, and starts Pi. A
rerun does not reset an existing home, backend choice, grants, settings,
projects, profiles, auth, trust, or
sessions. A clean Firstmate checkout advanced by the guarded upstream updater
is accepted as a descendant and is never reset to the older installer floor.

No setup path imports another instance's projects, backlog, task state, locks,
secondmate records, provider credentials, GitHub/cloud credentials, Pi
`auth.json`, `trust.json`, sessions, or package checkout tree. Pi project trust
is local input approval, not a sandbox: package extensions execute with the
full authority of the daily user. Review the exact clone before accepting its
trust prompt.

## Native Apple Silicon macOS (provisional)

Before `--apply`, install the Xcode Command Line Tools through Apple's UI and
Homebrew through its official instructions:

```bash
xcode-select --install
# Follow https://docs.brew.sh/Installation; Apple Silicon prefix must be /opt/homebrew.
```

The adapter refuses root. It uses Homebrew only for OS prerequisites and keeps
Node, Pi/npm CLIs, Firstmate, Herdr, Treehouse, no-mistakes, optional doctl, and
optional language tools user-owned. Downloads are size-bounded and verified by
reviewed SHA-256 before an atomic activation. Exact npm semvers are checked
against the reviewed registry integrity before staging. The installer never
creates accounts, enables Remote Login, changes the firewall or energy policy,
or changes FileVault or automatic login. Interactive authorization required by
Apple or Homebrew remains a human action. The existing-user adapter installs
AXI CLIs without their cross-harness `setup hooks`, so it does not rewrite
`~/.claude`, `~/.codex`, or OpenCode configuration.

For optional post-login Herdr supervision, add `--launch-agent` with the Herdr
backend. This installs and validates
`~/Library/LaunchAgents/dev.bibi.herdr.default.plist` but does not load it. After
reviewing it in an Aqua login session, load it explicitly:

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/dev.bibi.herdr.default.plist"
launchctl print "gui/$(id -u)/dev.bibi.herdr.default"
```

Run `~/.local/bin/bibi-verify` after apply and after loading the optional
LaunchAgent. Herdr stdout/stderr are user-owned under
`~/Library/Logs/Firstmate/`; include their growth and rotation in real-Mac
acceptance rather than assuming hosted CI exercised them.

`KeepAlive` can restart the Herdr server after a crash while that GUI user is
logged in; `RunAtLoad` starts it only after login. It is not a pre-login daemon.
With FileVault and automatic login unchanged, a cold boot waits for human
unlock/login. Herdr endpoint identifiers may recover after a server restart,
but Pi harness processes and live conversations do not survive logout/reboot;
the next interactive `bibi` launch reconciles durable Firstmate state.

### Required real Mac Mini acceptance receipt

Do not describe native macOS as supported until a standard, disposable Apple
Silicon account records all of these results:

1. `--plan` changes no files; `--apply` succeeds without root-owned files in the user home; rerun is idempotent.
2. Exact commands resolve in interactive and non-login shells and under the LaunchAgent's absolute PATH.
3. `plutil -lint` and `launchctl print gui/<uid>/dev.bibi.herdr.default` prove the reviewed program, arguments, logs, and Aqua scope.
4. A fresh Pi launch uses the new `FM_HOME`/Pi home, requires trust for only that clone, and requires a fresh provider `/login`.
5. `herdr integration status` reports Pi `current` in that Pi home, and one controlled turn shows idle → working → idle in both Herdr and Firstmate.
6. A disposable project/Treehouse worker can be created and safely torn down without touching another checkout or Herdr session.
7. No project, backlog, state, secondmate marker, credential, grant, trust decision, or session from another instance exists.
8. Wrong-checksum and interrupted-download tests leave the active command intact; staged upgrade and rollback both succeed.
9. Logout/login starts only Herdr after Aqua login and does not claim Pi is alive. A FileVault reboot requires manual unlock/login and then reconciles.
10. Browser smoke runs only when the browser profile is selected.

## Shared Chrome for project testing (Ubuntu x86_64)

Install once, then use a **new disposable profile and native AXI session for every
run**. `group_vars/all.yml` owns Chrome's exact Debian version/SHA-256, official
source, AXI pin and MCP pin/integrity. There is no Playwright-cache fallback.
The default `ubuntu-compat` profile is unchanged; `browser` is explicitly opt-in.

### Administrator installation / reconciliation

On an already provisioned Ubuntu 24.04+ x86_64 host, as **bibi-admin**, review the
PR and check out its exact approved commit in an administrator-owned clone.
Then, from that clone (Ansible, Node and the daily account must already exist):

```bash
sudo ansible-playbook --inventory 'localhost,' browser.yml
```

This is the browser-only path, **not** `bibi-machine-update`, `site.yml`, or
`bibi-setup --apply`. It installs the checksum-verified Google Stable `.deb`
with apt and necessary package dependencies; preserves the package's standard
sandbox installation; reconciles only Chrome AXI and its pinned MCP as `bibi`;
installs `/usr/local/bin/bibi-browser`, its source under
`/usr/local/lib/bibi/browser/`, and `/etc/bibi-browser.json`; and refreshes the
existing `bibi-verify` dispatcher/Linux checker to recognize that receipt. It ends with the
same unprivileged preflight that workers use. Google's package maintainer
scripts install its signed-by update source/keyring, desktop entries/browser
alternatives and (where needed) its standard AppArmor profile. Those are normal
package effects, not a custom privilege helper. No browser runs as root.

The helper includes the **existing** `bibi_memory_guard.py` admission source,
not a second monitor: `admit --no-reserve` reads the existing host policy and
`/proc` memory facts. This keeps browser-only reconciliation independent of an
older installed guard daemon. It does not install/restart a memory timer,
change swap, accounts, sudo, SSH, firewall, worker endpoints or unrelated tools.
The separate browser receipt does not overwrite the full machine receipt.

Google Chrome at `/opt/google/chrome/chrome` is covered by Ubuntu's standard
`/etc/apparmor.d/chrome` user-namespace policy. See the official
[Chromium sandbox explanation](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md),
[Ubuntu policy rationale](https://ubuntu.com/blog/ubuntu-23-10-restricted-unprivileged-user-namespaces),
and [Chrome Linux requirements](https://support.google.com/chrome/a/answer/9025903).
Do **not** use `--no-sandbox`, copy a privileged sandbox helper, disable AppArmor,
or globally relax user namespaces. If the installed package still reports
`No usable sandbox`, stop and have the administrator diagnose the exact package
and loaded OS policy; a nonstandard security exception requires separate review.
Do not add sudo rights to the daily agent.

Linux ARM is refused before installation: this manifest has no official native
Google Chrome `.deb` for it. A separately reviewed distro-native sandbox-capable
browser and ARM verification are prerequisites for a future adapter, not a
claimed success here. The provisional macOS adapter remains unchanged: install
native Google Chrome through its official installer first; its existing smoke
is not evidence for this Ubuntu AXI helper.

### Daily-user preflight and project use

As the same ordinary `bibi` user that workers use:

```bash
bibi-browser preflight
# Run a foreground, project-owned test script after the same preflight:
cd /path/to/project
bibi-browser --timeout 900 run -- bash ./scripts/browser-qa.sh
```

Inside that script use the native CLI normally; the wrapper supplies only this
run's `CHROME_DEVTOOLS_AXI_SESSION`, loopback `BROWSER_URL`, explicit pinned
`MCP_PATH` and bridge port. It launches `/opt/google/chrome/chrome` explicitly,
with the standard sandbox, a fresh private `--user-data-dir`, and an ephemeral
CDP port. It never attaches to a personal browser. For example:

```bash
chrome-devtools-axi open http://127.0.0.1:3000
chrome-devtools-axi run <<'JS'
console.log(await page.eval(() => document.title));
JS
chrome-devtools-axi screenshot "$BIBI_BROWSER_EVIDENCE/project.png"
```

Keep scripts in the foreground; do not background bridges, change the provided
connection variables, launch extra browsers, or use `AUTO_CONNECT`. Preflight
serves a disposable local synthetic HTML page, proves the exact navigation and
JavaScript result, checks empty cookie/localStorage state, then saves a PNG via
AXI. Both CDP and the bridge must have **owned loopback-only** listening sockets
before use. No public debugging port, tunnel, firewall rule, or remote access is
created. CDP is unauthenticated local control, so loopback is not a security
boundary against other users on the same server; profiles isolate test state,
not mutually hostile code running under one Unix account.

One cooperative per-user file lock bounds wrapper-managed browser work to **one
run across projects**. The existing memory admission policy can refuse that
next run without inventorying or stopping any process. Other direct browser
launches are outside this bound: migrate projects to this command rather than
running around a busy slot. Let live tests/workers finish when admission refuses;
low memory never authorizes a kill. Commands are time-bounded (preflight stages
20s; project command default 900s, maximum 1800s), with bounded tool diagnostics.

Cleanup is automatic on normal exit, failure, timeout, SIGINT or SIGTERM. It
calls native `chrome-devtools-axi stop` only after checking this exclusively
created session's PID, UID, start identity, cwd and connection environment, then
terminates/reaps only its own Chrome child. AXI 0.1.31 can detach a bridge before
registration and leave it behind on startup timeout. The helper therefore also
retains Linux pidfds for at most 32 descendants witnessed through its **own live
AXI-start child** (not a machine-wide process scan); only those same identities
can be terminated if native cleanup leaves startup survivors. Unknown or changed
ownership refuses cleanup and retains evidence. There is no PID-taking stop command, `pkill`, or
fleet sweep. Do not call `stop` on a default/another task's session. SIGKILL or a
host crash cannot run a `finally` block: retain the printed session evidence for
operator investigation; do not assume a stale profile proves a browser orphan.

Run evidence is private under `~/.bibi-browser/run-*`: logs, a non-secret session
record and screenshot survive; the live profile and owned AXI session files are
removed only after cleanup. Record the result, then remove **that exact printed
run directory** when no longer needed (`rm -r -- /exact/printed/run-directory`).
Do not glob-delete other projects' runs. Project commands' output is captured in
that directory, not broadcast. Never copy cookies, personal profiles or provider
credentials. Use each project's own restricted test accounts and synthetic data;
there is no global shared login/password. Screenshots and logs may contain test
data; share only reviewed, redacted evidence. MCP 1.9's default filesystem root
is its OS temp directory, and AXI's SDK transport filters out `TMPDIR` and MCP
telemetry environment settings. The helper uses AXI's supported `MCP_PATH`
interface with a tiny private per-run entrypoint that imports the version-checked
MCP unchanged, passing native `--workspace=<private-evidence-directory>` and
`--no-usage-statistics` options. No global package is patched or unrestricted
filesystem access granted. Put synthetic upload fixtures there and write
screenshots there; outside file paths intentionally refuse.

`bibi-verify` invokes this preflight when the browser profile or browser-only
receipt is present. A successful package/unit/CI test is **not** a successful host
smoke: require the unprivileged launch/navigation/JS/screenshot/cleanup receipt.
Missing executable, sandbox refusal and AXI connection failure are distinct
errors. Command stdout is validated separately from retained stderr diagnostics:
Chrome can emit a benign channel warning to stderr even for `--version`. The
preflight compares the full Debian package version (including `-1`, etc.) to
the receipt, and compares the executable's upstream version without that package
revision. Both are exact checks; warnings are not version drift, and neither a
newer binary nor a different package revision is silently accepted. Chrome AXI 0.1.31's `pages` formatter can turn an underlying MCP
`list_pages` error into an empty list; its screenshot formatter can likewise
print a destination even when MCP refused the write. Never interpret `pages: 0`
or a printed screenshot path as proof. The helper checks positive end-to-end
results and the actual PNG file, and retains startup/tool diagnostics. The formatter's missing `isError` handling is a separate upstream AXI
issue; this repository does not patch globally installed code.

### Browser security updates

Google's versioned package URL can eventually disappear; fail closed rather
than downloading a moving `current` artifact or an unofficial mirror. Review
Chrome Stable security releases promptly, update version/hash together from the
[official Debian index](https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-amd64/Packages.gz),
verify downloaded bytes, run `make lint`, then administrator-reconcile and repeat
the daily-user smoke. Review MCP/AXI upgrades through the same pin owner. There
is no new updater daemon or package hold: normal administrator/OS package updates
may advance Chrome. Verification deliberately detects drift, and reconciliation
refuses to downgrade a newer installed browser; promote the reviewed pin instead
of undoing a security update. Exact top-level npm pins follow this repository's
npm policy, not a vendored transitive-dependency lockfile.

## Authentication and private capabilities

Public installation ends without GitHub, model-provider, cloud, browser, or
media credentials. Authenticate each service freshly as the daily user. Then
launch `bibi`, approve only the reviewed source clone, and run Pi `/login`.

Private packages use a local manifest kept outside this public repository. Copy
`examples/private-capabilities.manifest.example`, replace placeholders with
reviewed GitHub HTTPS repositories and full 40-hex commit IDs, and protect it
locally. The manifest is limited to 64 KiB and 1–32 distinct sources. On macOS,
plan prints those sources; apply validates the entire manifest and requires
`gh auth status` before any installer mutation, then uses `gh auth setup-git`
and Pi's official installer. On a fresh Mac, apply `base` first, run
`gh auth login`, then rerun with the private profile and manifest. On Ubuntu,
the privileged setup phase refuses `--private-manifest`; after setup,
authenticate as the daily user, and run
`bibi-private-capabilities-update --apply /path/to/manifest`. That command applies
the same complete preflight before installing. Tokens are never accepted
in the manifest, argv, receipt, or repository.

## Upgrade and rollback

Change versions, commits, hashes, and npm integrities only in a reviewed PR.
The macOS adapter stages and verifies user-owned archives and npm CLIs before
switching command symlinks; a failed download, checksum, extraction, or version
check leaves the active command unchanged. Ubuntu uses the pinned Ansible
reconciliation path and its idempotency/upgrade fixture.
Receipts contain versions, paths, profiles, and hashes but no credentials.
Homebrew-managed prerequisites are verified rather than automatically
downgraded. Activate service configuration last.

To roll back, re-run a previously reviewed manifest. Switch Firstmate code back
only when upstream declares state compatibility; otherwise restore the paired
local state backup as well. Never roll back by importing this machine's state.
The personal reconciler deliberately preserves a clean Firstmate checkout that
advanced through `/updatefirstmate` instead of silently downgrading it.

## What the Ubuntu compatibility profile builds

- `bibi`: the unprivileged daily agent account; it cannot use `sudo`
- `bibi-admin`: a separate key-only maintenance account with `sudo`
- Pi as primary and crew harness
- Herdr as the persistent FirstMate backend
- FirstMate pinned to a reviewed Git commit
- checksum-pinned Herdr, Treehouse, no-mistakes, and official `doctl` binaries
- a checksum-pinned Temurin JDK 21 and Clojure CLI in `/opt/bibi/toolchains`
- exact npm pins for Pi, Wrangler 4.x, Firecrawl CLI, and the required AXI tools
- exact public Pi package pins plus the Ubuntu set named in
  `cloudflare_skill_names` from the reviewed official `cloudflare/skills`
  checkout
- a reviewed commit pin and daily-user installer for the private Pi Extensions collection
- SSH key authentication only, no root SSH, no agent forwarding
- the DigitalOcean bootstrap key is removed from root after it is copied to
  `bibi-admin` and `bibi`
- UFW, fail2ban, and unattended security updates
- a bounded 2 GiB swap file and a memory guard that cleans only
  ownership-proven browser-helper leaks and refuses a new worker launch when
  the host has no headroom for it
- GitHub Actions validation for YAML, Ansible, and cloud-init rendering

Herdr is an experimental FirstMate backend. That choice is explicit in the
isolated instance's `config/backend`; changing an existing selection requires
review rather than a silent setup rerun.

## Ubuntu / DigitalOcean path

The sections below retain the original unattended Ubuntu workflow. They select
the explicit `ubuntu-compat` profile unless an operator invokes the portable
entry point with narrower profiles.

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
skill repository is checked out at a reviewed commit, and only the skills named
in `cloudflare_skill_names` are linked into the daily user's Pi skill directory.
A bounded swap file and the memory guard's
service and timer are installed; provisioning runs the guard only in read-only
report mode and as a pure launch admission query, and never performs a cleanup.
Provisioning does **not** authenticate
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

## 6. Authenticate and install the legacy Ubuntu compatibility package

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
token. The extension requires Node.js 22+ and the `pi-ai`, `coding-agent`,
`pi-tui`, and `typebox` core peers, plus official doctl 1.165.0+ in the 1.x
series. Pi supplies those peers; this pin was exercised with Pi 0.85.1 and
doctl 1.166.0.

If you prefer non-interactive Firecrawl setup, set `FIRECRAWL_API_KEY` in the
remote VM environment instead of running `firecrawl login --browser`. Firecrawl
can use its keyless free tier for supported commands, but a logged-in API key is
preferred for usable limits. To disable Firecrawl telemetry, optionally add this
to the remote shell environment:

```bash
export FIRECRAWL_NO_TELEMETRY=1
```

Approve Pi's trust prompt the first time `bibi` launches from the exact
commit-named Firstmate source under `~/.local/share/firstmate/source/`; that
allows Firstmate's tracked Pi extensions to load. Credentials stay in the new
instance-local Pi home. Do not forward your Mac's SSH agent.

## 7. Launch the persistent flight deck

After `ssh bibi`:

```bash
herdr
```

Inside Herdr, run:

```bash
bibi
```

`bibi` is a small launcher that exports the independent `FM_HOME` and Pi home,
enters the reviewed Firstmate source, and starts Pi. Herdr keeps
the PTY session alive when WezTerm closes or SSH disconnects. Reconnect with
`ssh bibi`, run `herdr`, and reattach.

## Verify the installation

As the daily user, verify the shared JDK 21/Clojure CLI resolution, command
availability, exact global npm CLI versions, pinned public Pi packages, the
official Cloudflare skill source and links, the exact doctl version and root
ownership/mode, the memory guard version/policy/timer, launch admission, and the
bounded swap area,
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
unpinned collection ref is an error. `bibi-verify` reads the owner-only daily-user
receipt. A root-readable mirror supports machine administration:

```bash
cat "$HOME/.local/state/bibi/provisioned-versions"
cat /etc/bibi-provisioned-versions
```

## Update or reconcile the VM

1. Change pins or tasks in this repository. For doctl, review both declared
   architectures and replace both archive checksums. Review exact npm and public
   Pi package versions before changing them. For Cloudflare skills or Pi
   Extensions, review and replace the full commit ref; never provision a moving
   branch. The memory guard's grace periods, per-run cap, launch admission
   floors and reserve, and swap size are
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
   reconcile public/system state. The command carries the receipt's selected
   profiles and backend forward instead of silently expanding a narrow install:

```bash
ssh bibi-admin
sudo /usr/local/sbin/bibi-machine-update
```

For `ubuntu-compat`, the machine update reconciles Wrangler, public Pi packages,
and the official Cloudflare skill checkout for `bibi` without touching
credentials. Narrow profiles reconcile only their selected optional surfaces.

4. Return as `bibi`. If the private collection pin changed—or merely to repair
   its checkout—rerun the authentication-gated daily-user reconciliation:

```bash
bibi-pi-extensions-update
bibi-verify
```

The daily `bibi` account cannot run the machine update command, and the admin
reconciliation intentionally cannot fetch the private collection. Treat changes
to this repository as root-level changes and protect its default branch.

Firstmate also has its own guarded `/updatefirstmate` workflow. If it advances a
clean checkout, Ansible accepts that descendant and does not silently reset it
to the older manifest floor. Review and bump `firstmate_ref` here so clean
rebuilds eventually converge on the promoted commit.

## Memory safety net

An 8 GiB Droplet has run out of memory headroom twice, for opposite reasons.
First, with no swap, browser QA left its helper processes behind: a **leak**,
answered by an ownership-proven reaper. Then, on 2026-09-17, a fleet of
perfectly healthy workers simply outgrew the host and one more launch wedged
it: **no leak at all**, answered by launch admission. This section documents
what the safety net does, what it refuses to do, and how to drive it.

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

### The 2026-09-17 wedge: live workers, no leak

The second incident had nothing to clean. About 15 live, legitimate worker
lanes outgrew this 4-vCPU / 8 GiB host, one more worker was launched, and the
host stopped answering SSH until it was rebooted. `sar` recorded all of it:

| UTC | MemAvailable | Page cache | Swap used | Stalled on memory (`%smem-60`) | System CPU | Load |
| --- | --- | --- | --- | --- | --- | --- |
| 09-15, all day | 2.7 GiB | 1.6 GiB | 57% | 0 | ~15% | ~1 |
| 09-16 06:10 | 2.4 GiB | 1.3 GiB | **89%** | 0 | ~15% | ~1 |
| 09-16 10:10 | 2.5 GiB | 1.3 GiB | **100%** | 0 | ~15% | ~1 |
| 09-17 04:20 | **0.65 GiB** | 0.55 GiB | 100% | 0.8 | ~15% | ~1 |
| 09-17 05:00 | 0.39 GiB | 0.17 GiB | 100% | **15.7** (near miss, recovered) | - | - |
| 09-17 07:30 | 0.73 GiB | 0.48 GiB | 100% | 0.0 | 15% | 1.9 |
| 09-17 07:35 | *a new Claude worker is launched* | | | | | |
| 09-17 07:40 | 0.26 GiB | **0.06 GiB** | 100% | 62.8 | 37% | 17.9 |
| 09-17 08:00 | 0.26 GiB | 0.05 GiB | 100% | 92.9 | **94%** | 39 |
| 09-17 08:50 | 0.28 GiB | 0.09 GiB | 100% | 97.6 | 97% | **85** |

From 07:40 until the reboot the host read 1.5 GiB/s from disk (`pgpgin/s`) and
took 13,000 major faults per second while swapping almost nothing (`pswpin/s`
peaked at 19 and then stayed under 3). That combination is the signature. It
was not swap thrash:

| Link | What it is |
| --- | --- |
| Earliest defensible cause | **Aggregate working set of live workers.** Swap filled on 09-16 and never drained; on 09-17 04:20 MemAvailable fell to 0.65 GiB and commit reached 264%. By the guard's own thresholds the host had been `critical` for more than a day, but those alerts land in journald, the status file, and the login banner, and nothing reads any of them at the one moment they matter - a launch. |
| Triggering launch | The 07:35 worker took the last ~0.5 GiB. It was an ordinary launch; any launch would have done it, and the 05:00 near miss shows an earlier one almost did. |
| Wedge mechanism | With swap full, anonymous memory cannot be evicted, so the only reclaimable pages left were the fleet's own **executable and library text**. The kernel evicted them and the running workers faulted them straight back in, forever. Reclaim kept "succeeding" (`%vmeff` ~140), so the kernel never declared an OOM and never killed anything: a livelock at 97% system CPU, not a crash. |
| Separate cgroup OOM | The Captain Channel was OOM-killed at 08:50 for exceeding **its own** `MemoryMax=256M`. That was 70 minutes into the storm, and the channel had already been failing since 07:40 with timeouts and `database is locked`. It was a victim inside its own cgroup. One unit hitting its own ceiling is not evidence of a host-wide leak, and nothing here treats it as one. |
| Visible symptom | SSH and every pane hung, because `sshd`, shells, and the login banner needed the same evicted pages and the same four CPUs. |

The leak reaper was enabled and behaved correctly: nothing on the host was an
ownership-proven orphan, so it refused to kill anything, exactly as designed.
No amount of cleanup authority would have been safe here, because every
process holding memory was somebody's live, unlanded work. The only lever that
is always safe is the one that was missing: **do not start the next worker.**

### Launch admission

`bibi-memory-guard admit` answers one question from `/proc/meminfo` and
`/proc/pressure/memory`: *is there headroom for one more worker?* It is the same
program, policy file, and status record as the reaper, so there is one memory
monitor on the host, not two. It can refuse a launch. It cannot do anything else:
that code path never walks the process table, never builds a signaller, and
never sends a signal, and the tests assert all three.

A launch is admitted only when **projected** headroom clears a floor:

```text
projected = MemAvailable - reservations of recently admitted workers - reserve for this one
admit  <=>  projected >= floor% of MemTotal   and   PSI some avg60 < stall limit
```

| Setting (`memory_guard_launch_*`) | Default | Why |
| --- | --- | --- |
| `worker_reserve_kb` | 786432 (768 MiB) | One worker's working set. The 07:35 launch took ~0.5 GiB within five minutes and was still growing. |
| `min_mem_available_percent` | 15 | Floor while swap is still a cushion. |
| `min_mem_available_no_swap_percent` | 25 | Floor once swap is absent or past `swap_used_critical_percent` (60). MemAvailable is then mostly the fleet's executable text, and the cliff below it is sudden. |
| `max_memory_stall_percent` | 10 | PSI `some avg60`, the figure `sar -q MEM` calls `%smem-60`. A lagging signal: it was 0.0 at 07:30, so it backs the headroom test rather than replacing it. |
| `reservation_seconds` | 300 | How long an admitted launch keeps its reserve. |
| `gate_enabled` | true | The explicit off switch. |

Swap raises the floor instead of refusing outright, so a host with ample RAM and
old cold pages parked in swap is still admitted.

Replayed over every 10-minute `sar` sample from 09-13 to the reboot, the
reviewed policy admits all 429 samples of 09-13 to 09-15 with `level=warn`
(`swap-used-warn`), never refuses a healthy sample, and refuses every sample
from 09-16 05:50 onward - **26 hours before the wedge, with 2.2 GiB still
available** - including the 07:35 launch, with
`projected-headroom-below-floor,swap-cushion-exhausted`.

FirstMate dispatches in bursts, and a new worker needs minutes to grow into its
working set, so five launches in a minute would all read the same comfortable
MemAvailable. Each admission therefore leaves a short-lived **reservation**
(`<epoch> <kb>` in the invoking user's `$XDG_RUNTIME_DIR/bibi-memory-guard/`)
that later admissions subtract until `/proc` shows the memory for real. The
ledger is an enhancement to the `/proc` facts, never a substitute: if it cannot
be used the decision proceeds on `/proc` alone and reports `ledger=unavailable`.

| Exit | Meaning |
| --- | --- |
| 0 | Admitted. `level` and `reasons` still carry any warning. |
| 75 | Refused for headroom or stall (`EX_TEMPFAIL`): retry after memory is freed. |
| 69 | Refused because `/proc/meminfo` could not be read (`EX_UNAVAILABLE`). |
| 2 | The policy file is invalid. |

**Unavailable telemetry refuses.** A reading with no `MemTotal` looks 100%
available, and admitting on a number nobody measured is how a host gets tipped
over. Refusing a launch loses nothing and can be retried; the scheduled run also
raises `memory-telemetry-unavailable`. A kernel without PSI is not a telemetry
failure: the decision is then made on `/proc/meminfo` alone.

**A refusal never touches running work.** It starts nothing and stops nothing,
and says so. Freeing memory is an operator decision: let workers finish, or
deliberately land or stow one. A closed gate also never widens the reaper's
kill eligibility - the tests run the same fleet under healthy and wedged memory
and require identical verdicts.

#### Binding a launcher to the gate

The gate is a command prefix. It becomes the given command only when it admits:

```bash
# Start a worker only if the host has room for it (reserves its headroom).
bibi-memory-guard admit -- claude "$brief"

# Ask without launching or reserving anything.
bibi-memory-guard admit --no-reserve
```

```text
admission: decision=refuse level=critical mem_total_kb=8131792 mem_available_kb=769520 ... reasons=projected-headroom-below-floor,swap-cushion-exhausted
bibi-memory-guard: launch REFUSED: this host has no safe headroom for another worker.
bibi-memory-guard:   MemAvailable is 751 MB; one more worker is budgeted at 768 MB, leaving 0 MB.
bibi-memory-guard:   The floor is 1985 MB (25% of RAM) because swap is 100% used and is no longer a cushion.
bibi-memory-guard:   Nothing was started and nothing was stopped. This gate never kills a worker.
```

The decision line goes to stderr when a command follows, so stdout belongs to
the launched command. Gate **new** worker launches only. Do not put it in front
of resuming an existing worker, which is how unlanded work gets landed, or in
front of the FirstMate primary, which is how an operator stows work under
pressure. That distinction is why the gate is a prefix for the launcher rather
than a shim around the `claude` or `pi` binaries: only the launcher knows whether
a start is new work. FirstMate is consumed unmodified by this repository, so
this repository installs the gate and proves it; the one-line binding in
FirstMate's worker spawn path is a FirstMate change.

### The reaper's policy

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

Treehouse pool slots are recycled, so several FirstMate tasks can name the same
worktree over time. When more than one claims it, the **most protective** claim
wins, so a finished predecessor is never mistaken for the owner while its
successor is still running there.

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
loudly, kills nothing, and closes launch admission so the pressure at least
stops growing.

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
- **the login banner**, which prints MemAvailable, swap usage, the guard's
  last verdict, and whether launches are `open` or `CLOSED`
- **`bibi-verify`**, which fails when the guard, its policy, its timer, or the
  swap area drifts from the reviewed pins, and warns when admission is closed
- **the launcher itself**: every `admit` prints the current `level` and
  `reasons`, so a launch hears `warn` long before it ever hears `refuse`.
  The scheduled run records the same decision and raises
  `launch-admission-closed`, so a closed gate is visible before anyone tries

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

# Would a new worker be admitted right now? Reserves nothing, starts nothing.
/usr/local/sbin/bibi-memory-guard admit --no-reserve

# The same facts from sysstat, for the history behind a refusal.
sar -r -S -B -q MEM

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

To open launch admission unconditionally, set
`memory_guard_launch_gate_enabled: false` and reconcile; `admit` then always
admits with `reasons=launch-gate-disabled`. Floors and the reserve are bounded
by `tasks/install-memory-guard.yml`, so the gate cannot be tuned into a no-op
by accident - turning it off has to be this explicit switch. A single launch
can always bypass the gate by not using the prefix; that is an operator
decision and is deliberately not automated.

To remove the swap safety net, set `swap_manage_system: false` and reconcile,
then retire the area by hand:

```bash
sudo swapoff /swapfile
sudo sed -i '\|^/swapfile |d' /etc/fstab
sudo rm -f /swapfile /etc/sysctl.d/60-bibi-memory.conf
```

Reconciliation itself is never destructive: applying `site.yml` installs the
guard and runs it only in read-only report mode and as a pure admission query.

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

`admit` is far cheaper, because it has to stay usable on a host that is already
struggling: it reads `/proc/meminfo` and `/proc/pressure/memory`, plus a ledger
of at most 256 short lines, and never touches the process table. It runs
unprivileged in tens of milliseconds. Its ledger lock is non-blocking with a
bounded one-second retry, after which the decision proceeds on `/proc` alone,
so the gate can never hang a launch on its own lock.

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
