#!/usr/bin/env python3
"""One disposable Ubuntu Chrome + native AXI session, not a browser daemon.

No attach/reap command: cleanup authority comes only from this invocation's
child handle and freshly allocated AXI session, never a caller-supplied PID.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class BrowserError(RuntimeError):
    pass


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise BrowserError(f"Not an owned private directory: {path}")
    return path


def session_parent(path):
    # Native AXI's existing parents can be 0755/0775. Do not chmod shared state.
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o002:
        raise BrowserError(f'Unsafe AXI state parent: {path}')


def acquire_slot(home):
    # One cooperative slot across all projects for this daily user, held through
    # cleanup. No stale PID locks and no process inventory on admission.
    directory = private_dir(home / '.bibi-browser')
    fd = os.open(directory / 'slot', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        os.close(fd)
        raise BrowserError('Unsafe browser slot file')
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise BrowserError('Browser slot busy; let the other project finish. Nothing stopped.') from None
    return fd


def child_environment(session, port, url, mcp):
    # Do not inherit personal auto-connect, Chrome flags, credentials headers,
    # developer sandbox overrides, or another task's profile/session selection.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('CHROME_DEVTOOLS_AXI_', 'CHROME_DEVTOOLS_MCP_')) and k not in
           ('CHROME_DEVEL_SANDBOX', 'CHROME_USER_DATA_DIR', 'CHROME_LOG_FILE')}
    env.update(CHROME_DEVTOOLS_AXI_SESSION=session, CHROME_DEVTOOLS_AXI_PORT=str(port),
               CHROME_DEVTOOLS_AXI_BROWSER_URL=url, CHROME_DEVTOOLS_AXI_MCP_PATH=str(mcp),
               CHROME_DEVTOOLS_AXI_BRIDGE_TIMEOUT_MS='15000',
               CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS='1',
               CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS='1')
    return env


def chrome_args(executable, profile):
    return [str(executable), '--headless', '--remote-debugging-address=127.0.0.1',
            '--remote-debugging-port=0', f'--user-data-dir={profile}',
            '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
            '--disable-component-update', '--disable-sync', 'about:blank']


def process_identity(pid, session, url, cwd):
    """Read only the PID registered by our fresh native session. Ambiguity refuses."""
    try:
        root = Path('/proc') / str(pid)
        info = root.stat()
        argv = (root / 'cmdline').read_bytes().split(b'\0')
        env = (root / 'environ').read_bytes().split(b'\0')
        fields = (root / 'stat').read_text().rsplit(')', 1)[1].split()
        if (info.st_uid != os.getuid() or fields[0] == 'Z'
                or not any(a.endswith(b'/chrome-devtools-axi-bridge.js') for a in argv)
                or f'CHROME_DEVTOOLS_AXI_SESSION={session}'.encode() not in env
                or f'CHROME_DEVTOOLS_AXI_BROWSER_URL={url}'.encode() not in env
                or (root / 'cwd').resolve() != cwd):
            return None
        return fields[19]  # /proc stat field 22: immutable start time
    except (OSError, ValueError, IndexError):
        return None


def loopback_listener(pid, port):
    """Both address and socket ownership must match; never probe another task's port."""
    inodes = set()
    try:
        for fd in (Path('/proc') / str(pid) / 'fd').iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith('socket:['):
                inodes.add(target[8:-1])
        found = False
        for table in ('tcp', 'tcp6'):
            for row in (Path('/proc/net') / table).read_text().splitlines()[1:]:
                fields = row.split()
                address, raw_port = fields[1].split(':')
                if int(raw_port, 16) != port or fields[3] != '0A':
                    continue
                if address not in ('0100007F', '00000000000000000000000001000000'):
                    return False
                found |= fields[9] in inodes
        return found
    except (OSError, ValueError, IndexError):
        return False


def diagnostic(text):
    """Short safe operator hints; raw bounded evidence stays in the private run dir."""
    text = re.sub(r'https?://\S+|wss?://\S+', '<url>', text)
    text = re.sub(r'(?i)(token|password|secret|authorization)[=: ]+\S+', r'\1=<redacted>', text)
    text = re.sub(r'/home/[^/\s]+', '<home>', text)
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    # Chromium itself suggests disabling its sandbox. Never echo that as advice.
    text = re.sub(r'If you want to live dangerously.*', '', text)
    return text[-3000:]


