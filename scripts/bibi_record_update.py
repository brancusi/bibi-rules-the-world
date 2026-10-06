#!/usr/bin/python3
"""Root-only update recorder; never publish raw output or caller-supplied labels."""
import datetime
import json
import os
import re
import signal
import stat
import subprocess
import sys
import uuid


PRIVATE_PATH = "/var/lib/bibi-machine-update-private"
SUMMARY_PATH = "/var/lib/bibi-machine-update"
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def timestamp():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def checked_directory(parent, name, mode=None, owner=0):
    """Open relative to a trusted fd, without following links or fixing permissions."""
    if mode is not None:
        try:
            os.mkdir(name, mode, dir_fd=parent)
        except FileExistsError:
            pass
    fd = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
    info = os.fstat(fd)
    if info.st_uid != owner or info.st_mode & 0o022 or (
        mode is not None and (stat.S_IMODE(info.st_mode) != mode or info.st_gid != os.getegid())
    ):
        os.close(fd)
        raise PermissionError("unsafe recording directory")
    return fd


def recording_directory(path, mode):
    fd = os.open("/", DIRECTORY_FLAGS)
    try:
        parts = path.strip("/").split("/")
        for index, part in enumerate(parts):
            child = checked_directory(fd, part, mode if index == len(parts) - 1 else None)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def publish(directory, name, record):
    """Atomic snapshots; directory fd is trusted and no existing file is opened."""
    temporary = "." + uuid.uuid4().hex
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as output:
            json.dump(record, output, ensure_ascii=True, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


class Recap:
    """Bounded strict default-callback parser, not a success/attestation oracle."""
    keys = ("ok", "changed", "unreachable", "failed", "skipped", "rescued", "ignored")
    row = re.compile(rb"localhost\s+:\s+" + rb"\s+".join(
        key.encode() + rb"=([0-9]{1,9})" for key in keys) + rb"\s*")

    def __init__(self):
        self.buffer = b""
        self.discard = False
        self.in_recap = False
        self.counts = None

    def feed(self, chunk):
        # Consume at most a 4KiB line; never hold arbitrarily long task output.
        for fragment in chunk.splitlines(keepends=True):
            ended = fragment.endswith(b"\n")
            if len(self.buffer) + len(fragment) > 4096:
                self.discard = True
            if not self.discard:
                self.buffer += fragment
            if ended:
                if not self.discard:
                    self.line(self.buffer.rstrip(b"\r\n"))
                self.buffer = b""
                self.discard = False

    def line(self, line):
        if re.fullmatch(rb"PLAY RECAP \*+\s*", line):
            self.in_recap = True
            self.counts = None
        elif self.in_recap and line.strip():
            match = self.row.fullmatch(line)
            if match:
                self.counts = dict(zip(self.keys, map(int, match.groups())))
            # Only the fixed localhost row immediately after a recap is accepted.
            self.in_recap = False


def record_run(command, private, summaries):
    started = timestamp()
    run_id = started.replace(":", "").replace("+0000", "Z") + "-" + uuid.uuid4().hex
    name = run_id + ".json"
    record = dict(schema=1, run_id=run_id, started_at=started, finished_at=None,
                  state="started", updater_exit=None, signal=None, recap=None,
                  log_complete=False)
    log_fd = os.open(run_id + ".log", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=private)
    child = None
    received = None
    old_handlers = {}
    recap = Recap()
    log_ok = True
    terminal_ok = True

    def interrupted(signum, _frame):
        nonlocal received
        received = signum
        if child is not None:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    try:
        # A durable started record precedes every child invocation. SIGKILL or
        # power loss can leave this snapshot behind; it never implies success.
        os.fsync(private)
        publish(summaries, name, record)
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old_handlers[signum] = signal.signal(signum, interrupted)
        if received is None:
            try:
                child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         start_new_session=True)
                if received is not None:
                    interrupted(received, None)
            except OSError:
                record["state"] = "launch_failed"
        if child is None:
            exit_code = 128 + received if received else 127
        else:
            with child.stdout:
                while True:
                    chunk = os.read(child.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    recap.feed(chunk)
                    if log_ok:
                        try:
                            view = memoryview(chunk)
                            while view:
                                written = os.write(log_fd, view)
                                view = view[written:]
                        except OSError:
                            log_ok = False
                    if terminal_ok:
                        try:
                            view = memoryview(chunk)
                            while view:
                                written = os.write(sys.stdout.fileno(), view)
                                view = view[written:]
                        except (OSError, ValueError):
                            terminal_ok = False
            raw_exit = child.wait()
            exit_code = raw_exit if raw_exit >= 0 else 128 - raw_exit
            record["updater_exit"] = exit_code
        try:
            os.fsync(log_fd)
        except OSError:
            log_ok = False
        record.update(finished_at=timestamp(), signal=received, recap=recap.counts,
                      log_complete=log_ok)
        if received:
            record["state"] = "interrupted"
        elif child is not None:
            record["state"] = "completed"
        try:
            publish(summaries, name, record)
        except OSError:
            print("Final update summary unavailable; inspect private log.", file=sys.stderr)
            return exit_code or (128 + received if received else 74)
        # Preserve a real child failure even when logging fails. A zero child
        # status cannot hide a recorder failure or an observed interruption.
        return exit_code or (128 + received if received else 0) or (0 if log_ok else 74)
    finally:
        os.close(log_fd)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


def main():
    if os.geteuid() != 0 or os.getegid() != 0 or len(sys.argv) < 3 or sys.argv[1] != "--":
        print("Run through sudo as the maintenance administrator: bibi-record-update -- COMMAND", file=sys.stderr)
        return 1
    os.umask(0o077)
    # mkdir's mode is filtered by umask, so allow safe directory traversal but
    # never loose file creation; all new files initially request mode 0600.
    os.umask(0o022)
    private = recording_directory(PRIVATE_PATH, 0o700)
    try:
        summaries = recording_directory(SUMMARY_PATH, 0o755)
        try:
            os.umask(0o077)
            return record_run(sys.argv[2:], private, summaries)
        finally:
            os.close(summaries)
    finally:
        os.close(private)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError:
        # Error details can include private paths. Leave the started snapshot
        # incomplete when final publication fails; never fabricate an exit.
        print("Update recording failed; administrator inspection is required.", file=sys.stderr)
        sys.exit(74)
