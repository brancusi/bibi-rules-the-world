"""Hermetic browser ownership/lifetime tests. Not a real Chrome smoke receipt."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('browser', ROOT / 'scripts/bibi_browser.py')
browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser)


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.config = {'executable': '/opt/google/chrome/chrome', 'version': '153.0.8010.52-1',
                       'axi': '/missing/axi', 'axi_version': '0.1.31',
                       'mcp': '/missing/mcp', 'mcp_version': '1.9.0'}

    def tearDown(self):
        self.tmp.cleanup()

    def run_object(self):
        return browser.BrowserRun(self.config, self.home)

    def metadata(self):
        for key, name in [('axi', 'chrome-devtools-axi'), ('mcp', 'chrome-devtools-mcp')]:
            root = self.home / name
            root.mkdir()
            (root / 'package.json').write_text(json.dumps({'name': name, 'version': self.config[key + '_version']}))
            self.config[key] = str(root / 'command.js')

    def test_explicit_executable_and_sandbox(self):
        args = browser.chrome_args(self.config['executable'], self.home / 'private')
        self.assertEqual(args[0], '/opt/google/chrome/chrome')
        self.assertIn('--remote-debugging-address=127.0.0.1', args)
        self.assertIn('--remote-debugging-port=0', args)
        self.assertFalse(any('no-sandbox' in arg or 'disable-setuid-sandbox' in arg for arg in args))
        self.assertFalse(any('playwright' in arg for arg in args))

    def test_missing_browser_fails_before_any_launch(self):
        run = self.run_object()
        with patch.object(browser.os, 'access', return_value=False), patch.object(browser.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(browser.BrowserError, 'Missing official'):
                run.start()
            launch.assert_not_called()
        run.cleanup()

    def test_foreign_executable_refused(self):
        self.config['executable'] = '/home/user/.cache/ms-playwright/chrome'
        with self.assertRaisesRegex(browser.BrowserError, 'No cache fallback'):
            self.run_object().start()

    def test_ambient_personal_connection_and_flags_removed(self):
        with patch.dict(os.environ, {'CHROME_DEVTOOLS_AXI_AUTO_CONNECT': '1',
                                    'CHROME_DEVTOOLS_AXI_CHROME_ARGS': '--no-sandbox',
                                    'CHROME_DEVTOOLS_AXI_USER_DATA_DIR': '/personal',
                                    'CHROME_DEVTOOLS_AXI_WS_HEADERS': 'private',
                                    'CHROME_DEVEL_SANDBOX': '/random/helper'}):
            env = browser.child_environment('test', 4321, 'http://127.0.0.1:4322', '/reviewed/mcp')
        self.assertNotIn('CHROME_DEVTOOLS_AXI_AUTO_CONNECT', env)
        self.assertNotIn('CHROME_DEVTOOLS_AXI_CHROME_ARGS', env)
        self.assertNotIn('CHROME_DEVTOOLS_AXI_WS_HEADERS', env)
        self.assertNotIn('CHROME_DEVTOOLS_AXI_USER_DATA_DIR', env)
        self.assertNotIn('CHROME_DEVEL_SANDBOX', env)
        self.assertEqual(env['CHROME_DEVTOOLS_AXI_SESSION'], 'test')
        self.assertEqual(env['CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS'], '1')

    def test_independent_profiles_and_owned_cleanup(self):
        a, b = self.run_object(), self.run_object()
        self.assertNotEqual(a.session, b.session)
        self.assertNotEqual(a.profile, b.profile)
        (a.profile / 'cookies').write_text('synthetic only')
        self.assertFalse((b.profile / 'cookies').exists())
        self.assertEqual(a.profile.stat().st_mode & 0o777, 0o700)
        screenshot = a.evidence / 'preflight.png'
        screenshot.write_bytes(b'evidence retained')
        a.cleanup()
        self.assertFalse(a.profile.exists())
        self.assertTrue(b.profile.exists())
        self.assertTrue(screenshot.exists())
        b.cleanup()

    def test_one_slot_across_projects_released_by_owner(self):
        slot = browser.acquire_slot(self.home)
        try:
            with self.assertRaisesRegex(browser.BrowserError, 'slot busy'):
                browser.acquire_slot(self.home)
        finally:
            os.close(slot)
        slot = browser.acquire_slot(self.home)
        os.close(slot)

    def test_symlink_slot_and_profile_parent_refused(self):
        directory = browser.private_dir(self.home / '.bibi-browser')
        (directory / 'slot').symlink_to(self.home / 'unrelated')
        with self.assertRaises(OSError):
            browser.acquire_slot(self.home)
        link = self.home / 'linked'
        link.symlink_to(directory)
        with self.assertRaisesRegex(browser.BrowserError, 'private directory'):
            browser.private_dir(link)

    def test_loopback_and_owned_socket_not_just_a_port(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            sock.listen()
            self.assertTrue(browser.loopback_listener(os.getpid(), sock.getsockname()[1]))
            self.assertFalse(browser.loopback_listener(1, sock.getsockname()[1]))
        with socket.socket() as sock:
            sock.bind(('0.0.0.0', 0))
            sock.listen()
            self.assertFalse(browser.loopback_listener(os.getpid(), sock.getsockname()[1]))

    def test_cleanup_refuses_unrelated_live_process(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        run = self.run_object()
        run.bridge_pid, run.bridge_identity = unrelated.pid, 'invented-start'
        run.url = 'http://127.0.0.1:1234'
        try:
            with patch.object(run, 'call') as call:
                with self.assertRaisesRegex(browser.BrowserError, 'ownership uncertain'):
                    run.cleanup()
                call.assert_not_called()
            self.assertIsNone(unrelated.poll())
            self.assertTrue(run.profile.exists())
            self.assertIsNone(browser.process_identity(unrelated.pid, run.session, run.url, run.cwd))
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_changed_bridge_identity_never_stopped(self):
        run = self.run_object()
        run.bridge_pid, run.bridge_identity = 12345, 'first-start'
        with patch.object(browser, 'process_identity', return_value='different-start'), patch.object(run, 'call') as call:
            with self.assertRaises(browser.BrowserError):
                run.cleanup()
            call.assert_not_called()

    def test_sandbox_error_is_not_zero_pages_or_success(self):
        self.metadata()
        run = self.run_object()
        def refused_launch(_args, **kwargs):
            kwargs['stderr'].write('No usable sandbox! Ubuntu AppArmor user namespace restriction\n')
            kwargs['stderr'].flush()
            child = Mock()
            child.poll.return_value = -6
            return child
        with patch.object(browser.os, 'access', return_value=True), \
                patch.object(run, 'call', side_effect=['Google Chrome 153.0.8010.52', 'admitted']), \
                patch.object(browser.subprocess, 'Popen', side_effect=refused_launch):
            with self.assertRaisesRegex(browser.BrowserError, 'No usable sandbox.*Ubuntu AppArmor'):
                run.start()
        run.cleanup()

    def test_version_drift_and_missing_mcp_refuse_before_browser(self):
        with patch.object(browser.os, 'access', return_value=True):
            with self.assertRaisesRegex(browser.BrowserError, 'Missing pinned'):
                self.run_object().start()
        self.metadata()
        run = self.run_object()
        with patch.object(browser.os, 'access', return_value=True), \
                patch.object(run, 'call', return_value='Google Chrome 999.0.0.1'), \
                patch.object(browser.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(browser.BrowserError, 'version drift'):
                run.start()
            launch.assert_not_called()

    def test_admission_refusal_does_not_launch_or_signal(self):
        self.metadata()
        run = self.run_object()
        with patch.object(browser.os, 'access', return_value=True), \
                patch.object(run, 'call', side_effect=['Google Chrome 153.0.8010.52', browser.BrowserError('refused headroom')]) as call, \
                patch.object(browser.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(browser.BrowserError, 'headroom'):
                run.start()
            self.assertEqual(call.call_args.args[0][-2:], ['admit', '--no-reserve'])
            launch.assert_not_called()

    def test_bounded_command_failure_and_sanitized_diagnostic(self):
        run = self.run_object()
        start = time.monotonic()
        with self.assertRaisesRegex(browser.BrowserError, 'deadline'):
            run.call([sys.executable, '-c', 'import time; time.sleep(60)'], timeout=0.1)
        self.assertLess(time.monotonic() - start, 4)
        with self.assertRaisesRegex(browser.BrowserError, 'output'):
            run.call([sys.executable, '-c', 'print("x" * 2000000)'])
        self.assertLessEqual((run.evidence / 'command-2.log').stat().st_size, 1048576)
        text = browser.diagnostic('No usable sandbox! token=abc https://private:secret@example.invalid/secret')
        self.assertIn('No usable sandbox', text)
        self.assertNotIn('abc', text)
        self.assertNotIn('example.invalid', text)

    def test_preflight_requires_actual_navigation_js_and_png(self):
        run = self.run_object()
        calls = []
        def fake_axi(args, timeout=20, input_text=None):
            calls.append(args[1])
            if args[1] == 'run':
                self.assertIn('document.title', input_text)
                self.assertIn('location.href', input_text)
                self.assertIn('localStorage.length', input_text)
                return input_text.rsplit('console.log(', 1)[1].split(')', 1)[0].strip('"')
            if args[1] == 'screenshot':
                Path(args[2]).write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            return ''
        with patch.object(run, 'call', side_effect=fake_axi):
            run.preflight()
        self.assertEqual(calls, ['newpage', 'run', 'screenshot'])
        with patch.object(run, 'call', return_value='pages: 0'):
            with self.assertRaisesRegex(browser.BrowserError, 'did not prove'):
                run.preflight()
        run.cleanup()

    def test_native_cleanup_only_with_matching_registration(self):
        run = self.run_object()
        run.session_dir = self.home / 'owned-session'
        run.session_dir.mkdir()
        (run.session_dir / 'bridge.pid').write_text('{"pid":12345,"port":1234}')
        with patch.object(browser, 'process_identity', side_effect=['start', 'start', None]), \
                patch.object(run, 'call') as call:
            run.cleanup()
            call.assert_called_once_with([self.config['axi'], 'stop'], timeout=10)
        self.assertFalse(run.profile.exists())

    def test_overwritten_native_registration_refuses_stop(self):
        run = self.run_object()
        run.session_dir = self.home / 'owned-session'
        run.session_dir.mkdir()
        (run.session_dir / 'bridge.pid').write_text('{"pid":22222,"port":1234}')
        run.bridge_pid, run.bridge_identity = 11111, 'original'
        with patch.object(browser, 'process_identity', return_value='original'), patch.object(run, 'call') as call:
            with self.assertRaisesRegex(browser.BrowserError, 'ownership uncertain'):
                run.cleanup()
            call.assert_not_called()
        self.assertTrue(run.session_dir.exists())

    def test_unregistered_startup_cleanup_uses_only_witnessed_pidfds(self):
        parent = subprocess.Popen([sys.executable, '-c',
            'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); time.sleep(30)'])
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        owned = browser.OwnedStartup()
        try:
            deadline = time.monotonic() + 3
            while not owned.handles and time.monotonic() < deadline:
                owned.observe(parent)
                time.sleep(0.01)
            self.assertTrue(owned.handles)
            self.assertNotIn(unrelated.pid, owned.handles)
            self.assertNotIn(parent.pid, owned.handles)
            parent.terminate()
            parent.wait(timeout=5)
            owned.close()  # detached/reparented but same retained kernel identities
            self.assertIsNone(unrelated.poll())
        finally:
            owned.close()
            for child in (parent, unrelated):
                if child.poll() is None:
                    child.terminate()
                child.wait(timeout=5)

    def test_failed_start_without_identity_retains_evidence(self):
        run = self.run_object()
        run.start_attempted = True
        with self.assertRaisesRegex(browser.BrowserError, 'ownership uncertain'):
            run.cleanup()
        self.assertTrue(run.profile.exists())

    def test_browser_only_playbook_cannot_import_whole_machine(self):
        source = (ROOT / 'browser.yml').read_text()
        self.assertIn('tasks/install-browser.yml', source)
        self.assertNotIn('import_playbook:', source)
        install = (ROOT / 'tasks/install-browser.yml').read_text()
        self.assertIn('install-axi-tools.yml', install)
        self.assertNotIn('install-memory-guard.yml', install)
        self.assertNotIn('setup hooks', install)


if __name__ == '__main__':
    unittest.main()