class OwnedStartup:
    """Retain pidfds only for descendants witnessed during our native AXI start.

    AXI 0.1.31 detaches its bridge before writing bridge.pid and does not tear
    that child down on startup timeout. Watch only our live CLI child and then
    its proven descendants, never the machine's process table. pidfds prevent
    recycled-PID signalling. There is no external PID input or discovery API.
    """
    def __init__(self):
        self.handles = {}

    def observe(self, cli):
        parents = list(self.handles)
        if cli.poll() is None:
            parents.append(cli.pid)  # unreaped Popen child is still our identity
        for parent in parents:
            if parent in self.handles and select.select([self.handles[parent]], [], [], 0)[0]:
                continue
            try:
                tasks = list((Path('/proc') / str(parent) / 'task').iterdir())
                children = set()
                for task in tasks:
                    children.update(map(int, (task / 'children').read_text().split()))
                for pid in children:
                    if pid in self.handles:
                        continue
                    if len(self.handles) >= 32:
                        raise BrowserError('Unexpected AXI startup tree size; refusing further work')
                    fd = os.pidfd_open(pid)
                    try:
                        root = Path('/proc') / str(pid)
                        fields = (root / 'stat').read_text().rsplit(')', 1)[1].split()
                        parent_still_owned = (parent not in self.handles or not
                                              select.select([self.handles[parent]], [], [], 0)[0])
                        if parent_still_owned and root.stat().st_uid == os.getuid() and int(fields[1]) == parent:
                            self.handles[pid] = fd
                            fd = None
                    finally:
                        if fd is not None:
                            os.close(fd)
            except (OSError, ValueError, IndexError):
                continue  # disappeared/unreadable is never authority to signal

    def close(self):
        # Descendants are recorded after parents. Native stop normally already
        # closed everything; this only handles survivors of our own startup.
        try:
            for sig, grace in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
                for fd in reversed(list(self.handles.values())):
                    if not select.select([fd], [], [], 0)[0]:
                        try:
                            signal.pidfd_send_signal(fd, sig)
                        except ProcessLookupError:
                            pass
                deadline = time.monotonic() + grace
                while time.monotonic() < deadline:
                    if all(select.select([fd], [], [], 0)[0] for fd in self.handles.values()):
                        return
                    time.sleep(0.05)
            raise BrowserError('Owned AXI startup processes did not exit; retain evidence')
        finally:
            for fd in self.handles.values():
                os.close(fd)
            self.handles.clear()


