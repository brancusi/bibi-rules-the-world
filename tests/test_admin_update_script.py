"""The saved administrator update script, with fake ssh and curl only.

Nothing here reaches a server, runs sudo, or downloads anything.
"""

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/bibi-admin-update.sh"
RECORDER = ROOT / "scripts/bibi_record_update.py"
INSTALLED = ("if grep -qs -- bibi-record-update /usr/local/sbin/bibi-machine-update; "
             "then exec /usr/local/sbin/bibi-machine-update; fi")


def readme_bootstrap():
    line = next(line for line in (ROOT / "README.md").read_text().splitlines()
                if line.startswith("ssh -t bibi-admin 'sudo bash -c "))
    return shlex.split(shlex.split(line)[3])[3]


class AdminUpdateScriptTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.fake = self.root / "bin"
        self.fake.mkdir()
        self.log = self.root / "ssh.json"
        stub = self.fake / "ssh"
        stub.write_text("#!/usr/bin/python3\nimport json, os, sys\n"
                        "open(os.environ['FIXTURE_LOG'], 'w').write(json.dumps(sys.argv[1:]))\n"
                        "sys.exit(int(os.environ.get('FIXTURE_EXIT', '0')))\n")
        stub.chmod(0o755)

    def run_script(self, *args, exit_code=0):
        env = {**os.environ, "FIXTURE_LOG": str(self.log), "FIXTURE_EXIT": str(exit_code),
               "PATH": str(self.fake) + os.pathsep + os.environ["PATH"]}
        result = subprocess.run(["bash", str(SCRIPT), *args], env=env, text=True,
                                capture_output=True, timeout=10)
        return result, (json.loads(self.log.read_text()) if self.log.exists() else None)

    def remote(self):
        result, argv = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(argv[:2], ["-t", "bibi-admin"])
        self.assertEqual(len(argv), 3)
        # The administrator's login shell parses the one remote argument.
        command = shlex.split(argv[2])
        self.assertEqual(command[:3], ["sudo", "bash", "-c"])
        self.assertEqual(len(command), 4)
        return command[3]

    def test_runs_the_documented_pinned_bootstrap(self):
        remote = self.remote()
        bootstrap = readme_bootstrap()
        self.assertEqual(remote, f"{INSTALLED}; {bootstrap}")
        # The pinned recorder is still the repository's recorder.
        self.assertIn(hashlib.sha256(RECORDER.read_bytes()).hexdigest(), bootstrap)
        subprocess.run(["bash", "-n", "-c", remote], check=True)

    def test_update_exit_status_is_the_scripts(self):
        for code in (0, 2, 74):
            with self.subTest(code=code):
                result, argv = self.run_script(exit_code=code)
                self.assertEqual(result.returncode, code)
                self.assertIsNotNone(argv)

    def test_arguments_are_refused_before_ssh(self):
        result, argv = self.run_script("--now")
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(argv)

    def server(self, wrapper_text, recorder_bytes):
        """Run the remote command against fixture paths with a fake curl."""
        sbin = self.root / "sbin"
        sbin.mkdir(exist_ok=True)
        marker = self.root / "wrapper-ran"
        wrapper = sbin / "bibi-machine-update"
        wrapper.write_text(f"#!/bin/sh\n# {wrapper_text}\ntouch {marker}\nexit 5\n")
        wrapper.chmod(0o755)
        source = self.root / "served-recorder"
        source.write_bytes(recorder_bytes)
        curl = self.fake / "curl"
        curl.write_text("#!/bin/sh\nwhile [ $# -gt 1 ]; do [ \"$1\" = -o ] && "
                        f"cp {source} \"$2\"; shift; done\n")
        curl.chmod(0o755)
        remote = self.remote()
        for old, new in (("/usr/local/sbin/", f"{sbin}/"), ("/root/", f"{self.root}/")):
            self.assertIn(old, remote)
            remote = remote.replace(old, new)
        env = {**os.environ, "PATH": str(self.fake) + os.pathsep + os.environ["PATH"]}
        result = subprocess.run(["bash", "-c", remote], env=env, text=True,
                                capture_output=True, timeout=20)
        return result, marker.exists()

    def test_installed_recorded_wrapper_runs_without_the_bootstrap(self):
        result, ran = self.server("exec /usr/local/sbin/bibi-record-update -- x", b"unused")
        self.assertTrue(ran)
        self.assertEqual(result.returncode, 5)
        self.assertEqual(list(self.root.glob("bibi-update-bootstrap.*")), [])

    def test_old_wrapper_runs_only_through_the_verified_recorder(self):
        result, ran = self.server("exec ansible-pull", b"print('tampered')\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(ran, "a recorder failing its checksum must not start the update")
        if os.geteuid() == 0:
            self.skipTest("as root the verified recorder would record to the real /var/lib")
        result, ran = self.server("exec ansible-pull", RECORDER.read_bytes())
        # The verified recorder starts, and as a non-root test it refuses to
        # record, so the old wrapper is still never run directly.
        self.assertEqual(result.returncode, 1)
        self.assertIn("Run through sudo", result.stderr)
        self.assertFalse(ran)


if __name__ == "__main__":
    unittest.main()
