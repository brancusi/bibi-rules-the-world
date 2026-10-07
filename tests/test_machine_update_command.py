"""Render the real update command and receipt; never run the provisioning play.

The site.yml tasks render through the real Ansible template action into a
temporary directory as the current user. The rendered command then runs with a
fake ansible-pull that records its argv, so no host is reconciled.
"""

import json
import os
from pathlib import Path
import pwd
import site
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMMAND_TASK = "Install the recorded administrator update implementation"
RECEIPT_TASK = "Record the root-readable provisioned versions"
REPO = "https://github.com/brancusi/bibi-rules-the-world.git"

# The 2026-08-28 receipt still on the managed Droplet, written before profiles
# existed. It is the format the first recorded update must accept.
LEGACY_RECEIPT = f"""\
configuration_repo={REPO}
configuration_ref=main
firstmate_repo=https://github.com/kunchenguid/firstmate.git
firstmate_ref=673b6ad39d01183a19c5bd8a52675a2ee8ea06e6
node_major=24
shared_toolchain_root=/opt/bibi/toolchains
jdk_version=21.0.12+8
jdk_runtime_version=21.0.12+8-LTS
jdk_home=/opt/bibi/toolchains/jdk-21
clojure_cli_version=1.12.4.1618
clojure_home=/opt/bibi/toolchains/clojure
pi_version=0.83.0
pi_package=@earendil-works/pi-coding-agent@0.83.0
wrangler_package=wrangler@4.125.0
doctl_version=1.166.0
doctl_install_path=/usr/local/bin/doctl
doctl_install_owner=root
doctl_install_group=root
doctl_install_mode=0555
pi_extensions_repo=https://github.com/brancusi/pi-extensions.git
pi_extensions_ref=c66061fd372ad36e4ffbf61c41fb92dd1d2d9152
pi_extensions_package_version=1.5.0
pi_extensions_source=https://github.com/brancusi/pi-extensions.git@c66061fd372ad36e4ffbf61c41fb92dd1d2d9152
pi_public_packages=npm:@tmustier/pi-files-widget@0.2.0 npm:pi-web-access@0.24.0
cloudflare_skills_repo=https://github.com/cloudflare/skills.git
cloudflare_skills_ref=f96bff754e428838818017f75817f0f9428acd48
cloudflare_skills_checkout=/home/bibi/.local/share/bibi/cloudflare-skills
cloudflare_skill_link_dir=/home/bibi/.pi/agent/skills
cloudflare_skill_names=cloudflare wrangler
firecrawl_cli_package=firecrawl-cli@1.19.27
no_mistakes_version=1.40.0
axi_packages=gh-axi@0.1.30 chrome-devtools-axi@0.1.27 lavish-axi@0.1.50 tasks-axi@0.2.5 quota-axi@0.1.29

memory_guard_version=1.0.0
memory_guard_install_path=/usr/local/sbin/bibi-memory-guard
swap_file_path=/swapfile
"""


def site_task(name, dest, mode):
    tasks = yaml.safe_load((ROOT / "site.yml").read_text())[0]["tasks"]
    matches = [task for task in tasks if task.get("name") == name]
    assert len(matches) == 1, name
    task = matches[0]
    module = task["ansible.builtin.template"]
    module["src"] = str(ROOT / module["src"])
    user = pwd.getpwuid(os.getuid()).pw_name
    # Only the destination and its root ownership move into the fixture.
    module.update(dest=str(dest), owner=user, group=user, mode=mode)
    return task


class MachineUpdateCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix=".test-update-command-", dir=ROOT)
        cls.root = Path(cls.temp.name)
        cls.command = cls.root / "bibi-machine-update-command"
        cls.current = cls.root / "current-receipt"
        play = cls.root / "render.yml"
        play.write_text(yaml.safe_dump([{
            "name": "Isolated update command and receipt render",
            "hosts": "all", "connection": "local", "become": False,
            "gather_facts": False,
            "vars_files": [str(ROOT / "group_vars/all.yml")],
            "vars": {"ansible_python_interpreter": "/usr/bin/python3"},
            "tasks": [site_task(COMMAND_TASK, cls.command, "0755"),
                      site_task(RECEIPT_TASK, cls.current, "0644")],
        }]))
        env = {**os.environ, "ANSIBLE_NOCOLOR": "1",
               "PYTHONUSERBASE": site.getuserbase(),
               "ANSIBLE_LOCAL_TEMP": str(cls.root / "ansible-local"),
               "ANSIBLE_REMOTE_TEMP": str(cls.root / "ansible-remote")}
        # A non-default selection, passed the way the command passes it, proves
        # the receipt round-trips it.
        selection = json.dumps({"bibi_profiles": ["base", "cloudflare"], "bibi_backend": "tmux"})
        result = subprocess.run(["ansible-playbook", "-i", "localhost,", "-e", selection, str(play)],
                                cwd=ROOT, env=env, text=True, capture_output=True, timeout=90)
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)

        # Run as the current user against a fixture receipt: exactly one root
        # check and one receipt path are replaced, so any change in either
        # shape fails here instead of being silently skipped.
        rendered = cls.command.read_text()
        cls.receipt = cls.root / "receipt"
        for old, new in (("if [[ ${EUID} -ne 0 ]]; then", "if false; then"),
                         ("receipt=/etc/bibi-provisioned-versions", f"receipt={cls.receipt}")):
            assert rendered.count(old) == 1, old
            rendered = rendered.replace(old, new)
        cls.runnable = cls.root / "runnable-command"
        cls.runnable.write_text(rendered)
        cls.fake = cls.root / "fake-bin"
        cls.fake.mkdir()
        stub = cls.fake / "ansible-pull"
        stub.write_text("#!/usr/bin/python3\nimport json, os, sys\n"
                        "open(os.environ['FIXTURE_LOG'], 'w').write(json.dumps(sys.argv[1:]))\n")
        stub.chmod(0o755)
        cls.log = cls.root / "ansible-pull.json"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_command(self, receipt_text):
        if self.receipt.is_symlink() or self.receipt.exists():
            self.receipt.unlink()
        if receipt_text is not None:
            self.receipt.write_text(receipt_text)
        return self.invoke()

    def invoke(self):
        if self.log.exists():
            self.log.unlink()
        env = {**os.environ, "FIXTURE_LOG": str(self.log),
               "PATH": str(self.fake) + os.pathsep + os.environ["PATH"]}
        result = subprocess.run(["bash", str(self.runnable)], env=env, text=True,
                                capture_output=True, timeout=10)
        argv = json.loads(self.log.read_text()) if self.log.exists() else None
        return result, argv

    def pull(self, *extra):
        return ["--url", REPO, "--checkout", "main", "--inventory", "localhost,",
                "--connection", "local", *extra, "site.yml"]

    def test_rendered_command_is_shell_valid(self):
        subprocess.run(["bash", "-n", str(self.command)], check=True)
        self.assertNotIn("{{", self.command.read_text())
        self.assertIn('if [[ ${EUID} -ne 0 ]]', self.command.read_text())
        self.assertIn("receipt=/etc/bibi-provisioned-versions", self.command.read_text())

    def test_legacy_receipt_applies_repository_defaults(self):
        # Before this fix the command exited 1 with no message here, so a run
        # that failed after installing it locked every later update out.
        result, argv = self.run_command(LEGACY_RECEIPT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(argv, self.pull())
        self.assertIn("Legacy machine receipt", result.stderr)

    def test_current_receipt_carries_its_selection(self):
        current = self.current.read_text()
        self.assertIn("firstmate_home=", current)
        result, argv = self.run_command(current)
        self.assertEqual(result.returncode, 0, result.stderr)
        selection = {"bibi_profiles": ["base", "cloudflare"], "bibi_backend": "tmux"}
        self.assertEqual(argv[:-3], self.pull()[:-1])
        self.assertEqual(argv[-3], "--extra-vars")
        self.assertEqual(json.loads(argv[-2]), selection)
        self.assertEqual(argv[-1], "site.yml")
        self.assertEqual(result.stderr, "")

    def test_invalid_receipts_are_refused_before_ansible(self):
        current = self.current.read_text()

        def without(text, key):
            kept = [line for line in text.splitlines(True) if not line.startswith(key + "=")]
            self.assertLess(len(kept), len(text.splitlines(True)), key)
            return "".join(kept)

        def replaced(text, key, value):
            return "".join(f"{key}={value}\n" if line.startswith(key + "=") else line
                           for line in text.splitlines(True))

        cases = {
            # A current receipt is never completed from defaults.
            "current without profiles": (without(current, "profiles"), "record profiles exactly once"),
            "current without backend": (without(current, "backend"), "record backend exactly once"),
            "current with only firstmate_home": (
                without(without(current, "profiles"), "backend"), "record profiles exactly once"),
            "legacy plus backend only": (LEGACY_RECEIPT + "backend=herdr\n", "record profiles exactly once"),
            "duplicate profiles": (current + "profiles=ubuntu-compat\n", "record profiles exactly once"),
            "duplicate backend": (current + "backend=herdr\n", "record backend exactly once"),
            "unknown backend": (replaced(current, "backend", "screen"), "Invalid backend"),
            "empty backend": (replaced(current, "backend", ""), "Invalid backend"),
            "malformed profiles": (replaced(current, "profiles", "Ubuntu Compat"), "Invalid profiles"),
            "empty profiles": (replaced(current, "profiles", ""), "Invalid profiles"),
            "trailing comma": (replaced(current, "profiles", "base,"), "Invalid profiles"),
            "empty receipt": ("", "Unrecognised machine receipt"),
            "foreign file": ("hello=world\n", "Unrecognised machine receipt"),
            "legacy without firstmate_ref": (without(LEGACY_RECEIPT, "firstmate_ref"),
                                             "Unrecognised machine receipt"),
            "duplicate legacy repository": (LEGACY_RECEIPT + f"configuration_repo={REPO}\n",
                                            "Unrecognised machine receipt"),
            "legacy for another repository": (
                replaced(LEGACY_RECEIPT, "configuration_repo", "https://example.invalid/other.git"),
                "names another configuration repository"),
        }
        for name, (text, error) in cases.items():
            with self.subTest(name):
                result, argv = self.run_command(text)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(error, result.stderr)
                self.assertIsNone(argv, "ansible-pull must not start")

        target = self.root / "real-receipt"
        target.write_text(LEGACY_RECEIPT)
        for name in ("missing", "symlink"):
            with self.subTest(name):
                self.run_command(None)
                if name == "symlink":
                    self.receipt.symlink_to(target)
                result, argv = self.invoke()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("Missing direct machine receipt", result.stderr)
                self.assertIsNone(argv)

if __name__ == "__main__":
    unittest.main()