class SyntheticPage(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>bibi-browser-preflight</title><h1>Synthetic browser check</h1>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class BrowserRun:
    def __init__(self, config, home):
        self.config, self.home = config, home
        self.cwd = Path.cwd().resolve()
        self.session = 'bibi-' + uuid.uuid4().hex
        self.evidence = Path(tempfile.mkdtemp(prefix='run-', dir=private_dir(home / '.bibi-browser')))
        self.profile = self.evidence / 'profile'
        self.profile.mkdir(mode=0o700)
        self.browser = None
        self.bridge_identity = None
        self.bridge_pid = None
        self.session_dir = None
        self.env = None
        self.url = None
        self.log_number = 0
        self.startup = OwnedStartup()
        self.start_attempted = False
        self.start_succeeded = False

    def call(self, args, timeout=20, input_text=None):
        self.log_number += 1
        log = self.evidence / f'command-{self.log_number}.log'
        is_start = args == [self.config['axi'], 'start']
        with log.open('w') as output:
            try:
                child = subprocess.Popen(args, env=self.env, text=True, stdin=subprocess.PIPE,
                                         stdout=output, stderr=output)
                try:
                    child.stdin.write(input_text or '')
                    child.stdin.close()
                    deadline = time.monotonic() + timeout
                    while child.poll() is None:
                        if is_start:
                            self.startup.observe(child)
                        if time.monotonic() >= deadline or log.stat().st_size > 1048576:
                            raise BrowserError(f'Command deadline/output bound ({timeout}s, 1 MiB); inspect {log}')
                        time.sleep(0.05)
                    if log.stat().st_size > 1048576:
                        raise BrowserError(f'Command exceeded 1 MiB output; inspect {log}')
                finally:
                    if is_start:
                        self.startup.observe(child)
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait(timeout=2)
            finally:
                # Retain bounded raw diagnostics even from a noisy failing tool.
                if log.stat().st_size > 1048576:
                    with log.open('r+b') as bounded:
                        bounded.truncate(1048576)
        with log.open('rb') as stream:
            text = stream.read(65536).decode(errors='replace')
        if child.returncode:
            raise BrowserError(f'Command failed ({child.returncode}): {diagnostic(text)}; evidence {log}')
        return text

    def start(self):
        executable = Path(self.config['executable'])
        if str(executable) != '/opt/google/chrome/chrome' or not os.access(executable, os.X_OK):
            raise BrowserError('Missing official /opt/google/chrome/chrome; administrator must run browser.yml. No cache fallback.')
        for key, package in (('axi', 'chrome-devtools-axi'), ('mcp', 'chrome-devtools-mcp')):
            target = Path(self.config[key]).resolve()
            metadata = next((p / 'package.json' for p in target.parents if (p / 'package.json').is_file()), None)
            if not metadata:
                raise BrowserError(f'Missing pinned {package}; reconcile browser.yml')
            actual = json.loads(metadata.read_text())
            if actual.get('name') != package or actual.get('version') != self.config[key + '_version']:
                raise BrowserError(f'{package} version drift; reconcile reviewed browser pins')
        version = self.call([str(executable), '--version']).strip()
        if version != 'Google Chrome ' + self.config['version'].rsplit('-', 1)[0]:
            raise BrowserError('Chrome version drift; review/bump group_vars/all.yml; do not downgrade security updates')
        # Reuse the repo's existing admission path (no process inventory/signals).
        # Ship the same source alongside this helper so older host daemons need
        # not be changed by this narrowly scoped browser installation.
        self.call([sys.executable, str(Path(__file__).with_name('bibi_memory_guard.py')),
                   'admit', '--no-reserve'])
        log = self.evidence / 'chrome.log'
        with log.open('w') as output:
            self.browser = subprocess.Popen(chrome_args(executable, self.profile),
                                            env=child_environment(self.session, 1, '', self.config['mcp']),
                                            stdout=output, stderr=output)
        deadline = time.monotonic() + 20
        active = self.profile / 'DevToolsActivePort'
        while not active.is_file():
            if self.browser.poll() is not None or time.monotonic() >= deadline:
                with log.open('rb') as stream:
                    detail = diagnostic(stream.read(65536).decode(errors='replace'))
                raise BrowserError('Chrome startup failed or timed out. Keep the sandbox enabled; '
                                   'check the official package and Ubuntu AppArmor policy. ' + detail)
            time.sleep(0.1)
        port = int(active.read_text().splitlines()[0])
        if not loopback_listener(self.browser.pid, port):
            raise BrowserError('CDP is not an ownership-proven loopback listener; refusing connection')
        self.url = f'http://127.0.0.1:{port}'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            bridge_port = sock.getsockname()[1]
        # AXI checks the session on collisions. Never reuse a default session.
        session_parent(self.home / '.chrome-devtools-axi')
        session_parent(self.home / '.chrome-devtools-axi' / 'sessions')
        self.session_dir = self.home / '.chrome-devtools-axi' / 'sessions' / self.session
        self.session_dir.mkdir(mode=0o700)  # exclusive: existing identity refuses
        self.env = child_environment(self.session, bridge_port, self.url, self.config['mcp'])
        # MCP 1.9 defaults its filesystem root to os.tmpdir(). Narrow that native
        # boundary to our private evidence directory; never enable unrestricted paths.
        self.env['TMPDIR'] = str(self.evidence)
        self.start_attempted = True
        self.call([self.config['axi'], 'start'])
        self.start_succeeded = True
        self.identify_bridge()
        if not self.bridge_identity or not loopback_listener(self.bridge_pid, bridge_port):
            raise BrowserError('AXI bridge is not an ownership-proven loopback listener')
        (self.evidence / 'session.json').write_text(json.dumps({
            'session': self.session, 'project': str(self.cwd), 'chrome_version': version,
            'profile': str(self.profile), 'browser_pid': self.browser.pid,
            'bridge_pid': self.bridge_pid, 'loopback_only': True}, indent=2) + '\n')

    def identify_bridge(self):
        if not self.session_dir:
            return False
        try:
            record = json.loads((self.session_dir / 'bridge.pid').read_text())
            pid = record['pid']
            if type(pid) is not int or pid <= 1:
                return False
            identity = process_identity(pid, self.session, self.url, self.cwd)
            if identity and (self.bridge_identity is None or (pid, identity) == (self.bridge_pid, self.bridge_identity)):
                self.bridge_pid, self.bridge_identity = pid, identity
                return True
        except (OSError, ValueError, KeyError):
            pass
        return False

    def preflight(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), SyntheticPage)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{server.server_port}/'
            self.call([self.config['axi'], 'newpage', url])
            nonce = uuid.uuid4().hex
            script = ('const result = await page.eval(() => { '
                      'if (document.title !== "bibi-browser-preflight" || location.href !== ' + json.dumps(url) + ') '
                      'throw new Error("navigation failed"); '
                      'if (localStorage.length || document.cookie) throw new Error("profile not clean"); '
                      'localStorage.setItem("preflight", "synthetic"); return 6 * 7; }); '
                      'if (result !== 42) throw new Error("JS proof failed"); console.log(' + json.dumps(nonce) + ');')
            output = self.call([self.config['axi'], 'run'], input_text=script)
            if nonce not in output:
                raise BrowserError('AXI did not prove navigation/JavaScript; inspect raw command evidence, not formatted pages count')
            screenshot = self.evidence / 'preflight.png'
            self.call([self.config['axi'], 'screenshot', str(screenshot)])
            if not screenshot.is_file() or screenshot.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
                raise BrowserError('AXI did not produce a PNG screenshot')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def cleanup(self):
        safe = True
        # A failed start may still have registered its bridge. Check only our
        # exclusively allocated session, then prove env + cwd + start identity.
        registered = self.identify_bridge()
        if self.bridge_pid:
            identity = process_identity(self.bridge_pid, self.session, self.url, self.cwd)
            if not registered or identity != self.bridge_identity:
                safe = False  # Never stop a changed/unrelated PID.
            else:
                try:
                    self.call([self.config['axi'], 'stop'], timeout=10)
                    safe = process_identity(self.bridge_pid, self.session, self.url, self.cwd) is None
                except BrowserError:
                    safe = False
        elif self.session_dir and (self.session_dir / 'bridge.pid').exists():
            safe = False
        elif self.start_attempted and not self.start_succeeded and not self.startup.handles:
            safe = False  # no startup identity was witnessed; do not claim cleanup
        try:
            self.startup.close()
        except (BrowserError, OSError):
            safe = False
        if self.browser:
            # Popen retains the unreaped child identity: no PID-file based kill.
            if self.browser.poll() is None:
                self.browser.terminate()
                try:
                    self.browser.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.browser.kill()
                    self.browser.wait(timeout=5)
        if safe:
            shutil.rmtree(self.profile)
            if self.session_dir:
                shutil.rmtree(self.session_dir)
        else:
            raise BrowserError(f'Cleanup ownership uncertain: nothing else signalled; retain {self.evidence} for review')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/bibi-browser.json')
    parser.add_argument('--timeout', type=int, default=900, help='run command deadline, 1..1800 seconds')
    parser.add_argument('mode', choices=['preflight', 'run'])
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.getuid() == 0 or sys.platform != 'linux':
        parser.error('Run as the ordinary daily user on supported Ubuntu, never root')
    command = args.command[1:] if args.command and args.command[0] == '--' else args.command
    if (not 1 <= args.timeout <= 1800 or (args.mode == 'run' and not command)
            or (args.mode == 'preflight' and command)):
        parser.error('run requires a foreground command; preflight takes none; timeout must be in 1..1800')
    os.umask(0o077)
    slot, run = None, None
    try:
        home = Path.home()
        slot = acquire_slot(home)
        config = json.loads(Path(args.config).read_text())
        run = BrowserRun(config, home)
        print(f'Browser evidence: {run.evidence}', flush=True)
        def interrupted(_signum, _frame):
            raise BrowserError('Interrupted; cleaning only this run')
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        failure = None
        try:
            run.start()
            run.preflight()
            if args.mode == 'run':
                run.env['BIBI_BROWSER_EVIDENCE'] = str(run.evidence)
                run.call(command, timeout=args.timeout)
        except BaseException as exc:
            failure = exc
            raise
        finally:
            try:
                run.cleanup()
            except (BrowserError, OSError) as cleanup_error:
                if failure is not None:
                    raise BrowserError(f'{failure}; cleanup: {cleanup_error}') from failure
                raise
        (run.evidence / 'result.json').write_text(json.dumps({'ok': True, 'mode': args.mode,
            'session': run.session, 'profile_removed': not run.profile.exists(),
            'session_removed': not run.session_dir.exists()}) + '\n')
        print('ok: Chrome sandbox launch, loopback CDP/AXI, navigation, JS, screenshot, owned cleanup')
        return 0
    except (BrowserError, OSError, ValueError, KeyError) as exc:
        if run is not None:
            (run.evidence / 'result.json').write_text(json.dumps({'ok': False,
                'error': diagnostic(str(exc)), 'session': run.session}) + '\n')
        print(f'bibi-browser: {exc}', file=sys.stderr)
        return 1
    finally:
        if slot is not None:
            os.close(slot)


if __name__ == '__main__':
    sys.exit(main())
