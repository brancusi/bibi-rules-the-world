"""Hermetic recorder tests: no root, live updater, or host destinations."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/bibi_record_update.py"
spec = importlib.util.spec_from_file_location("recorder", SCRIPT)
recorder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recorder)
ROW = "localhost : ok=3 changed=2 unreachable=0 failed=0 skipped=1 rescued=0 ignored=0\n"
RECAP = "PLAY RECAP *********************************************************************\n" + ROW


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.private = self.root / "private"
        self.shared = self.root / "shared"
        self.private.mkdir(mode=0o700)
        self.shared.mkdir(mode=0o755)
        self.addCleanup(self.temp.cleanup)

    def start(self, code):
        # Invoke the API using explicitly supplied test-only dirfds. The real CLI
        # has no environment/path override and requires root for fixed /var paths.
        harness = (
            "import importlib.util,os,sys; "
            f"s=importlib.util.spec_from_file_location('r',{str(SCRIPT)!r}); "
            "r=importlib.util.module_from_spec(s); s.loader.exec_module(r); "
            f"p=os.open({str(self.private)!r},r.DIRECTORY_FLAGS); "
            f"q=os.open({str(self.shared)!r},r.DIRECTORY_FLAGS); "
            f"sys.exit(r.record_run([sys.executable,'-c',{code!r}],p,q))"
        )
        return subprocess.Popen([sys.executable, "-c", harness], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)

    def finish(self, code):
        child = self.start(code)
        output, errors = child.communicate(timeout=10)
        self.assertEqual(errors, b"")
        records = list(self.shared.glob("*.json"))
        record = json.loads(max(records, key=lambda p: p.stat().st_mtime_ns).read_text())
        return child.returncode, record, output

    def test_success_privacy_and_preservation(self):
        code = f"print('PRIVATE_TOKEN=fixture-secret'); print({RECAP!r},end='')"
        result, record, output = self.finish(code)
        self.assertEqual(result, 0)
        self.assertEqual(record["updater_exit"], 0)
        self.assertEqual(record["state"], "completed")
        self.assertEqual(record["recap"]["changed"], 2)
        self.assertTrue(record["log_complete"])
        self.assertIn(b"fixture-secret", output)
        log = next(self.private.glob("*.log"))
        self.assertIn("fixture-secret", log.read_text())
        summary = next(self.shared.glob("*.json"))
        before = summary.read_bytes()
        self.assertNotIn(b"fixture-secret", before)
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(summary.stat().st_mode), 0o644)
        self.finish("print('second')")
        self.assertEqual(len(list(self.shared.glob("*.json"))), 2)
        self.assertEqual(summary.read_bytes(), before)

    def test_closed_terminal_does_not_lose_log_or_exit(self):
        child = self.start("import time; time.sleep(0.1); print('retained output'); raise SystemExit(7)")
        child.stdout.close()
        self.assertEqual(child.wait(timeout=5), 7)
        self.assertEqual(child.stderr.read(), b"")
        child.stderr.close()
        self.assertIn("retained output", next(self.private.glob("*.log")).read_text())
        record = json.loads(next(self.shared.glob("*.json")).read_text())
        self.assertEqual(record["updater_exit"], 7)

    def test_failure_before_after_and_missing_recap(self):
        for code, expected, has_recap in [
            ("import sys; print('raw secret error'); sys.exit(9)", 9, False),
            (f"import sys; print({RECAP!r}); sys.exit(4)", 4, True),
            ("print('no recap')", 0, False),
        ]:
            with self.subTest(code=code):
                result, record, _ = self.finish(code)
                self.assertEqual(result, expected)
                self.assertEqual(record["updater_exit"], expected)
                self.assertEqual(record["recap"] is not None, has_recap)
                self.assertNotIn("raw secret", json.dumps(record))

    def wait_started(self, child):
        for _ in range(150):
            files = list(self.shared.glob("*.json"))
            if files:
                record = json.loads(files[0].read_text())
                if record["state"] == "started":
                    return files[0]
            if child.poll() is not None:
                self.fail("recorder exited before started record")
            time.sleep(0.02)
        self.fail("missing durable started record")

    def test_interruptions(self):
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=signum):
                marker = self.root / "child-ready"
                child = self.start(f"from pathlib import Path; import time; Path({str(marker)!r}).touch(); time.sleep(60)")
                record_path = self.wait_started(child)
                for _ in range(150):
                    if marker.exists():
                        break
                    time.sleep(0.02)
                self.assertTrue(marker.exists())
                child.send_signal(signum)
                child.communicate(timeout=10)
                record = json.loads(record_path.read_text())
                self.assertEqual(child.returncode, 128 + signum)
                self.assertEqual(record["state"], "interrupted")
                self.assertEqual(record["signal"], signum)
                self.assertIsNone(record["recap"])
                record_path.unlink()
                marker.unlink()

    def test_sigkill_leaves_started_not_success(self):
        # Child exits on its own soon; killing only this fixture recorder cannot
        # strand a worker or touch an actual updater.
        child = self.start("import time; time.sleep(0.3)")
        path = self.wait_started(child)
        child.kill()
        child.communicate(timeout=5)
        record = json.loads(path.read_text())
        self.assertEqual(record["state"], "started")
        self.assertIsNone(record["finished_at"])
        self.assertIsNone(record["updater_exit"])

    def test_untrusted_recap_is_bounded(self):
        parser = recorder.Recap()
        parser.feed(b"x" * 200000)
        self.assertLessEqual(len(parser.buffer), 4096)
        parser.feed(b"\n")
        for row in (ROW.replace("localhost", "secret-host"),
                    ROW.replace("ok=3", "ok=999999999999999"), ROW.rstrip() + " PRIVATE=secret\n"):
            parser.feed(b"PLAY RECAP *****\n" + row.encode())
            self.assertIsNone(parser.counts)
        # A valid row is permitted but never arbitrary suffixes or host labels.
        parser.feed(b"PLAY RECAP *****\nsecret-host : SECRET=value\n")
        self.assertIsNone(parser.counts)
        parser.feed(RECAP.encode())
        self.assertEqual(parser.counts["ok"], 3)

    def test_refuse_untrusted_destinations(self):
        fd = os.open(self.root, recorder.DIRECTORY_FLAGS)
        self.addCleanup(os.close, fd)
        (self.root / "link").symlink_to(self.private)
        with self.assertRaises(OSError):
            recorder.checked_directory(fd, "link", 0o700, os.getuid())
        self.private.chmod(0o777)
        with self.assertRaises(PermissionError):
            recorder.checked_directory(fd, "private", 0o700, os.getuid())
        self.private.chmod(0o755)
        with self.assertRaises(PermissionError):
            recorder.checked_directory(fd, "private", 0o700, os.getuid())
        with self.assertRaises(PermissionError):
            recorder.checked_directory(fd, "shared", 0o755, os.getuid() + 1)
        good = recorder.checked_directory(fd, "shared", 0o755, os.getuid())
        os.close(good)

    def test_initial_summary_failure_prevents_launch(self):
        private = os.open(self.private, recorder.DIRECTORY_FLAGS)
        shared = os.open(self.shared, recorder.DIRECTORY_FLAGS)
        self.addCleanup(os.close, private)
        self.addCleanup(os.close, shared)
        with mock.patch.object(recorder, "publish", side_effect=PermissionError), \
                mock.patch.object(recorder.subprocess, "Popen") as launch:
            with self.assertRaises(PermissionError):
                recorder.record_run(["not-run"], private, shared)
            launch.assert_not_called()

    def test_final_summary_failure_preserves_updater_failure(self):
        private = os.open(self.private, recorder.DIRECTORY_FLAGS)
        shared = os.open(self.shared, recorder.DIRECTORY_FLAGS)
        self.addCleanup(os.close, private)
        self.addCleanup(os.close, shared)
        publish = recorder.publish
        for child_exit, expected in ((5, 5), (0, 74)):
            calls = 0

            def fail_final(*args):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise PermissionError
                return publish(*args)

            with mock.patch.object(recorder, "publish", side_effect=fail_final):
                self.assertEqual(recorder.record_run(
                    [sys.executable, "-c", f"raise SystemExit({child_exit})"], private, shared), expected)
        for path in self.shared.glob("*.json"):
            self.assertEqual(json.loads(path.read_text())["state"], "started")

    def test_private_log_failure_cannot_report_success(self):
        private = os.open(self.private, recorder.DIRECTORY_FLAGS)
        shared = os.open(self.shared, recorder.DIRECTORY_FLAGS)
        self.addCleanup(os.close, private)
        self.addCleanup(os.close, shared)
        with mock.patch.object(recorder.os, "write", side_effect=OSError):
            result = recorder.record_run([sys.executable, "-c", "print('fixture')"], private, shared)
        self.assertEqual(result, 74)
        record = json.loads(next(self.shared.glob("*.json")).read_text())
        self.assertFalse(record["log_complete"])
        self.assertEqual(record["updater_exit"], 0)

    def test_nonroot_cli_never_opens_host_paths(self):
        with mock.patch.object(recorder.os, "geteuid", return_value=1001), \
                mock.patch.object(recorder, "recording_directory") as directory:
            self.assertEqual(recorder.main(), 1)
            directory.assert_not_called()

    def test_installed_wrapper_records_before_receipt_validation(self):
        wrapper = (ROOT / "templates/bibi-machine-update.j2").read_text()
        command = (ROOT / "templates/bibi-machine-update-command.j2").read_text()
        site = (ROOT / "site.yml").read_text()
        self.assertIn("exec /usr/local/sbin/bibi-record-update -- /usr/local/sbin/bibi-machine-update-command", wrapper)
        self.assertNotIn("receipt=", wrapper)
        self.assertIn("receipt=/etc/bibi-provisioned-versions", command)
        self.assertIn('exec ansible-pull', command)
        self.assertLess(site.index('src: scripts/bibi_record_update.py'), site.index('src: templates/bibi-machine-update.j2'))
        self.assertLess(site.index('src: templates/bibi-machine-update-command.j2'), site.index('src: templates/bibi-machine-update.j2'))

    def test_missing_command_is_launch_failure(self):
        private = os.open(self.private, recorder.DIRECTORY_FLAGS)
        shared = os.open(self.shared, recorder.DIRECTORY_FLAGS)
        try:
            self.assertEqual(recorder.record_run([str(self.root / "absent")], private, shared), 127)
        finally:
            os.close(private)
            os.close(shared)
        record = json.loads(next(self.shared.glob("*.json")).read_text())
        self.assertEqual(record["state"], "launch_failed")
        self.assertIsNone(record["updater_exit"])

    def test_documented_bootstrap_is_pinned_and_shell_valid(self):
        line = next(line for line in (ROOT / 'README.md').read_text().splitlines()
                    if line.startswith("ssh -t bibi-admin 'sudo bash -c "))
        outer = shlex.split(line)
        self.assertEqual(outer[:3], ['ssh', '-t', 'bibi-admin'])
        inner = shlex.split(outer[3])
        self.assertEqual(inner[:3], ['sudo', 'bash', '-c'])
        script = inner[3]
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertIn(hashlib.sha256(SCRIPT.read_bytes()).hexdigest(), script)
        self.assertIn('/cc8ffd368149ab96cb3e7a08cb200dcf3440e90b/scripts/bibi_record_update.py', script)
        self.assertLess(script.index('sha256sum --check --status'), script.index('exec /usr/bin/python3'))
        self.assertIn('-- /usr/local/sbin/bibi-machine-update', script)
        self.assertIn('mktemp -d /root/', script)
        self.assertIn('umask 077', script)

    def test_bootstrap_records_old_wrapper_without_install(self):
        old = self.root / "old-wrapper"
        old.write_text("#!/bin/sh\nprintf 'legacy update output\\n'\nexit 6\n")
        old.chmod(0o700)
        private = os.open(self.private, recorder.DIRECTORY_FLAGS)
        shared = os.open(self.shared, recorder.DIRECTORY_FLAGS)
        try:
            # Same recorder -- old-wrapper flow as the documented bootstrap.
            self.assertEqual(recorder.record_run([str(old)], private, shared), 6)
        finally:
            os.close(private)
            os.close(shared)
        record = json.loads(next(self.shared.glob("*.json")).read_text())
        self.assertEqual(record["updater_exit"], 6)
        self.assertIsNone(record["recap"])
        self.assertIn("legacy update output", next(self.private.glob("*.log")).read_text())


if __name__ == "__main__":
    unittest.main()
