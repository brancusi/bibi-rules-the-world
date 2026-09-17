#!/usr/bin/env python3
"""Bounded memory observer and ownership-proven browser-leak reaper.

This guard exists for one concrete failure mode observed on the managed
Droplet: browser QA tasks end without tearing down their Chrome DevTools
bridges, so `chrome-devtools-axi` bridge daemons, their `chrome-devtools-mcp`
children, the MCP telemetry watchdogs, and any descendant headless Chromium
trees keep running after the owning task is gone. The bridge deliberately
daemonises, so reparenting to PID 1 is its *normal* state, not a leak signal;
on a host with no swap the leaked anonymous pages can never be paged out, so
MemAvailable falls monotonically until the host is at OOM risk.

The design consequence is the whole point of this file:

  * High RSS, old age, reparenting, and an idle listening port are all shared
    by healthy live bridges and leaked ones. None of them, alone or combined,
    authorises a kill.
  * Only positive *ownership* evidence authorises a kill: the tree's owning
    worktree must be provably finished (its FirstMate task reached a terminal
    status and is no longer busy, and no owner harness process is still rooted
    there) or provably gone (its working directory was deleted).
  * Every name-based match in this file is protective only. Matching a name can
    stop a kill; it can never cause one.
  * Anything ambiguous refuses and alerts. Refusing is always the safe outcome.

Memory thresholds are deliberately separated from destructive eligibility.
Low memory raises alerts; it never lowers the bar for killing a process. A
large, old, ownership-proven tree is cleaned on the ordinary schedule, before
any emergency.

A second failure mode is handled by admission, never by killing. On
2026-09-17 nothing had leaked: the aggregate working set of live, legitimate
workers had filled swap and drained MemAvailable, and one more worker launch
evicted the fleet's executable pages. The kernel then refaulted them forever
without ever declaring an OOM, and the host wedged. No process in that state
is provably abandoned, so nothing may be killed. The only safe lever is to
refuse the *next* launch. `admit` therefore answers one question from
`/proc/meminfo` and `/proc/pressure/memory` alone - "is there headroom for one
more worker?" - and that code path never builds a process inventory, never
constructs a signaller, and can never send a signal.

The process inventory, the memory reading, the socket table, the clock, the
sleep, and the signal sender are all injected, so the full decision pipeline
runs against synthetic inventories in tests without touching a real process.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import signal
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Iterable, Sequence

VERSION = "1.1.0"

DEFAULT_CONFIG_PATH = "/etc/bibi-memory-guard.conf"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Defaults are the reviewed conservative policy. The rendered configuration
# file at DEFAULT_CONFIG_PATH is authoritative on a provisioned host; these
# values keep the script runnable and testable on its own.
DEFAULTS: dict[str, str] = {
    # The only account whose processes may ever be signalled.
    "owner_user": "bibi",
    # Ownership oracles.
    "axi_state_dir": "/home/bibi/.chrome-devtools-axi",
    "firstmate_state_dir": "/home/bibi/firstmate/state",
    "worktree_root": "/home/bibi/.treehouse",
    # Commands that mean "a task owner is still running here". Protective only.
    "owner_commands": "claude pi codex opencode cursor-agent amp goose aider",
    # Observation thresholds. These raise alerts. They never authorise a kill.
    "mem_available_warn_percent": "20",
    "mem_available_critical_percent": "10",
    "swap_used_warn_percent": "25",
    "swap_used_critical_percent": "60",
    # Destructive eligibility thresholds. Independent of memory pressure.
    "leak_min_age_seconds": "3600",
    "leak_min_member_age_seconds": "300",
    "leak_min_tree_rss_kb": "262144",
    "leak_max_tree_processes": "60",
    "leak_max_trees_per_run": "2",
    "owner_task_settled_seconds": "900",
    "busy_state_fresh_seconds": "3600",
    # Launch admission. Refusing a launch is the only action these authorise;
    # like the observation thresholds, they never authorise a kill.
    "launch_gate_enabled": "true",
    "launch_worker_reserve_kb": "786432",
    "launch_min_mem_available_percent": "15",
    "launch_min_mem_available_no_swap_percent": "25",
    "launch_max_memory_stall_percent": "10",
    "launch_reservation_seconds": "300",
    # Empty means the invoking user's XDG runtime directory.
    "launch_ledger_dir": "",
    # Termination behaviour.
    "term_grace_seconds": "10",
    "term_poll_interval_seconds": "0.5",
    # Bounded reporting.
    "max_log_lines": "200",
    "state_file": "/var/lib/bibi-memory-guard/status.json",
    "lock_file": "/run/lock/bibi-memory-guard.lock",
}

INT_KEYS = frozenset(
    {
        "mem_available_warn_percent",
        "mem_available_critical_percent",
        "swap_used_warn_percent",
        "swap_used_critical_percent",
        "leak_min_age_seconds",
        "leak_min_member_age_seconds",
        "leak_min_tree_rss_kb",
        "leak_max_tree_processes",
        "leak_max_trees_per_run",
        "owner_task_settled_seconds",
        "busy_state_fresh_seconds",
        "term_grace_seconds",
        "max_log_lines",
        "launch_worker_reserve_kb",
        "launch_min_mem_available_percent",
        "launch_min_mem_available_no_swap_percent",
        "launch_reservation_seconds",
    }
)

FLOAT_KEYS = frozenset({"term_poll_interval_seconds", "launch_max_memory_stall_percent"})

LIST_KEYS = frozenset({"owner_commands"})

BOOL_KEYS = frozenset({"launch_gate_enabled"})


class ConfigError(RuntimeError):
    """The configuration file exists but cannot be trusted."""


def load_config(path: str | None) -> dict[str, object]:
    """Read `key=value` configuration, mirroring /etc/bibi-provisioned-versions."""
    raw = dict(DEFAULTS)
    if path:
        config_path = Path(path)
        if config_path.exists():
            for lineno, line in enumerate(
                config_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if "=" not in stripped:
                    raise ConfigError(f"{path}:{lineno}: expected key=value")
                key, _, value = stripped.partition("=")
                key = key.strip()
                if key not in DEFAULTS:
                    raise ConfigError(f"{path}:{lineno}: unknown key {key!r}")
                raw[key] = value.strip()

    config: dict[str, object] = {}
    for key, value in raw.items():
        if key in INT_KEYS:
            try:
                config[key] = int(value)
            except ValueError as exc:
                raise ConfigError(f"{key} must be an integer, got {value!r}") from exc
        elif key in FLOAT_KEYS:
            try:
                config[key] = float(value)
            except ValueError as exc:
                raise ConfigError(f"{key} must be a number, got {value!r}") from exc
        elif key in LIST_KEYS:
            config[key] = tuple(value.split())
        elif key in BOOL_KEYS:
            if value.lower() not in {"true", "false"}:
                raise ConfigError(f"{key} must be true or false, got {value!r}")
            config[key] = value.lower() == "true"
        else:
            config[key] = value
    return config


# ---------------------------------------------------------------------------
# Process model
# ---------------------------------------------------------------------------

ROLE_BRIDGE = "axi-bridge"
ROLE_MCP_LAUNCHER = "mcp-launcher"
ROLE_MCP_SHIM = "mcp-shim"
ROLE_MCP_SERVER = "mcp-server"
ROLE_MCP_WATCHDOG = "mcp-watchdog"
ROLE_BROWSER = "browser"
ROLE_OTHER = "other"

FAMILY_ROLES = frozenset(
    {ROLE_BRIDGE, ROLE_MCP_LAUNCHER, ROLE_MCP_SHIM, ROLE_MCP_SERVER, ROLE_MCP_WATCHDOG, ROLE_BROWSER}
)

# Only these roles may ever anchor a cleanup. A stray browser process that has
# escaped its MCP server is detected and reported, but is never a root: nothing
# ties it back to a session, so cleaning it would be a guess.
ROOT_ROLES = frozenset({ROLE_BRIDGE, ROLE_MCP_LAUNCHER, ROLE_MCP_SHIM, ROLE_MCP_SERVER})

BROWSER_BINARIES = frozenset(
    {"chrome", "chromium", "chromium-browser", "chrome-headless-shell", "headless_shell", "chrome_crashpad_handler"}
)

# Browser binaries only count as family members when they were launched from a
# browser-automation cache, not from a user's own installed browser.
BROWSER_ROOT_MARKERS = (
    "/.cache/ms-playwright/",
    "/.cache/puppeteer/",
    "/.cache/chrome-devtools-mcp/",
    "/.cache/chrome-for-testing/",
)

WATCHDOG_PARENT_RE = re.compile(r"^--parent-pid=(\d+)$")
SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class RawProcess:
    """A process as read from the inventory source, argv included."""

    pid: int
    ppid: int
    pgid: int
    sid: int
    uid: int
    comm: str
    identity: str
    age_seconds: float
    rss_kb: int
    cwd: str | None
    cwd_missing: bool
    exe: str | None
    argv: tuple[str, ...]


@dataclass(frozen=True)
class Process:
    """A classified process. argv is deliberately dropped.

    Command lines carry task briefs, URLs, and occasionally credentials. They
    are read once for structural classification and then discarded, so no code
    path downstream of this type can put them into a log line or the state file.
    """

    pid: int
    ppid: int
    pgid: int
    sid: int
    uid: int
    comm: str
    identity: str
    age_seconds: float
    rss_kb: int
    cwd: str | None
    cwd_missing: bool
    exe_name: str | None
    role: str
    watchdog_parent_pid: int | None = None


def _basename(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.basename(path.split(" (deleted)")[0])


def _tokens(argv: Sequence[str]) -> tuple[str, ...]:
    """Flatten argv into whitespace-separated tokens.

    Both `npm` and `chrome-devtools-mcp` overwrite their process title, which
    collapses their whole command line into argv[0] and pads the rest with
    empty slots. Splitting on whitespace recovers the individual tokens so
    classification survives a rewritten title.
    """
    flattened: list[str] = []
    for element in argv:
        flattened.extend(element.split())
    return tuple(flattened)


def _has_script(tokens: Sequence[str], suffix: str, must_contain: str | None = None) -> bool:
    """True when some token is a path ending in `suffix`.

    This is a structural test against an installed script path, not a match on
    a process name, so renaming a process title cannot fake it and cannot hide
    it.
    """
    for token in tokens:
        if not token.endswith(suffix):
            continue
        if must_contain is not None and must_contain not in token:
            continue
        return True
    return False


def classify(raw: RawProcess) -> Process:
    """Assign a structural role and drop argv."""
    tokens = _tokens(raw.argv)
    exe_name = _basename(raw.exe)
    is_node = bool(exe_name and exe_name.startswith("node"))
    first = tokens[0] if tokens else ""
    role = ROLE_OTHER
    watchdog_parent: int | None = None

    if is_node and _has_script(tokens, "chrome-devtools-axi-bridge.js", "/chrome-devtools-axi/"):
        role = ROLE_BRIDGE
    elif is_node and _has_script(tokens, "/telemetry/watchdog/main.js", "/chrome-devtools-mcp/"):
        role = ROLE_MCP_WATCHDOG
        for token in tokens:
            match = WATCHDOG_PARENT_RE.match(token)
            if match:
                watchdog_parent = int(match.group(1))
                break
    elif is_node and _has_script(tokens, "/chrome-devtools-mcp/build/src/index.js"):
        role = ROLE_MCP_SERVER
    elif is_node and _basename(first) == "npm" and any(
        token == "chrome-devtools-mcp" or token.startswith("chrome-devtools-mcp@")
        for token in tokens
    ):
        role = ROLE_MCP_LAUNCHER
    elif exe_name in {"sh", "dash", "bash"} and any(
        "chrome-devtools-mcp" in token for token in tokens
    ):
        role = ROLE_MCP_SHIM
    elif is_node and first.strip('"') == "chrome-devtools-mcp":
        # chrome-devtools-mcp overwrites its own process title, so the script
        # path is no longer visible. Treated as the MCP server, but every kill
        # still needs the same ownership proof as any other root.
        role = ROLE_MCP_SERVER
    elif exe_name in BROWSER_BINARIES and raw.exe and any(
        marker in raw.exe for marker in BROWSER_ROOT_MARKERS
    ):
        role = ROLE_BROWSER

    return Process(
        pid=raw.pid,
        ppid=raw.ppid,
        pgid=raw.pgid,
        sid=raw.sid,
        uid=raw.uid,
        comm=raw.comm,
        identity=raw.identity,
        age_seconds=raw.age_seconds,
        rss_kb=raw.rss_kb,
        cwd=raw.cwd,
        cwd_missing=raw.cwd_missing,
        exe_name=exe_name,
        role=role,
        watchdog_parent_pid=watchdog_parent,
    )


@dataclass(frozen=True)
class PressureReading:
    """Memory pressure stall information, as the kernel averages it.

    `some` is the share of wall time in which at least one task was stalled on
    memory. These are the same figures `sar -q MEM` records as %smem.
    """

    some_avg10: float
    some_avg60: float
    full_avg60: float


PRESSURE_FIELD_RE = re.compile(r"(avg10|avg60|avg300)=([0-9]+(?:\.[0-9]+)?)")


def parse_pressure(text: str) -> PressureReading | None:
    """Parse /proc/pressure/memory, or None when it is not a PSI document."""
    rows: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        kind, _, rest = line.partition(" ")
        if kind in {"some", "full"}:
            rows[kind] = {name: float(value) for name, value in PRESSURE_FIELD_RE.findall(rest)}
    some = rows.get("some", {})
    if "avg10" not in some or "avg60" not in some:
        return None
    return PressureReading(
        some_avg10=some["avg10"],
        some_avg60=some["avg60"],
        full_avg60=rows.get("full", {}).get("avg60", 0.0),
    )


@dataclass(frozen=True)
class MemoryFacts:
    """Everything launch admission reads: two small /proc files, no processes."""

    meminfo: dict[str, int]
    pressure: PressureReading | None


@dataclass(frozen=True)
class Inventory:
    processes: tuple[Process, ...]
    meminfo: dict[str, int]
    tcp_states: dict[int, frozenset[str]]
    pressure: PressureReading | None = None

    def by_pid(self) -> dict[int, Process]:
        return {process.pid: process for process in self.processes}

    def children_of(self) -> dict[int, list[Process]]:
        children: dict[int, list[Process]] = {}
        for process in self.processes:
            children.setdefault(process.ppid, []).append(process)
        return children


# ---------------------------------------------------------------------------
# Inventory sources
# ---------------------------------------------------------------------------


class ProcSource:
    """Reads the live host through /proc. Never writes anything."""

    def __init__(self, proc_root: str = "/proc") -> None:
        self.proc_root = Path(proc_root)
        self._clock_ticks = os.sysconf("SC_CLK_TCK")

    def _boot_time(self) -> float:
        for line in (self.proc_root / "stat").read_text(encoding="utf-8").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
        raise RuntimeError("/proc/stat does not report btime")

    def read(self) -> Inventory:
        boot_time = self._boot_time()
        now = time.time()
        processes: list[Process] = []
        for entry in self.proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            raw = self._read_process(entry, boot_time, now)
            if raw is not None:
                processes.append(classify(raw))
        return Inventory(
            processes=tuple(processes),
            meminfo=self._read_meminfo(),
            tcp_states=self._read_tcp_states(),
            pressure=self._read_pressure(),
        )

    def read_memory_facts(self) -> MemoryFacts:
        """Read only what launch admission needs. Never walks the process table.

        An unreadable /proc/meminfo yields an empty reading rather than an
        exception, so the admission decision can name the missing telemetry.
        """
        try:
            meminfo = self._read_meminfo()
        except (OSError, ValueError):
            meminfo = {}
        return MemoryFacts(meminfo=meminfo, pressure=self._read_pressure())

    def _read_pressure(self) -> PressureReading | None:
        # PSI is optional: a kernel built without it simply has no such file.
        try:
            text = (self.proc_root / "pressure" / "memory").read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            return parse_pressure(text)
        except ValueError:
            return None

    def _read_process(self, entry: Path, boot_time: float, now: float) -> RawProcess | None:
        try:
            stat_text = (entry / "stat").read_text(encoding="utf-8")
            status_text = (entry / "status").read_text(encoding="utf-8")
            argv_bytes = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            return None

        close = stat_text.rfind(")")
        if close < 0:
            return None
        comm = stat_text[stat_text.find("(") + 1 : close]
        fields = stat_text[close + 2 :].split()
        # Field numbering follows proc(5): fields[0] is state (field 3).
        try:
            ppid = int(fields[1])
            pgid = int(fields[2])
            sid = int(fields[3])
            start_ticks = int(fields[19])
        except (IndexError, ValueError):
            return None

        uid = -1
        rss_kb = 0
        for line in status_text.splitlines():
            if line.startswith("Uid:"):
                uid = int(line.split()[1])
            elif line.startswith("VmRSS:"):
                rss_kb = int(line.split()[1])

        cwd: str | None = None
        cwd_missing = False
        try:
            link = os.readlink(entry / "cwd")
            if link.endswith(" (deleted)"):
                cwd = link[: -len(" (deleted)")]
                cwd_missing = True
            else:
                cwd = link
                cwd_missing = not os.path.isdir(link)
        except OSError:
            cwd = None

        exe: str | None = None
        try:
            exe = os.readlink(entry / "exe")
        except OSError:
            exe = None

        argv = tuple(part for part in argv_bytes.decode("utf-8", "replace").split("\0") if part)
        age = now - (boot_time + start_ticks / self._clock_ticks)
        return RawProcess(
            pid=int(entry.name),
            ppid=ppid,
            pgid=pgid,
            sid=sid,
            uid=uid,
            comm=comm,
            identity=str(start_ticks),
            age_seconds=max(age, 0.0),
            rss_kb=rss_kb,
            cwd=cwd,
            cwd_missing=cwd_missing,
            exe=exe,
            argv=argv,
        )

    def _read_meminfo(self) -> dict[str, int]:
        values: dict[str, int] = {}
        for line in (self.proc_root / "meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0])
        return values

    def _read_tcp_states(self) -> dict[int, frozenset[str]]:
        """Map local TCP port to the set of socket states seen on it.

        Only the port and the state are extracted; no peer address, no inode,
        and no owning process is recorded.
        """
        states: dict[int, set[str]] = {}
        for name in ("net/tcp", "net/tcp6"):
            path = self.proc_root / name
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines()[1:]:
                fields = line.split()
                if len(fields) < 4:
                    continue
                for address in (fields[1], fields[2]):
                    _, _, port_hex = address.rpartition(":")
                    try:
                        port = int(port_hex, 16)
                    except ValueError:
                        continue
                    if port:
                        states.setdefault(port, set()).add(
                            "LISTEN" if fields[3] == "0A" else "CONNECTED"
                        )
        return {port: frozenset(seen) for port, seen in states.items()}


class FixtureSource:
    """Reads a synthetic inventory from a JSON document.

    Tests drive the entire decision pipeline through this so no real process is
    ever inspected or signalled.
    """

    def __init__(self, document: dict) -> None:
        self.document = document
        self.dead: set[int] = set()

    def read(self) -> Inventory:
        processes = []
        for record in self.document.get("processes", []):
            if record["pid"] in self.dead:
                continue
            raw = RawProcess(
                pid=int(record["pid"]),
                ppid=int(record.get("ppid", 1)),
                pgid=int(record.get("pgid", record["pid"])),
                sid=int(record.get("sid", record["pid"])),
                uid=int(record.get("uid", 1001)),
                comm=str(record.get("comm", "")),
                identity=str(record.get("identity", record["pid"])),
                age_seconds=float(record.get("age_seconds", 0)),
                rss_kb=int(record.get("rss_kb", 0)),
                cwd=record.get("cwd"),
                cwd_missing=bool(record.get("cwd_missing", False)),
                exe=record.get("exe"),
                argv=tuple(record.get("argv", [])),
            )
            processes.append(classify(raw))
        tcp_states: dict[int, frozenset[str]] = {}
        for entry in self.document.get("tcp", []):
            port = int(entry["port"])
            tcp_states.setdefault(port, frozenset())
            tcp_states[port] = frozenset(set(tcp_states[port]) | {str(entry["state"])})
        return Inventory(
            processes=tuple(processes),
            meminfo=dict(self.document.get("meminfo", {})),
            tcp_states=tcp_states,
            pressure=self._pressure(),
        )

    def read_memory_facts(self) -> MemoryFacts:
        return MemoryFacts(
            meminfo=dict(self.document.get("meminfo", {})), pressure=self._pressure()
        )

    def _pressure(self) -> PressureReading | None:
        record = self.document.get("pressure")
        if not isinstance(record, dict):
            return None
        return PressureReading(
            some_avg10=float(record.get("some_avg10", 0.0)),
            some_avg60=float(record.get("some_avg60", 0.0)),
            full_avg60=float(record.get("full_avg60", 0.0)),
        )


# ---------------------------------------------------------------------------
# Signalling
# ---------------------------------------------------------------------------


class RealSignaller:
    """Sends real signals, one verified PID at a time.

    There is no group kill and no pattern kill anywhere in this guard. Each
    signal names one PID whose identity was re-checked immediately beforehand.
    """

    def send(self, pid: int, sig: int) -> str:
        if pid <= 1:
            return "refused"
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return "gone"
        except PermissionError:
            return "denied"
        except OSError as exc:  # pragma: no cover - defensive
            if exc.errno == errno.ESRCH:
                return "gone"
            return "error"
        return "sent"


class RecordingSignaller:
    """Records signals and simulates their effect against a FixtureSource."""

    def __init__(self, source: FixtureSource, term_survivors: Iterable[int] = ()) -> None:
        self.source = source
        self.term_survivors = set(term_survivors)
        self.sent: list[tuple[int, int]] = []

    def send(self, pid: int, sig: int) -> str:
        self.sent.append((pid, sig))
        if pid <= 1:
            return "refused"
        if sig == signal.SIGTERM and pid in self.term_survivors:
            return "sent"
        self.source.dead.add(pid)
        return "sent"


# ---------------------------------------------------------------------------
# Ownership oracles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionBinding:
    name: str
    port: int


def read_session_bindings(axi_state_dir: str) -> dict[int, SessionBinding]:
    """Map bridge PID to the named session that registered it.

    chrome-devtools-axi writes `{"pid":N,"port":P}` into `bridge.pid` under the
    session state directory. A bridge that no file claims cannot be named, and
    an unnameable bridge is treated as ambiguous rather than as a leak.
    """
    bindings: dict[int, SessionBinding] = {}
    base = Path(axi_state_dir)
    candidates = [(base / "bridge.pid", "default")]
    sessions_dir = base / "sessions"
    try:
        for entry in sorted(sessions_dir.iterdir()):
            if entry.is_dir():
                candidates.append((entry / "bridge.pid", entry.name))
    except OSError:
        pass

    for pid_file, name in candidates:
        if not SESSION_NAME_RE.match(name):
            continue
        try:
            document = json.loads(pid_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pid = document.get("pid")
        port = document.get("port")
        if isinstance(pid, int) and isinstance(port, int) and pid > 1 and 0 < port < 65536:
            bindings[pid] = SessionBinding(name=name, port=port)
    return bindings


@dataclass(frozen=True)
class TaskOwnership:
    task: str
    status_state: str | None
    status_age_seconds: float | None
    busy: bool


TERMINAL_STATES = frozenset({"done", "failed"})
BUSY_TIMESTAMP_RE = re.compile(r"(?:^|\s)ts=(\d+)(?:\s|$)")


def read_worktree_owners(
    firstmate_state_dir: str, now: float, busy_fresh_seconds: float
) -> dict[str, TaskOwnership]:
    """Map worktree path to the FirstMate task that claims it.

    `<task>.meta` carries a `worktree=` line, `<task>.status` carries the task's
    own append-only state log, and `<task>.busy-state` carries a live busy flag.
    A task is only treated as finished when its last status line is `done:` or
    `failed:` and its busy flag is not fresh. Every other shape - including
    `blocked:`, `paused:`, `needs-decision:`, an unreadable file, or a missing
    one - leaves the worktree protected.
    """
    owners: dict[str, TaskOwnership] = {}
    state_dir = Path(firstmate_state_dir)
    try:
        meta_files = sorted(state_dir.glob("*.meta"))
    except OSError:
        return owners

    for meta_file in meta_files:
        task = meta_file.name[: -len(".meta")]
        worktree: str | None = None
        try:
            for line in meta_file.read_text(encoding="utf-8").splitlines():
                if line.startswith("worktree="):
                    worktree = line[len("worktree=") :].strip()
                    break
        except OSError:
            continue
        if not worktree:
            continue

        status_file = state_dir / f"{task}.status"
        status_state: str | None = None
        status_age: float | None = None
        try:
            lines = [
                line.strip()
                for line in status_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if lines:
                status_state = lines[-1].split(":", 1)[0].strip()
            status_age = now - status_file.stat().st_mtime
        except OSError:
            status_state = None

        # A busy flag protects only while it is fresh. An agent that died mid
        # turn leaves the flag set forever, and an indefinitely protected
        # worktree would make the guard useless against the leak it exists for.
        # A stale flag simply stops protecting; the terminal-status and
        # live-owner checks still have to pass on their own.
        busy = False
        busy_file = state_dir / f"{task}.busy-state"
        try:
            busy_text = busy_file.read_text(encoding="utf-8").strip()
            if "state=busy" in busy_text:
                stamp = BUSY_TIMESTAMP_RE.search(busy_text)
                age = now - float(stamp.group(1)) if stamp else 0.0
                busy = age <= busy_fresh_seconds
        except (OSError, ValueError):
            busy = False

        existing = owners.get(worktree)
        candidate = TaskOwnership(
            task=task,
            status_state=status_state,
            status_age_seconds=status_age,
            busy=busy,
        )
        # When several tasks claim one worktree, the most protective wins.
        if existing is None or _is_more_protective(candidate, existing):
            owners[worktree] = candidate
    return owners


def _is_more_protective(candidate: TaskOwnership, existing: TaskOwnership) -> bool:
    def rank(ownership: TaskOwnership) -> int:
        if ownership.busy:
            return 2
        if ownership.status_state not in TERMINAL_STATES:
            return 1
        return 0

    return rank(candidate) > rank(existing)


def resolve_worktree(path: str | None, worktree_root: str) -> str | None:
    """Reduce a working directory to the Treehouse worktree that contains it.

    Pool worktrees look like `<root>/<project>-<hash>/<slot>/<project>`; only
    that exact shape is accepted, so a process running anywhere else resolves to
    no worktree and is therefore never cleanable.
    """
    if not path:
        return None
    root = os.path.normpath(worktree_root)
    normalised = os.path.normpath(path)
    if not (normalised == root or normalised.startswith(root + os.sep)):
        return None
    relative = os.path.relpath(normalised, root).split(os.sep)
    if len(relative) < 3 or relative[0] in {"", ".", ".."}:
        return None
    return os.path.join(root, relative[0], relative[1], relative[2])


# ---------------------------------------------------------------------------
# Memory observation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryReading:
    total_kb: int
    available_kb: int
    swap_total_kb: int
    swap_used_kb: int

    @property
    def available_percent(self) -> float:
        return 100.0 * self.available_kb / self.total_kb if self.total_kb else 100.0

    @property
    def swap_used_percent(self) -> float:
        return 100.0 * self.swap_used_kb / self.swap_total_kb if self.swap_total_kb else 0.0


def read_memory(meminfo: dict[str, int]) -> MemoryReading:
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    return MemoryReading(
        total_kb=meminfo.get("MemTotal", 0),
        available_kb=meminfo.get("MemAvailable", 0),
        swap_total_kb=swap_total,
        swap_used_kb=max(swap_total - swap_free, 0),
    )


def memory_level(reading: MemoryReading, config: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    """Classify memory pressure. Purely observational.

    Nothing this function returns is ever consulted when deciding whether a
    process may be killed.
    """
    reasons: list[str] = []
    level = "ok"
    if reading.available_percent <= float(config["mem_available_critical_percent"]):
        level = "critical"
        reasons.append("mem-available-critical")
    elif reading.available_percent <= float(config["mem_available_warn_percent"]):
        level = "warn"
        reasons.append("mem-available-warn")

    if reading.swap_total_kb:
        if reading.swap_used_percent >= float(config["swap_used_critical_percent"]):
            level = "critical"
            reasons.append("swap-used-critical")
        elif reading.swap_used_percent >= float(config["swap_used_warn_percent"]):
            level = "critical" if level == "critical" else "warn"
            reasons.append("swap-used-warn")
    elif level != "ok":
        reasons.append("no-swap-headroom")
    return level, tuple(reasons)


# ---------------------------------------------------------------------------
# Launch admission
# ---------------------------------------------------------------------------

EXIT_ADMIT = 0
EXIT_REFUSED_TELEMETRY = 69  # EX_UNAVAILABLE: the facts could not be read
EXIT_REFUSED_PRESSURE = 75  # EX_TEMPFAIL: retry once memory has been freed

REASON_GATE_DISABLED = "launch-gate-disabled"
REASON_TELEMETRY = "memory-telemetry-unavailable"
REASON_HEADROOM = "projected-headroom-below-floor"
REASON_SWAP_EXHAUSTED = "swap-cushion-exhausted"
REASON_NO_SWAP = "no-swap-cushion"
REASON_STALL = "memory-stall"


def telemetry_problems(meminfo: dict[str, int]) -> tuple[str, ...]:
    """Name what is missing from a memory reading, or () when it is usable.

    `read_memory` deliberately tolerates gaps so observation can still report.
    Admission cannot: a reading with no MemTotal looks 100% available, and
    admitting on a number nobody measured is how a host gets tipped over.
    """
    problems: list[str] = []
    total = meminfo.get("MemTotal")
    available = meminfo.get("MemAvailable")
    if not isinstance(total, int) or total <= 0:
        problems.append("MemTotal")
    if not isinstance(available, int) or available < 0:
        problems.append("MemAvailable")
    if not problems and available > total:
        problems.append("MemAvailable-exceeds-MemTotal")
    return tuple(problems)


@dataclass(frozen=True)
class Admission:
    """Whether one more worker may start. Refusing is the only effect."""

    decision: str  # "admit" or "refuse"
    level: str  # ok, warn, critical, or unknown when telemetry is unavailable
    reasons: tuple[str, ...]
    mem_total_kb: int = 0
    mem_available_kb: int = 0
    pending_reserved_kb: int = 0
    worker_reserve_kb: int = 0
    projected_available_kb: int = 0
    required_floor_kb: int = 0
    floor_percent: int = 0
    swap_used_percent: float = 0.0
    swap_cushion: bool = False
    memory_stall_avg60: float | None = None
    missing: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.decision == "admit"

    @property
    def exit_code(self) -> int:
        if self.admitted:
            return EXIT_ADMIT
        return EXIT_REFUSED_TELEMETRY if REASON_TELEMETRY in self.reasons else EXIT_REFUSED_PRESSURE

    def to_state(self) -> dict:
        return {
            "decision": self.decision,
            "level": self.level,
            "reasons": list(self.reasons),
            "mem_available_kb": self.mem_available_kb,
            "pending_reserved_kb": self.pending_reserved_kb,
            "worker_reserve_kb": self.worker_reserve_kb,
            "projected_available_kb": self.projected_available_kb,
            "required_floor_kb": self.required_floor_kb,
            "floor_percent": self.floor_percent,
            "swap_used_percent": round(self.swap_used_percent, 1),
            "swap_cushion": self.swap_cushion,
            "memory_stall_avg60": self.memory_stall_avg60,
            "missing_telemetry": list(self.missing),
        }


def decide_admission(
    facts: MemoryFacts, pending_reserved_kb: int, config: dict[str, object]
) -> Admission:
    """Decide from /proc facts alone whether one more worker fits.

    The question is about the launch that has not happened yet, so the test is
    on *projected* headroom: MemAvailable, less the reserve already promised to
    workers admitted moments ago, less the budget for this one. What remains
    must clear a floor, because MemAvailable is mostly page cache and that
    cache is the running fleet's executable text - the 2026-09-17 wedge began
    when it was evicted, not when memory ran out.

    Swap raises the floor rather than refusing outright. Swap that is absent or
    already past the guard's own critical mark is no cushion, so the floor is
    the stricter one; a host with ample RAM and old cold pages parked in swap
    is still admitted.

    Pure function of its arguments. It reads no process, names no process, and
    nothing it returns is ever consulted when deciding whether to kill.
    """
    stall = facts.pressure.some_avg60 if facts.pressure is not None else None
    missing = telemetry_problems(facts.meminfo)

    if not bool(config["launch_gate_enabled"]):
        return Admission(
            decision="admit",
            level="unknown" if missing else memory_level(read_memory(facts.meminfo), config)[0],
            reasons=(REASON_GATE_DISABLED,),
            memory_stall_avg60=stall,
            missing=missing,
        )

    if missing:
        # Refusing a launch loses nothing and can be retried. Admitting blind
        # can wedge the host and put every running worker's work at risk.
        return Admission(
            decision="refuse",
            level="unknown",
            reasons=(REASON_TELEMETRY,),
            memory_stall_avg60=stall,
            missing=missing,
        )

    reading = read_memory(facts.meminfo)
    level, level_reasons = memory_level(reading, config)

    swap_cushion = bool(reading.swap_total_kb) and reading.swap_used_percent < float(
        config["swap_used_critical_percent"]
    )
    floor_percent = int(
        config["launch_min_mem_available_percent"]
        if swap_cushion
        else config["launch_min_mem_available_no_swap_percent"]
    )
    required_floor_kb = reading.total_kb * floor_percent // 100
    reserve_kb = int(config["launch_worker_reserve_kb"])
    pending_kb = max(int(pending_reserved_kb), 0)
    projected_kb = reading.available_kb - pending_kb - reserve_kb

    refusals: list[str] = []
    if projected_kb < required_floor_kb:
        refusals.append(REASON_HEADROOM)
        if not swap_cushion:
            refusals.append(REASON_SWAP_EXHAUSTED if reading.swap_total_kb else REASON_NO_SWAP)
    if stall is not None and stall >= float(config["launch_max_memory_stall_percent"]):
        refusals.append(REASON_STALL)

    return Admission(
        decision="refuse" if refusals else "admit",
        level=level,
        # An admitted launch still carries the observation reasons, so the
        # launcher hears "warn" long before it ever hears "refuse".
        reasons=tuple(refusals) if refusals else level_reasons,
        mem_total_kb=reading.total_kb,
        mem_available_kb=reading.available_kb,
        pending_reserved_kb=pending_kb,
        worker_reserve_kb=reserve_kb,
        projected_available_kb=projected_kb,
        required_floor_kb=required_floor_kb,
        floor_percent=floor_percent,
        swap_used_percent=reading.swap_used_percent,
        swap_cushion=swap_cushion,
        memory_stall_avg60=stall,
    )


def emit_admission(reporter: Reporter, admission: Admission, ledger: str) -> None:
    reporter.emit(
        "admission",
        decision=admission.decision,
        level=admission.level,
        mem_total_kb=admission.mem_total_kb,
        mem_available_kb=admission.mem_available_kb,
        pending_reserved_kb=admission.pending_reserved_kb,
        worker_reserve_kb=admission.worker_reserve_kb,
        projected_available_kb=admission.projected_available_kb,
        required_floor_kb=admission.required_floor_kb,
        floor_percent=admission.floor_percent,
        swap_used_percent=admission.swap_used_percent,
        swap_cushion="yes" if admission.swap_cushion else "no",
        memory_stall_avg60=admission.memory_stall_avg60,
        ledger=ledger,
        missing=list(admission.missing) or None,
        reasons=list(admission.reasons) or None,
    )


def explain_refusal(admission: Admission) -> list[str]:
    """Operator-facing sentences for a refused launch. Numbers only, no names."""

    def mb(kilobytes: int) -> int:
        return max(kilobytes, 0) // 1024

    lines = ["launch REFUSED: this host has no safe headroom for another worker."]
    if REASON_TELEMETRY in admission.reasons:
        lines.append(
            "  /proc/meminfo did not yield " + ", ".join(admission.missing)
            + "; admitting on an unmeasured host is not safe."
        )
    if REASON_HEADROOM in admission.reasons:
        pending = (
            f" and {mb(admission.pending_reserved_kb)} MB is already promised to workers"
            " admitted in the last few minutes"
            if admission.pending_reserved_kb
            else ""
        )
        lines.append(
            f"  MemAvailable is {mb(admission.mem_available_kb)} MB{pending}; one more worker is"
            f" budgeted at {mb(admission.worker_reserve_kb)} MB, leaving"
            f" {mb(admission.projected_available_kb)} MB."
        )
        if REASON_SWAP_EXHAUSTED in admission.reasons:
            why = f" because swap is {admission.swap_used_percent:.0f}% used and is no longer a cushion"
        elif REASON_NO_SWAP in admission.reasons:
            why = " because this host has no swap cushion"
        else:
            why = ""
        lines.append(
            f"  The floor is {mb(admission.required_floor_kb)} MB"
            f" ({admission.floor_percent}% of RAM){why}."
        )
    if REASON_STALL in admission.reasons:
        lines.append(
            f"  Tasks spent {admission.memory_stall_avg60:.1f}% of the last minute stalled on"
            " memory (PSI some avg60): the host is already thrashing."
        )
    lines.extend(
        [
            "  Nothing was started and nothing was stopped. This gate never kills a worker.",
            "  Let running workers finish, or deliberately land or stow one, then retry.",
            "  Inspect: bibi-memory-guard admit --no-reserve ; free -m ; sar -r -S -B -q MEM 1 3",
        ]
    )
    return lines


class LaunchLedger:
    """Short-lived record of headroom promised to just-admitted workers.

    A worker takes minutes to grow into its working set, so a burst of launches
    would each read the same comfortable MemAvailable. Every admission
    therefore leaves a reservation that later admissions subtract until it
    expires and /proc shows the memory for real.

    The ledger lives in the invoking user's runtime directory and holds only
    `<epoch> <kb>` lines. It is an enhancement to the /proc facts, never a
    substitute: when it cannot be used the decision proceeds on /proc alone and
    says so.
    """

    FILE_NAME = "launch-reservations"
    MAX_ENTRIES = 256
    LOCK_ATTEMPTS = 20
    LOCK_RETRY_SECONDS = 0.05

    def __init__(
        self,
        directory: str | None,
        window_seconds: float,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        writable: bool = True,
    ) -> None:
        self.directory = directory
        self.window_seconds = window_seconds
        # A pure query reads existing reservations but creates nothing.
        self.writable = writable
        self.now = now
        self.sleep = sleep
        self.status = "unavailable"
        self._handle = None
        self._entries: list[tuple[float, int]] = []

    @staticmethod
    def default_directory(configured: str, environ: dict[str, str], uid: int) -> str | None:
        if configured:
            return configured
        runtime = environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
        if os.path.isdir(runtime) and os.access(runtime, os.W_OK):
            return os.path.join(runtime, "bibi-memory-guard")
        return None

    def __enter__(self) -> LaunchLedger:
        if not self.directory:
            return self
        path = os.path.join(self.directory, self.FILE_NAME)
        try:
            if self.writable:
                os.makedirs(self.directory, mode=0o700, exist_ok=True)
                flags = os.O_RDWR | os.O_CREAT
            else:
                flags = os.O_RDONLY
            descriptor = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        except FileNotFoundError:
            if not self.writable:
                self.status = "empty"
            return self
        except OSError:
            return self
        handle = os.fdopen(descriptor, "r+" if self.writable else "r", encoding="utf-8")
        lock = fcntl.LOCK_EX if self.writable else fcntl.LOCK_SH
        # Bounded, non-blocking attempts: a gate that can hang on its own lock
        # would be one more thing wedged on a host that is already struggling.
        for _ in range(self.LOCK_ATTEMPTS):
            try:
                fcntl.flock(handle.fileno(), lock | fcntl.LOCK_NB)
                break
            except OSError:
                self.sleep(self.LOCK_RETRY_SECONDS)
        else:
            handle.close()
            self.status = "busy"
            return self
        self._handle = handle
        self._entries = self._read(handle)
        self.status = "ok"
        return self

    def __exit__(self, *_exc) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def _read(self, handle) -> list[tuple[float, int]]:
        now = self.now()
        entries: list[tuple[float, int]] = []
        try:
            lines = handle.read().splitlines()[-self.MAX_ENTRIES :]
        except (OSError, UnicodeDecodeError):
            return entries
        for line in lines:
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                stamp, kilobytes = float(parts[0]), int(parts[1])
            except ValueError:
                continue
            # A stamp from the future is a clock step, not a promise to honour.
            if kilobytes > 0 and -1.0 <= now - stamp < self.window_seconds:
                entries.append((stamp, kilobytes))
        return entries

    @property
    def pending_kb(self) -> int:
        return sum(kilobytes for _, kilobytes in self._entries)

    def reserve(self, kilobytes: int) -> bool:
        if self._handle is None or not self.writable or kilobytes <= 0:
            return False
        self._entries.append((self.now(), kilobytes))
        try:
            self._handle.seek(0)
            self._handle.truncate()
            self._handle.write(
                "".join(f"{stamp:.0f} {size}\n" for stamp, size in self._entries[-self.MAX_ENTRIES :])
            )
            self._handle.flush()
        except OSError:
            self.status = "write-failed"
            return False
        return True


def admit_launch(
    facts: MemoryFacts,
    config: dict[str, object],
    ledger: LaunchLedger,
    reserve: bool,
) -> Admission:
    """Decide and, only when admitting for real, record the reservation.

    The ledger lock spans read, decision, and write, so two launches racing
    each other cannot both spend the same headroom.
    """
    with ledger:
        admission = decide_admission(facts, ledger.pending_kb, config)
        if reserve and admission.admitted and bool(config["launch_gate_enabled"]):
            ledger.reserve(admission.worker_reserve_kb)
    return admission


# ---------------------------------------------------------------------------
# Tree discovery and eligibility
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    root: Process
    members: tuple[Process, ...]
    session: SessionBinding | None
    worktree: str | None
    worktree_missing: bool
    owner_task: TaskOwnership | None
    live_owner_pid: int | None
    decision: str = "pending"
    reason: str = ""

    @property
    def rss_kb(self) -> int:
        return sum(member.rss_kb for member in self.members)

    @property
    def roles(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for member in self.members:
            counts[member.role] = counts.get(member.role, 0) + 1
        return counts


def collect_tree(root: Process, children: dict[int, list[Process]], limit: int) -> tuple[Process, ...] | None:
    """Breadth-first descendants of `root`, or None when the tree is too large.

    An unexpectedly wide tree is treated as ambiguous. Refusing to reason about
    it is cheaper than mis-scoping a kill.
    """
    collected: list[Process] = [root]
    seen = {root.pid}
    queue = [root.pid]
    while queue:
        current = queue.pop(0)
        for child in children.get(current, ()):
            if child.pid in seen:
                continue
            seen.add(child.pid)
            collected.append(child)
            queue.append(child.pid)
            if len(collected) > limit:
                return None
    return tuple(collected)


def find_candidates(
    inventory: Inventory,
    config: dict[str, object],
    session_bindings: dict[int, SessionBinding],
    worktree_owners: dict[str, TaskOwnership],
    owner_uid: int,
) -> list[Candidate]:
    """Locate every leak-family root and attach its ownership evidence."""
    by_pid = inventory.by_pid()
    children = inventory.children_of()
    limit = int(config["leak_max_tree_processes"])
    owner_commands = set(config["owner_commands"])
    worktree_root = str(config["worktree_root"])

    candidates: list[Candidate] = []
    for process in inventory.processes:
        if process.role not in ROOT_ROLES:
            continue
        parent = by_pid.get(process.ppid)
        if parent is not None and parent.role in FAMILY_ROLES:
            # Not a root: some family member above it already owns this subtree.
            continue

        members = collect_tree(process, children, limit)
        if members is None:
            candidates.append(
                Candidate(
                    root=process,
                    members=(process,),
                    session=session_bindings.get(process.pid),
                    worktree=None,
                    worktree_missing=False,
                    owner_task=None,
                    live_owner_pid=None,
                    decision="refused",
                    reason="tree-too-large",
                )
            )
            continue

        worktree = None
        worktree_missing = False
        for member in members:
            resolved = resolve_worktree(member.cwd, worktree_root)
            if resolved:
                worktree = resolved
                worktree_missing = member.cwd_missing or not os.path.isdir(worktree)
                break

        member_pids = {member.pid for member in members}
        live_owner_pid = None
        if worktree:
            for other in inventory.processes:
                if other.pid in member_pids or other.uid != owner_uid:
                    continue
                if other.comm not in owner_commands and (other.exe_name or "") not in owner_commands:
                    continue
                if resolve_worktree(other.cwd, worktree_root) == worktree:
                    live_owner_pid = other.pid
                    break

        candidates.append(
            Candidate(
                root=process,
                members=members,
                session=session_bindings.get(process.pid),
                worktree=worktree,
                worktree_missing=worktree_missing,
                owner_task=worktree_owners.get(worktree) if worktree else None,
                live_owner_pid=live_owner_pid,
            )
        )

    return candidates


def decide(
    candidate: Candidate,
    inventory: Inventory,
    config: dict[str, object],
    owner_uid: int,
    self_ancestry: frozenset[int],
) -> Candidate:
    """Apply the conservative eligibility policy to one candidate tree.

    Every branch that is not a clean, positive proof of orphanhood refuses.
    """
    if candidate.decision == "refused":
        return candidate

    member_pids = {member.pid for member in candidate.members}
    if member_pids & self_ancestry:
        return replace(candidate, decision="refused", reason="self-or-ancestor-in-tree")
    if any(member.pid <= 1 for member in candidate.members):
        return replace(candidate, decision="refused", reason="init-in-tree")
    if any(member.uid != owner_uid for member in candidate.members):
        return replace(candidate, decision="refused", reason="foreign-uid-member")

    if candidate.root.ppid > 1:
        return replace(candidate, decision="refused", reason="root-still-attached")

    if candidate.root.age_seconds < float(config["leak_min_age_seconds"]):
        return replace(candidate, decision="refused", reason="root-below-age-grace")
    youngest = min(member.age_seconds for member in candidate.members)
    if youngest < float(config["leak_min_member_age_seconds"]):
        return replace(candidate, decision="refused", reason="member-below-age-grace")

    if candidate.rss_kb < int(config["leak_min_tree_rss_kb"]):
        return replace(candidate, decision="refused", reason="below-rss-floor")

    if candidate.root.role == ROLE_BRIDGE:
        if candidate.session is None:
            # A bridge no session file claims cannot be named, so its owner
            # cannot be checked. Report it; never guess.
            return replace(candidate, decision="refused", reason="unregistered-bridge")
        states = inventory.tcp_states.get(candidate.session.port, frozenset())
        if "CONNECTED" in states:
            return replace(candidate, decision="refused", reason="active-client-connection")

    if candidate.worktree_missing:
        return replace(candidate, decision="eligible", reason="owning-worktree-deleted")

    if candidate.worktree is None:
        return replace(candidate, decision="refused", reason="unresolved-worktree")

    if candidate.live_owner_pid is not None:
        return replace(candidate, decision="refused", reason="owner-process-alive")

    ownership = candidate.owner_task
    if ownership is None:
        return replace(candidate, decision="refused", reason="unclaimed-worktree")
    if ownership.busy:
        return replace(candidate, decision="refused", reason="owning-task-busy")
    if ownership.status_state is None:
        return replace(candidate, decision="refused", reason="owning-task-status-unreadable")
    if ownership.status_state not in TERMINAL_STATES:
        return replace(candidate, decision="refused", reason="owning-task-not-terminal")
    if (
        ownership.status_age_seconds is not None
        and ownership.status_age_seconds < float(config["owner_task_settled_seconds"])
    ):
        return replace(candidate, decision="refused", reason="owning-task-recently-finished")

    return replace(candidate, decision="eligible", reason="owning-task-completed")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


@dataclass
class CleanupResult:
    root_pid: int
    signalled: tuple[int, ...] = ()
    killed: tuple[int, ...] = ()
    survivors: tuple[int, ...] = ()
    skipped_identity: tuple[int, ...] = ()
    outcome: str = "no-op"
    elapsed_seconds: float = 0.0


def clean_tree(
    candidate: Candidate,
    source,
    signaller,
    config: dict[str, object],
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> CleanupResult:
    """Terminate exactly one proven tree: TERM, bounded wait, KILL survivors.

    Members are signalled leaves-first so a watchdog never observes its parent
    disappearing while it is still expected to supervise it. Every PID is
    re-read from a fresh inventory and matched on its start identity right
    before the signal, so a recycled PID can never be hit.
    """
    started = now()
    result = CleanupResult(root_pid=candidate.root.pid)
    expected = {member.pid: member.identity for member in candidate.members}
    depth = _depth_map(candidate)
    ordered = sorted(candidate.members, key=lambda member: (-depth[member.pid], member.pid))

    signalled: list[int] = []
    skipped: list[int] = []
    live = source.read().by_pid()
    for member in ordered:
        current = live.get(member.pid)
        if current is None:
            continue
        if current.identity != expected[member.pid]:
            skipped.append(member.pid)
            continue
        if signaller.send(member.pid, signal.SIGTERM) in {"sent", "gone"}:
            signalled.append(member.pid)

    grace = float(config["term_grace_seconds"])
    poll = float(config["term_poll_interval_seconds"])
    survivors = _wait_for_exit(source, expected, grace, poll, now, sleep)

    killed: list[int] = []
    for pid in sorted(survivors):
        if signaller.send(pid, signal.SIGKILL) in {"sent", "gone"}:
            killed.append(pid)

    if killed:
        survivors = _wait_for_exit(source, expected, grace, poll, now, sleep)
    else:
        survivors = _survivors(source, expected)

    result.signalled = tuple(signalled)
    result.killed = tuple(killed)
    result.survivors = tuple(sorted(survivors))
    result.skipped_identity = tuple(sorted(skipped))
    result.elapsed_seconds = round(now() - started, 3)
    result.outcome = "verified-absent" if not survivors else "survivors-remain"
    return result


def _depth_map(candidate: Candidate) -> dict[int, int]:
    parents = {member.pid: member.ppid for member in candidate.members}
    depths: dict[int, int] = {}
    for pid in parents:
        depth = 0
        cursor = pid
        while cursor in parents and parents[cursor] in parents and depth < len(parents):
            cursor = parents[cursor]
            depth += 1
        depths[pid] = depth
    return depths


def _wait_for_exit(
    source,
    expected: dict[int, str],
    grace: float,
    poll: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> set[int]:
    """Poll until the expected PIDs are gone, or the grace period expires.

    Bounded by both wall clock and poll count so a stalled or non-monotonic
    clock can never wedge the service inside its own timer window.
    """
    deadline = now() + grace
    remaining = max(1, int(grace / poll) + 1) if poll > 0 else 1
    survivors = _survivors(source, expected)
    while survivors and remaining > 0 and now() < deadline:
        sleep(poll)
        remaining -= 1
        survivors = _survivors(source, expected)
    return survivors


def _survivors(source, expected: dict[int, str]) -> set[int]:
    live = source.read().by_pid()
    return {
        pid
        for pid, identity in expected.items()
        if pid in live and live[pid].identity == identity
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class Reporter:
    """Bounded, secret-free line emitter.

    Only whitelisted scalars reach this class. Command lines and environment
    values are dropped during classification and never reconstructed.
    """

    def __init__(self, limit: int, stream=None) -> None:
        self.limit = limit
        self.stream = stream if stream is not None else sys.stdout
        self.lines: list[str] = []
        self.truncated = 0

    def emit(self, event: str, **fields: object) -> None:
        if len(self.lines) >= self.limit:
            self.truncated += 1
            return
        rendered = " ".join(f"{key}={_scalar(value)}" for key, value in fields.items())
        line = f"{event}: {rendered}" if rendered else f"{event}:"
        self.lines.append(line)
        print(line, file=self.stream)

    def finish(self) -> None:
        if self.truncated:
            note = f"note: output_truncated_lines={self.truncated} log_line_cap={self.limit}"
            self.lines.append(note)
            print(note, file=self.stream)


def _scalar(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value) or "none"
    if value is None:
        return "none"
    text = str(value)
    return text.replace(" ", "_") if " " in text else text


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@dataclass
class RunOutcome:
    mode: str
    level: str
    memory_before: MemoryReading
    memory_after: MemoryReading
    candidates: list[Candidate] = field(default_factory=list)
    cleanups: list[CleanupResult] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    admission: Admission | None = None
    exit_code: int = 0

    def to_state(self, timestamp: float) -> dict:
        return {
            "version": VERSION,
            "timestamp": int(timestamp),
            "mode": self.mode,
            "level": self.level,
            "alerts": list(self.alerts),
            "admission": self.admission.to_state() if self.admission else None,
            "memory": {
                "before": {
                    "mem_available_kb": self.memory_before.available_kb,
                    "mem_available_percent": round(self.memory_before.available_percent, 1),
                    "swap_total_kb": self.memory_before.swap_total_kb,
                    "swap_used_percent": round(self.memory_before.swap_used_percent, 1),
                },
                "after": {
                    "mem_available_kb": self.memory_after.available_kb,
                    "mem_available_percent": round(self.memory_after.available_percent, 1),
                    "swap_total_kb": self.memory_after.swap_total_kb,
                    "swap_used_percent": round(self.memory_after.swap_used_percent, 1),
                },
                "reclaimed_kb": self.memory_after.available_kb - self.memory_before.available_kb,
            },
            "trees": [
                {
                    "root_pid": candidate.root.pid,
                    "role": candidate.root.role,
                    "session": candidate.session.name if candidate.session else None,
                    "port": candidate.session.port if candidate.session else None,
                    "members": len(candidate.members),
                    "roles": candidate.roles,
                    "rss_kb": candidate.rss_kb,
                    "age_seconds": int(candidate.root.age_seconds),
                    "worktree": candidate.worktree,
                    "owning_task": candidate.owner_task.task if candidate.owner_task else None,
                    "decision": candidate.decision,
                    "reason": candidate.reason,
                }
                for candidate in self.candidates
            ],
            "cleanups": [
                {
                    "root_pid": cleanup.root_pid,
                    "terminated": list(cleanup.signalled),
                    "killed": list(cleanup.killed),
                    "survivors": list(cleanup.survivors),
                    "skipped_identity_mismatch": list(cleanup.skipped_identity),
                    "outcome": cleanup.outcome,
                    "elapsed_seconds": cleanup.elapsed_seconds,
                }
                for cleanup in self.cleanups
            ],
        }


def run(
    mode: str,
    config: dict[str, object],
    source,
    signaller,
    reporter: Reporter,
    owner_uid: int,
    self_ancestry: frozenset[int],
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> RunOutcome:
    """Observe, decide, and - only in `once` mode - act.

    `report` and `dry-run` share the identical decision pipeline with `once`;
    the only difference is whether a signal is ever sent.
    """
    inventory = source.read()
    memory_before = read_memory(inventory.meminfo)
    level, level_reasons = memory_level(memory_before, config)

    reporter.emit(
        "memory",
        mem_total_kb=memory_before.total_kb,
        mem_available_kb=memory_before.available_kb,
        mem_available_percent=memory_before.available_percent,
        swap_total_kb=memory_before.swap_total_kb,
        swap_used_percent=memory_before.swap_used_percent,
        level=level,
        reasons=list(level_reasons),
    )

    # The scheduled run records whether a launch would be admitted right now,
    # so the login banner and bibi-verify show a closed gate before anyone
    # tries to launch. It reserves nothing and, like the level above, is never
    # consulted when deciding whether a process may be killed.
    admission = decide_admission(
        MemoryFacts(meminfo=inventory.meminfo, pressure=inventory.pressure), 0, config
    )
    emit_admission(reporter, admission, ledger="not-consulted")

    family = [process for process in inventory.processes if process.role in FAMILY_ROLES]
    reporter.emit(
        "family",
        processes=len(family),
        rss_kb=sum(process.rss_kb for process in family),
        bridges=sum(1 for process in family if process.role == ROLE_BRIDGE),
        mcp=sum(1 for process in family if process.role in {ROLE_MCP_SERVER, ROLE_MCP_LAUNCHER, ROLE_MCP_SHIM}),
        watchdogs=sum(1 for process in family if process.role == ROLE_MCP_WATCHDOG),
        browsers=sum(1 for process in family if process.role == ROLE_BROWSER),
    )

    session_bindings = read_session_bindings(str(config["axi_state_dir"]))
    worktree_owners = read_worktree_owners(
        str(config["firstmate_state_dir"]),
        now(),
        float(config["busy_state_fresh_seconds"]),
    )

    candidates = find_candidates(
        inventory, config, session_bindings, worktree_owners, owner_uid
    )
    candidates = [
        decide(candidate, inventory, config, owner_uid, self_ancestry)
        for candidate in candidates
    ]
    candidates.sort(key=lambda candidate: (-candidate.rss_kb, candidate.root.pid))

    outcome = RunOutcome(
        mode=mode,
        level=level,
        memory_before=memory_before,
        memory_after=memory_before,
        candidates=candidates,
        admission=admission,
    )
    outcome.alerts.extend(level_reasons)
    if REASON_TELEMETRY in admission.reasons or admission.missing:
        outcome.alerts.append(REASON_TELEMETRY)
    if not admission.admitted:
        outcome.alerts.append("launch-admission-closed")

    # Chromium double-forks, so a browser reparented to PID 1 is the normal
    # shape for a perfectly healthy session - alerting on that alone would fire
    # every time anyone opens a browser. A browser is only worth reporting when
    # it is past the leak grace period *and* no live bridge or MCP root remains
    # in its worktree to account for it. Even then it is reported, never
    # cleaned: nothing ties a stranded browser back to a session, so scoping a
    # kill around it would be a guess.
    live_family_worktrees = {
        candidate.worktree for candidate in candidates if candidate.worktree
    }
    tree_members = {member.pid for candidate in candidates for member in candidate.members}
    stranded_browsers = [
        process
        for process in family
        if process.role == ROLE_BROWSER
        and process.ppid <= 1
        and process.pid not in tree_members
        and process.age_seconds >= float(config["leak_min_age_seconds"])
        and resolve_worktree(process.cwd, str(config["worktree_root"]))
        not in live_family_worktrees
    ]
    if stranded_browsers:
        outcome.alerts.append("stranded-browser-processes")
        reporter.emit(
            "refused",
            observation="stranded-browser",
            processes=len(stranded_browsers),
            rss_kb=sum(process.rss_kb for process in stranded_browsers),
            oldest_age_s=int(max(process.age_seconds for process in stranded_browsers)),
            reason="no-live-bridge-or-mcp-root-in-its-worktree-cannot-scope-a-kill",
        )

    cap = int(config["leak_max_trees_per_run"])
    acted = 0
    for candidate in candidates:
        reporter.emit(
            "tree",
            root=candidate.root.pid,
            role=candidate.root.role,
            session=candidate.session.name if candidate.session else None,
            port=candidate.session.port if candidate.session else None,
            members=len(candidate.members),
            rss_kb=candidate.rss_kb,
            age_s=int(candidate.root.age_seconds),
            worktree=candidate.worktree,
            task=candidate.owner_task.task if candidate.owner_task else None,
            decision=candidate.decision,
            reason=candidate.reason,
        )
        if candidate.decision != "eligible":
            continue
        if acted >= cap:
            candidate.decision = "deferred"
            candidate.reason = "run-cap-reached"
            reporter.emit("deferred", root=candidate.root.pid, reason="run-cap-reached")
            continue
        acted += 1
        if mode != "once":
            reporter.emit(
                "planned",
                root=candidate.root.pid,
                members=len(candidate.members),
                rss_kb=candidate.rss_kb,
                signal="TERM-then-KILL",
            )
            continue
        cleanup = clean_tree(candidate, source, signaller, config, now, sleep)
        outcome.cleanups.append(cleanup)
        reporter.emit(
            "action",
            root=cleanup.root_pid,
            terminated=len(cleanup.signalled),
            killed=len(cleanup.killed),
            survivors=len(cleanup.survivors),
            skipped_identity=len(cleanup.skipped_identity),
            outcome=cleanup.outcome,
            elapsed_s=cleanup.elapsed_seconds,
        )
        if cleanup.survivors:
            outcome.alerts.append("cleanup-survivors")

    if mode == "once" and outcome.cleanups:
        outcome.memory_after = read_memory(source.read().meminfo)

    eligible = sum(1 for candidate in candidates if candidate.decision == "eligible")
    refused = sum(1 for candidate in candidates if candidate.decision == "refused")
    if level != "ok" and eligible == 0:
        outcome.alerts.append("pressure-without-eligible-leak")
        reporter.emit(
            "refused",
            kind="memory-pressure",
            level=level,
            reason="no-ownership-proven-leak-low-memory-never-authorises-a-kill",
        )

    reporter.emit(
        "result",
        mode=mode,
        level=level,
        trees=len(candidates),
        eligible=eligible,
        refused=refused,
        cleaned=len(outcome.cleanups),
        mem_available_before_kb=outcome.memory_before.available_kb,
        mem_available_after_kb=outcome.memory_after.available_kb,
        reclaimed_kb=outcome.memory_after.available_kb - outcome.memory_before.available_kb,
        alerts=sorted(set(outcome.alerts)) or None,
    )
    reporter.finish()

    outcome.alerts = sorted(set(outcome.alerts))
    # A run that completed is a success even when it alerted or refused
    # everything: a failing unit would only add noise to the timer's journal.
    # A non-zero exit means the guard could not run at all.
    return outcome


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def self_ancestry(pid: int, proc_root: str = "/proc") -> frozenset[int]:
    """PIDs of this process and everything above it. Never signalled.

    Walks the parent chain directly rather than scanning every process, so the
    guard pays for its own depth and not for the whole process table.
    """
    ancestry = {pid}
    cursor = pid
    while cursor > 1 and len(ancestry) < 64:
        try:
            stat_text = Path(proc_root, str(cursor), "stat").read_text(encoding="utf-8")
        except OSError:
            break
        close = stat_text.rfind(")")
        if close < 0:
            break
        try:
            cursor = int(stat_text[close + 2 :].split()[1])
        except (IndexError, ValueError):
            break
        if cursor <= 0:
            break
        ancestry.add(cursor)
    return frozenset(ancestry)


def resolve_owner_uid(config: dict[str, object]) -> int:
    import pwd

    try:
        return pwd.getpwnam(str(config["owner_user"])).pw_uid
    except KeyError as exc:
        raise ConfigError(f"owner_user {config['owner_user']!r} does not exist") from exc


def write_state(path: str, document: dict) -> None:
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".new")
        temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(target)
    except OSError as exc:
        print(f"note: state_write_failed reason={exc.errno}", file=sys.stderr)


def acquire_lock(path: str):
    """Serialise runs so a manual invocation can never overlap the timer."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "w", encoding="utf-8")  # noqa: SIM115 - held for process lifetime
    except OSError:
        return None
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    return handle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bibi-memory-guard",
        description=(
            "Report host memory pressure, clean only ownership-proven "
            "chrome-devtools-axi / chrome-devtools-mcp leak trees, and refuse a "
            "new worker launch when the host has no headroom for it."
        ),
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="report",
        choices=["report", "dry-run", "once", "status", "admit"],
        help=(
            "report: observe and decide, never act (default). "
            "dry-run: same decisions, print the exact planned kill set. "
            "once: act on eligible trees. "
            "status: print the last recorded run. "
            "admit: decide from /proc/meminfo alone whether one more worker may "
            "launch; exit 0 admits, 75 refuses for headroom, 69 refuses because "
            "memory telemetry is unavailable. Append '-- command...' to start the "
            "command only when admitted. Never signals any process."
        ),
    )
    parser.add_argument(
        "--no-reserve",
        action="store_true",
        help="admit: answer without recording a launch reservation (a pure query)",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="configuration file path")
    parser.add_argument("--json", action="store_true", help="emit the machine-readable record")
    parser.add_argument(
        "--proc-source",
        help="read a synthetic inventory from this JSON file instead of /proc (testing only)",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def run_admit(
    args: argparse.Namespace,
    config: dict[str, object],
    command: Sequence[str],
    execvp: Callable[[str, Sequence[str]], None],
) -> int:
    """Launch admission. This path never inventories or signals a process."""
    if args.proc_source:
        if command:
            print("error: refusing to launch a command against synthetic facts", file=sys.stderr)
            return 2
        try:
            document = json.loads(Path(args.proc_source).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"error: proc_source_unreadable reason={type(exc).__name__}", file=sys.stderr)
            return 2
        facts = FixtureSource(document).read_memory_facts()
        # Synthetic facts must never touch the real reservation ledger.
        ledger = LaunchLedger(None, 0.0)
        reserve = False
    else:
        facts = ProcSource().read_memory_facts()
        ledger = LaunchLedger(
            LaunchLedger.default_directory(
                str(config["launch_ledger_dir"]), dict(os.environ), os.getuid()
            ),
            float(config["launch_reservation_seconds"]),
            writable=not args.no_reserve,
        )
        reserve = not args.no_reserve

    admission = admit_launch(facts, config, ledger, reserve)

    # With a command to start, stdout belongs to that command.
    reporter = Reporter(int(config["max_log_lines"]), stream=sys.stderr if command else None)
    emit_admission(reporter, admission, ledger=ledger.status)
    if args.json:
        print(json.dumps(admission.to_state(), indent=2, sort_keys=True))
    if not admission.admitted:
        for line in explain_refusal(admission):
            print(f"bibi-memory-guard: {line}", file=sys.stderr)
        return admission.exit_code

    if command:
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            execvp(command[0], list(command))
        except OSError as exc:
            print(
                f"error: launch_failed command={_scalar(command[0])} reason={exc.strerror}",
                file=sys.stderr,
            )
            return 127 if exc.errno == errno.ENOENT else 126
    return admission.exit_code


def main(
    argv: Sequence[str] | None = None,
    execvp: Callable[[str, Sequence[str]], None] = os.execvp,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Everything after the first `--` is the command to start once admitted.
    command: list[str] = []
    if "--" in arguments:
        split = arguments.index("--")
        arguments, command = arguments[:split], arguments[split + 1 :]

    parser = build_parser()
    args = parser.parse_args(arguments)
    if command and args.mode != "admit":
        parser.error("a command after '--' is only accepted by the admit mode")
    if args.mode == "admit" and "--" in (sys.argv[1:] if argv is None else argv) and not command:
        parser.error("admit: '--' must be followed by the command to launch")

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"error: config_invalid reason={exc}", file=sys.stderr)
        return 2

    if args.mode == "admit":
        return run_admit(args, config, command, execvp)

    if args.mode == "status":
        try:
            document = json.loads(Path(str(config["state_file"])).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print("status: no recorded run; run 'bibi-memory-guard report' first", file=sys.stderr)
            return 1
        print(json.dumps(document, indent=2, sort_keys=True))
        return 0

    if args.proc_source:
        if args.mode == "once":
            print("error: refusing to act against a synthetic inventory", file=sys.stderr)
            return 2
        try:
            document = json.loads(Path(args.proc_source).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"error: proc_source_unreadable reason={type(exc).__name__}", file=sys.stderr)
            return 2
        source = FixtureSource(document)
        signaller = RecordingSignaller(source)
    else:
        source = ProcSource()
        signaller = RealSignaller()

    try:
        owner_uid = resolve_owner_uid(config)
    except ConfigError as exc:
        print(f"error: config_invalid reason={exc}", file=sys.stderr)
        return 2

    lock = None
    if args.mode == "once":
        lock = acquire_lock(str(config["lock_file"]))
        if lock is False:
            print("skipped: another_run_in_progress", file=sys.stderr)
            return 0

    reporter = Reporter(int(config["max_log_lines"]))
    outcome = run(
        mode=args.mode,
        config=config,
        source=source,
        signaller=signaller,
        reporter=reporter,
        owner_uid=owner_uid,
        self_ancestry=frozenset() if args.proc_source else self_ancestry(os.getpid()),
    )

    document = outcome.to_state(time.time())
    if args.mode in {"once", "report"}:
        write_state(str(config["state_file"]), document)
    if args.json:
        print(json.dumps(document, indent=2, sort_keys=True))

    if lock:
        lock.close()
    return outcome.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
