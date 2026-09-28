"""Hermetic Ansible tests; fake mise never fetches or runs HEY or touches auth."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HeyCliInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-hey-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.variables = {
            "bibi_agent_home": str(self.home),
            "ansible_architecture": "x86_64",
            "mise_release_base_url": (self.root / "releases").as_uri(),
        }
        self.auth = self.home / ".config/hey/credentials.json"
        self.auth.parent.mkdir(parents=True)
        self.auth.write_text("synthetic-auth-sentinel\n")
        # Neither this config nor any project hook may be loaded by provisioning.
        (self.home / "mise.toml").write_text('[hooks]\npostinstall = "exit 99"\n')
        self.make_release()

    def make_release(self, mise="2026.9.15", hey="1.7.0", refuse=False, wrong=False):
        self.variables.update(mise_version=mise, hey_cli_version=hey)
        script = f'''#!/usr/bin/python3
import os, pathlib, sys
assert os.environ.get("MISE_NO_CONFIG") == "1"
if sys.argv[1:] == ["--version"]:
    print("{mise} fixture")
    sys.exit(0)
assert sys.argv[1] == "install-into"
assert os.environ.get("MISE_NO_HOOKS") == "1"
assert os.environ.get("MISE_PARANOID") == "1"
assert "github_attestations=false" not in sys.argv[2]
assert sys.argv[2].startswith("github:basecamp/hey-cli[asset_pattern=hey_{hey}_linux_")
assert ",checksum=sha256:" + "a" * 64 + "]@{hey}" in sys.argv[2]
root = pathlib.Path(sys.argv[3]).parent
assert pathlib.Path(os.environ["MISE_DATA_DIR"]) == root / "data"
assert pathlib.Path(os.environ["MISE_CACHE_DIR"]) == root / "cache"
assert pathlib.Path(os.environ["MISE_STATE_DIR"]) == root / "state"
with (root / "install-calls").open("a") as log:
    log.write("install\\n")
if {refuse!r}:
    sys.exit("required GitHub attestation unavailable (fixture)")
target = pathlib.Path(sys.argv[3])
target.mkdir(parents=True, exist_ok=True)
binary = target / "hey"
binary.write_text('#!/bin/sh\\n[ "$#" = 1 ] && [ "$1" = --version ] || exit 99\\nprintf "hey version {"0.0.0" if wrong else hey}\\\\n"\\n')
binary.chmod(0o755)
'''
        directory = self.root / "releases" / f"v{mise}"
        directory.mkdir(parents=True, exist_ok=True)
        self.variables["mise_archives"] = {}
        self.variables["hey_cli_archives"] = {}
        for architecture, mise_arch, hey_arch in [
            ("x86_64", "x64", "amd64"), ("aarch64", "arm64", "arm64")
        ]:
            target = directory / f"mise-v{mise}-linux-{mise_arch}"
            target.write_text(script)
            self.variables["mise_archives"][architecture] = {
                "arch": mise_arch,
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
            self.variables["hey_cli_archives"][architecture] = {
                "arch": hey_arch, "sha256": "a" * 64,
            }

    def run_installer(self, success=True, check=False):
        variables = self.root / "vars.json"
        variables.write_text(json.dumps(self.variables))
        command = [
            "ansible-playbook", "-i", "localhost,",
            str(ROOT / "tests/fixtures/hey-cli-install.yml"),
            "--extra-vars", f"@{variables}",
        ]
        if check:
            command.append("--check")
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True,
                                env={**os.environ, "ANSIBLE_NOCOLOR": "1"}, timeout=90)
        log = result.stdout + result.stderr
        self.assertEqual(result.returncode == 0, success, log[-8000:])
        self.assertEqual(self.auth.read_text(), "synthetic-auth-sentinel\n")
        self.assertFalse((self.home / ".profile").exists())
        self.assertFalse((self.home / ".config/mise").exists())
        return log

    def test_install_rerun_and_upgrade_both_architectures(self):
        for architecture in ("x86_64", "aarch64"):
            with self.subTest(architecture=architecture):
                self.variables["ansible_architecture"] = architecture
                self.run_installer()
                link = self.home / ".local/bin/hey"
                self.assertTrue(link.is_symlink())
                self.assertIn(self.variables["hey_cli_archives"][architecture]["arch"], str(link.resolve()))
                self.assertIn("changed=0", self.run_installer())
        self.make_release(mise="2026.9.16", hey="1.7.1")
        self.run_installer()
        self.assertIn("1.7.1", str((self.home / ".local/bin/hey").resolve()))
        self.assertIn("2026.9.16", str((self.home / ".local/bin/mise").resolve()))
        # No network or repeat install is needed when the pins already exist.
        import shutil
        shutil.rmtree(self.root / "releases")
        self.assertIn("changed=0", self.run_installer())
        calls = self.home / ".local/share/bibi/hey-cli/install-calls"
        self.assertEqual(calls.read_text().splitlines(), ["install"] * 3)

    def test_cached_binary_requires_matching_verification_receipt(self):
        self.run_installer()
        binary = (self.home / ".local/bin/hey").resolve()
        receipt = binary.parent / ".bibi-verified.json"
        original = receipt.read_bytes()
        receipt.unlink()
        self.assertIn("Unverified or interrupted", self.run_installer(success=False))
        receipt.write_bytes(original)
        binary.write_text("#!/bin/sh\nexit 99\n")
        self.assertIn("Verify cached HEY binary integrity", self.run_installer(success=False))

    def test_required_provenance_failure_never_exposes_hey(self):
        self.make_release(refuse=True)
        self.assertIn("required GitHub attestation unavailable", self.run_installer(success=False))
        self.assertFalse((self.home / ".local/bin/hey").exists())
        self.assertTrue((self.home / ".local/bin/mise").is_symlink())

    def test_unexpected_hey_version_never_exposes_hey(self):
        self.make_release(wrong=True)
        self.run_installer(success=False)
        self.assertFalse((self.home / ".local/bin/hey").exists())

    def test_mise_checksum_mismatch_never_exposes_a_command(self):
        self.variables["mise_archives"]["x86_64"]["sha256"] = "0" * 64
        self.run_installer(success=False)
        self.assertFalse((self.home / ".local/bin/mise").exists())
        self.assertFalse((self.home / ".local/bin/hey").exists())

    def test_unrelated_commands_are_preserved(self):
        directory = self.home / ".local/bin"
        directory.mkdir(parents=True)
        for name in ("mise", "hey"):
            target = directory / name
            target.write_text("unrelated executable\n")
            self.run_installer(success=False)
            self.assertEqual(target.read_text(), "unrelated executable\n")
            target.unlink()

    def test_redirected_install_root_is_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.home / ".local").symlink_to(outside, target_is_directory=True)
        self.run_installer(success=False)
        self.assertEqual(list(outside.iterdir()), [])

    def test_unknown_architecture_is_refused(self):
        self.variables["ansible_architecture"] = "riscv64"
        self.run_installer(success=False)
        self.assertFalse((self.home / ".local").exists())

    def test_check_mode_does_not_install_or_claim_a_binary(self):
        self.run_installer(check=True)
        self.assertFalse((self.home / ".local").exists())


if __name__ == "__main__":
    unittest.main()
