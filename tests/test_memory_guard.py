#!/usr/bin/env python3
"""Deterministic tests for the ownership-proven browser-leak reaper.

Every test runs the real decision pipeline against a synthetic process
inventory and a recording signaller. No test reads a real process, sends a real
signal, or touches host memory.

The inventories are modelled on the trees observed during the incident that
motivated the guard: a `chrome-devtools-axi` bridge daemon reparented to PID 1,
an `npm exec chrome-devtools-mcp` launcher, a `sh -c` shim, the MCP server with
its process title overwritten, an MCP telemetry watchdog that lives in its own
process group, and a descendant headless Chromium tree.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = REPO_ROOT / "scripts" / "bibi_memory_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("bibi_memory_guard", GUARD_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["bibi_memory_guard"] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()

OWNER_UID = 1001
AXI_BRIDGE_SCRIPT = (
    "/home/bibi/.local/lib/node_modules/chrome-devtools-axi/dist/bin/chrome-devtools-axi-bridge.js"
)
WATCHDOG_SCRIPT = (
    "/home/bibi/.npm/_npx/15c61037b1978c83/node_modules/chrome-devtools-mcp"
    "/build/src/telemetry/watchdog/main.js"
)
CHROME_EXE = "/home/bibi/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"

OLD = 90_000.0  # comfortably past every configured grace threshold


def process(
    pid: int,
    ppid: int,
    *,
    comm: str,
    argv: list[str],
    exe: str | None = "/usr/bin/node",
    cwd: str | None = None,
    rss_kb: int = 80_000,
    age_seconds: float = OLD,
    uid: int = OWNER_UID,
    pgid: int | None = None,
    cwd_missing: bool = False,
) -> dict:
    return {
        "pid": pid,
        "ppid": ppid,
        "pgid": pid if pgid is None else pgid,
        "sid": pid,
        "uid": uid,
        "comm": comm,
        "identity": f"start-{pid}",
        "age_seconds": age_seconds,
        "rss_kb": rss_kb,
        "cwd": cwd,
        "cwd_missing": cwd_missing,
        "exe": exe,
        "argv": argv,
    }


def bridge_tree(
    base: int,
    worktree: str,
    *,
    age_seconds: float = OLD,
    watchdog_age_seconds: float | None = None,
    with_browser: bool = False,
) -> list[dict]:
    """One complete leak-family tree, shaped like the observed real ones.

    The watchdog deliberately gets its own process group: a process-group kill
    would miss it, which is exactly why the guard walks parent links instead.
    """
    watchdog_age = age_seconds if watchdog_age_seconds is None else watchdog_age_seconds
    tree = [
        process(
            base,
            1,
            comm="MainThread",
            argv=["node", AXI_BRIDGE_SCRIPT],
            cwd=worktree,
            rss_kb=78_000,
            age_seconds=age_seconds,
        ),
        process(
            base + 1,
            base,
            comm="npm exec chrome",
            # npm collapses its whole command line into argv[0].
            argv=["npm exec chrome-devtools-mcp@latest --isolated --headless"],
            cwd=worktree,
            rss_kb=95_000,
            age_seconds=age_seconds,
            pgid=base,
        ),
        process(
            base + 2,
            base + 1,
            comm="sh",
            argv=["sh", "-c", '"chrome-devtools-mcp" --isolated --headless'],
            exe="/usr/bin/dash",
            cwd=worktree,
            rss_kb=1_800,
            age_seconds=age_seconds,
            pgid=base,
        ),
        process(
            base + 3,
            base + 2,
            comm="chrome-devtools",
            # chrome-devtools-mcp also overwrites its process title.
            argv=["chrome-devtools-mcp"],
            cwd=worktree,
            rss_kb=145_000,
            age_seconds=age_seconds,
            pgid=base,
        ),
        process(
            base + 4,
            base + 3,
            comm="MainThread",
            argv=["/usr/bin/node", WATCHDOG_SCRIPT, f"--parent-pid={base + 3}"],
            cwd=worktree,
            rss_kb=76_000,
            age_seconds=watchdog_age,
            pgid=base + 4,
        ),
    ]
    if with_browser:
        tree.append(
            process(
                base + 5,
                base + 3,
                comm="chrome",
                argv=[CHROME_EXE, "--headless"],
                exe=CHROME_EXE,
                cwd=worktree,
                rss_kb=210_000,
                age_seconds=age_seconds,
                pgid=base,
            )
        )
    return tree


HEALTHY_MEMINFO = {
    "MemTotal": 8_131_784,
    "MemAvailable": 3_965_448,
    "SwapTotal": 2_097_148,
    "SwapFree": 2_097_148,
}

STARVED_MEMINFO = {
    "MemTotal": 8_131_784,
    "MemAvailable": 500_000,
    "SwapTotal": 2_097_148,
    "SwapFree": 1_800_000,
}


class GuardHarness:
    """Builds the on-disk ownership oracles and runs the guard once."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.worktree_root = tmp / "treehouse"
        self.axi_state = tmp / "axi"
        self.firstmate_state = tmp / "fm-state"
        for directory in (self.worktree_root, self.axi_state, self.firstmate_state):
            directory.mkdir(parents=True, exist_ok=True)
        (self.axi_state / "sessions").mkdir(exist_ok=True)
        self.clock = 1_000_000.0

    def advance(self, seconds: float) -> None:
        """Stand in for sleeping, so bounded waits terminate deterministically."""
        self.clock += seconds

    # -- oracle construction -------------------------------------------------

    def worktree(self, project: str, slot: int = 1, create: bool = True) -> str:
        path = self.worktree_root / f"{project}-abc123" / str(slot) / project
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def bind_session(self, name: str, pid: int, port: int) -> None:
        directory = self.axi_state / "sessions" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "bridge.pid").write_text(json.dumps({"pid": pid, "port": port}))

    def task(
        self,
        name: str,
        worktree: str,
        *,
        status: str | None = "done: shipped",
        busy: bool = False,
        busy_age_seconds: float = 0.0,
        status_age_seconds: float = 7_200.0,
    ) -> None:
        (self.firstmate_state / f"{name}.meta").write_text(
            f"endpoint_task_id={name}\nworktree={worktree}\nharness=claude\n"
        )
        if status is not None:
            status_file = self.firstmate_state / f"{name}.status"
            status_file.write_text(f"working: started\n{status}\n")
            mtime = self.clock - status_age_seconds
            os.utime(status_file, (mtime, mtime))
        if busy:
            stamp = int(self.clock - busy_age_seconds)
            (self.firstmate_state / f"{name}.busy-state").write_text(
                "v1 gen=g1 seq=2 state=busy source=claude-hook "
                f"event=user-prompt-submit ts={stamp}"
            )

    # -- execution -----------------------------------------------------------

    def config(self, **overrides) -> dict:
        config = guard.load_config(None)
        config.update(
            {
                "axi_state_dir": str(self.axi_state),
                "firstmate_state_dir": str(self.firstmate_state),
                "worktree_root": str(self.worktree_root),
            }
        )
        config.update(overrides)
        return config

    def run(
        self,
        processes: list[dict],
        *,
        mode: str = "report",
        meminfo: dict | None = None,
        tcp: list[dict] | None = None,
        term_survivors: list[int] | None = None,
        **config_overrides,
    ):
        document = {
            "processes": processes,
            "meminfo": dict(meminfo or HEALTHY_MEMINFO),
            "tcp": list(tcp or []),
        }
        source = guard.FixtureSource(document)
        signaller = guard.RecordingSignaller(source, term_survivors or ())
        config = self.config(**config_overrides)
        reporter = guard.Reporter(int(config["max_log_lines"]), stream=StringIO())
        outcome = guard.run(
            mode=mode,
            config=config,
            source=source,
            signaller=signaller,
            reporter=reporter,
            owner_uid=OWNER_UID,
            self_ancestry=frozenset({999_999}),
            now=lambda: self.clock,
            sleep=self.advance,
        )
        return outcome, signaller, reporter, source


def decisions(outcome) -> dict[int, tuple[str, str]]:
    return {
        candidate.root.pid: (candidate.decision, candidate.reason)
        for candidate in outcome.candidates
    }


class GuardTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.harness = GuardHarness(Path(self._tmp.name))


class ClassificationTests(GuardTestCase):
    def test_recognises_every_member_of_the_leak_family(self) -> None:
        worktree = self.harness.worktree("money-monk")
        outcome, _, _, source = self.harness.run(
            bridge_tree(1000, worktree, with_browser=True)
        )
        roles = {
            member.pid: member.role
            for member in source.read().processes
        }
        self.assertEqual(roles[1000], guard.ROLE_BRIDGE)
        self.assertEqual(roles[1001], guard.ROLE_MCP_LAUNCHER)
        self.assertEqual(roles[1002], guard.ROLE_MCP_SHIM)
        self.assertEqual(roles[1003], guard.ROLE_MCP_SERVER)
        self.assertEqual(roles[1004], guard.ROLE_MCP_WATCHDOG)
        self.assertEqual(roles[1005], guard.ROLE_BROWSER)
        self.assertEqual(len(outcome.candidates), 1, "the bridge is the single root")

    def test_watchdog_records_only_its_integer_parent_pid(self) -> None:
        worktree = self.harness.worktree("money-monk")
        _, _, _, source = self.harness.run(bridge_tree(1000, worktree))
        watchdog = next(p for p in source.read().processes if p.pid == 1004)
        self.assertEqual(watchdog.watchdog_parent_pid, 1003)

    def test_a_users_own_browser_is_not_leak_family(self) -> None:
        outcome, _, _, source = self.harness.run(
            [
                process(
                    2000,
                    1,
                    comm="chrome",
                    argv=["/usr/bin/chromium", "--headless"],
                    exe="/usr/bin/chromium",
                    rss_kb=900_000,
                )
            ]
        )
        roles = {member.pid: member.role for member in source.read().processes}
        self.assertEqual(roles[2000], guard.ROLE_OTHER)
        self.assertEqual(outcome.candidates, [])


class ReparentingTests(GuardTestCase):
    def test_tree_is_scoped_by_parent_links_not_process_group(self) -> None:
        """The watchdog lives in its own process group.

        A `kill -TERM -PGID` would silently leave it running. The tree must be
        assembled from parent links so the watchdog is included.
        """
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-done-a1", worktree, status="done: shipped")
        outcome, _, _, _ = self.harness.run(bridge_tree(1000, worktree))

        candidate = outcome.candidates[0]
        member_pids = {member.pid for member in candidate.members}
        self.assertEqual(member_pids, {1000, 1001, 1002, 1003, 1004})
        watchdog = next(m for m in candidate.members if m.pid == 1004)
        self.assertNotEqual(watchdog.pgid, candidate.root.pgid)

    def test_a_root_still_attached_to_a_live_parent_is_refused(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-done-a1", worktree, status="done: shipped")
        tree = bridge_tree(1000, worktree)
        tree[0]["ppid"] = 4242  # still owned by a live launcher
        tree.append(
            process(4242, 1, comm="claude", argv=["claude"], exe="/usr/bin/claude", cwd=worktree)
        )
        outcome, signaller, _, _ = self.harness.run(tree, mode="once")
        self.assertEqual(decisions(outcome)[1000][0], "refused")
        self.assertEqual(signaller.sent, [])


class OwnershipTests(GuardTestCase):
    def test_active_browser_session_is_never_selected(self) -> None:
        """The exact live case from the incident.

        This tree is reparented, 25 hours old, holds ~400 MB, and its port is
        listening with no client attached - identical on every signal to the
        leaked tree below. Its owning task is still running, so it is protected.
        """
        worktree = self.harness.worktree("artist-archiver", slot=3)
        self.harness.bind_session("ara8", 1000, 9884)
        self.harness.task(
            "linear-ara-8-a1", worktree, status="working: implementing", busy=True
        )
        processes = bridge_tree(1000, worktree)
        processes.append(
            process(
                5000,
                4999,
                comm="claude",
                argv=["claude", "--effort", "xhigh"],
                exe="/usr/bin/claude",
                cwd=worktree,
                rss_kb=450_000,
            )
        )
        outcome, signaller, _, _ = self.harness.run(
            processes, mode="once", tcp=[{"port": 9884, "state": "LISTEN"}]
        )
        self.assertEqual(decisions(outcome)[1000], ("refused", "owner-process-alive"))
        self.assertEqual(signaller.sent, [], "no signal may be sent for a live session")

    def test_completed_task_orphan_is_eligible(self) -> None:
        worktree = self.harness.worktree("money-monk", slot=2)
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task(
            "money-monk-m1-a1", worktree, status="done: PR merged", status_age_seconds=7_200
        )
        outcome, _, _, _ = self.harness.run(
            bridge_tree(1000, worktree), tcp=[{"port": 10069, "state": "LISTEN"}]
        )
        self.assertEqual(decisions(outcome)[1000], ("eligible", "owning-task-completed"))

    def test_a_connected_client_protects_a_completed_task_tree(self) -> None:
        """Ownership records can lag reality; a live socket always wins."""
        worktree = self.harness.worktree("money-monk", slot=2)
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-m1-a1", worktree, status="done: PR merged")
        outcome, signaller, _, _ = self.harness.run(
            bridge_tree(1000, worktree),
            mode="once",
            tcp=[{"port": 10069, "state": "LISTEN"}, {"port": 10069, "state": "CONNECTED"}],
        )
        self.assertEqual(decisions(outcome)[1000], ("refused", "active-client-connection"))
        self.assertEqual(signaller.sent, [])

    def test_deleted_worktree_is_positive_orphan_evidence(self) -> None:
        worktree = self.harness.worktree("gone-project", create=False)
        self.harness.bind_session("gone-a1", 1000, 9500)
        outcome, _, _, _ = self.harness.run(bridge_tree(1000, worktree))
        self.assertEqual(decisions(outcome)[1000], ("eligible", "owning-worktree-deleted"))

    def test_non_terminal_task_states_all_protect(self) -> None:
        for status in (
            "working: still going",
            "blocked: needs help",
            "paused: waiting on upstream",
            "needs-decision: two options",
        ):
            with self.subTest(status=status):
                harness = GuardHarness(Path(self._tmp.name) / status.split(":")[0])
                worktree = harness.worktree("money-monk")
                harness.bind_session("mm-a1", 1000, 10069)
                harness.task("money-monk-a1", worktree, status=status)
                outcome, signaller, _, _ = harness.run(bridge_tree(1000, worktree), mode="once")
                self.assertEqual(
                    decisions(outcome)[1000], ("refused", "owning-task-not-terminal")
                )
                self.assertEqual(signaller.sent, [])

    def test_a_reused_pool_slot_is_judged_by_its_current_owner(self) -> None:
        """Treehouse pool slots are recycled, so several tasks name one worktree.

        A finished predecessor must never be mistaken for the worktree's owner
        while its successor is still running there.
        """
        worktree = self.harness.worktree("artist-archiver", slot=3)
        self.harness.bind_session("ara8", 1000, 9884)
        self.harness.task("previous-task-a1", worktree, status="done: shipped")
        self.harness.task("current-task-a1", worktree, status="working: implementing")

        outcome, signaller, _, _ = self.harness.run(bridge_tree(1000, worktree), mode="once")
        candidate = outcome.candidates[0]
        self.assertEqual(candidate.owner_task.task, "current-task-a1")
        self.assertEqual(
            (candidate.decision, candidate.reason), ("refused", "owning-task-not-terminal")
        )
        self.assertEqual(signaller.sent, [])

    def test_a_reused_slot_whose_current_owner_is_busy_is_protected(self) -> None:
        worktree = self.harness.worktree("artist-archiver", slot=3)
        self.harness.bind_session("ara8", 1000, 9884)
        self.harness.task("previous-task-a1", worktree, status="done: shipped")
        self.harness.task(
            "current-task-a1", worktree, status="done: also shipped", busy=True
        )
        outcome, signaller, _, _ = self.harness.run(bridge_tree(1000, worktree), mode="once")
        self.assertEqual(decisions(outcome)[1000], ("refused", "owning-task-busy"))
        self.assertEqual(signaller.sent, [])

    def test_a_reused_slot_is_cleanable_only_when_every_claimant_finished(self) -> None:
        worktree = self.harness.worktree("artist-archiver", slot=3)
        self.harness.bind_session("ara8", 1000, 9884)
        self.harness.task("previous-task-a1", worktree, status="done: shipped")
        self.harness.task("current-task-a1", worktree, status="failed: gave up")
        outcome, _, _, _ = self.harness.run(bridge_tree(1000, worktree))
        self.assertEqual(decisions(outcome)[1000], ("eligible", "owning-task-completed"))

    def test_busy_flag_overrides_a_terminal_status(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-a1", worktree, status="done: shipped", busy=True)
        outcome, signaller, _, _ = self.harness.run(bridge_tree(1000, worktree), mode="once")
        self.assertEqual(decisions(outcome)[1000], ("refused", "owning-task-busy"))
        self.assertEqual(signaller.sent, [])

    def test_a_stale_busy_flag_stops_protecting(self) -> None:
        """An agent that died mid-turn leaves its busy flag set forever."""
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task(
            "money-monk-a1",
            worktree,
            status="done: shipped",
            busy=True,
            busy_age_seconds=7_200,
        )
        outcome, _, _, _ = self.harness.run(bridge_tree(1000, worktree))
        self.assertEqual(decisions(outcome)[1000], ("eligible", "owning-task-completed"))

    def test_a_stale_busy_flag_alone_does_not_authorise_anything(self) -> None:
        """Staleness only removes a protection; every other proof still applies."""
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task(
            "money-monk-a1",
            worktree,
            status="working: still going",
            busy=True,
            busy_age_seconds=7_200,
        )
        outcome, signaller, _, _ = self.harness.run(bridge_tree(1000, worktree), mode="once")
        self.assertEqual(decisions(outcome)[1000], ("refused", "owning-task-not-terminal"))
        self.assertEqual(signaller.sent, [])

    def test_a_task_that_just_finished_is_left_alone(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task(
            "money-monk-a1", worktree, status="done: shipped", status_age_seconds=60
        )
        outcome, _, _, _ = self.harness.run(bridge_tree(1000, worktree))
        self.assertEqual(
            decisions(outcome)[1000], ("refused", "owning-task-recently-finished")
        )


class AmbiguityTests(GuardTestCase):
    def test_unclaimed_worktree_refuses_rather_than_guessing(self) -> None:
        worktree = self.harness.worktree("orphan-project")
        self.harness.bind_session("orphan-a1", 1000, 9700)
        outcome, signaller, _, _ = self.harness.run(bridge_tree(1000, worktree), mode="once")
        self.assertEqual(decisions(outcome)[1000], ("refused", "unclaimed-worktree"))
        self.assertEqual(signaller.sent, [])

    def test_unregistered_bridge_refuses_rather_than_guessing(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.task("money-monk-a1", worktree, status="done: shipped")
        outcome, signaller, _, _ = self.harness.run(bridge_tree(1000, worktree), mode="once")
        self.assertEqual(decisions(outcome)[1000], ("refused", "unregistered-bridge"))
        self.assertEqual(signaller.sent, [])

    def test_unreadable_owning_status_refuses(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-a1", worktree, status=None)
        outcome, _, _, _ = self.harness.run(bridge_tree(1000, worktree))
        self.assertEqual(
            decisions(outcome)[1000], ("refused", "owning-task-status-unreadable")
        )

    def test_cwd_outside_the_worktree_pool_refuses(self) -> None:
        self.harness.bind_session("stray-a1", 1000, 9700)
        outcome, _, _, _ = self.harness.run(bridge_tree(1000, "/home/bibi/scratch"))
        self.assertEqual(decisions(outcome)[1000], ("refused", "unresolved-worktree"))

    def test_a_foreign_uid_member_refuses_the_whole_tree(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-a1", worktree, status="done: shipped")
        tree = bridge_tree(1000, worktree)
        tree[3]["uid"] = 0
        outcome, signaller, _, _ = self.harness.run(tree, mode="once")
        self.assertEqual(decisions(outcome)[1000], ("refused", "foreign-uid-member"))
        self.assertEqual(signaller.sent, [])

    def test_an_oversized_tree_refuses(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-a1", worktree, status="done: shipped")
        tree = bridge_tree(1000, worktree)
        for index in range(80):
            tree.append(
                process(
                    6000 + index,
                    1003,
                    comm="chrome",
                    argv=[CHROME_EXE, "--type=renderer"],
                    exe=CHROME_EXE,
                    cwd=worktree,
                    rss_kb=40_000,
                )
            )
        outcome, signaller, _, _ = self.harness.run(tree, mode="once")
        self.assertEqual(decisions(outcome)[1000], ("refused", "tree-too-large"))
        self.assertEqual(signaller.sent, [])

    def test_a_stranded_browser_is_reported_not_killed(self) -> None:
        outcome, signaller, _, _ = self.harness.run(
            [
                process(
                    7000,
                    1,
                    comm="chrome",
                    argv=[CHROME_EXE, "--headless"],
                    exe=CHROME_EXE,
                    cwd=self.harness.worktree("money-monk"),
                    rss_kb=800_000,
                )
            ],
            mode="once",
        )
        self.assertEqual(outcome.candidates, [], "a browser is never a cleanup root")
        self.assertIn("stranded-browser-processes", outcome.alerts)
        self.assertEqual(signaller.sent, [])

    def test_a_live_sessions_reparented_browser_raises_no_alert(self) -> None:
        """Chromium double-forks, so PID 1 is the normal parent for a healthy browser.

        Alerting on that shape alone would fire on every browser session and
        train operators to ignore the signal.
        """
        worktree = self.harness.worktree("artist-archiver", slot=3)
        self.harness.bind_session("ara8", 2000, 9884)
        self.harness.task("linear-ara-8-a1", worktree, status="working: implementing")
        processes = bridge_tree(2000, worktree)
        processes.append(
            process(
                7000,
                1,
                comm="chrome",
                argv=[CHROME_EXE, "--headless"],
                exe=CHROME_EXE,
                cwd=worktree,
                rss_kb=216_000,
                age_seconds=240.0,
            )
        )
        outcome, signaller, _, _ = self.harness.run(processes, mode="once")
        self.assertNotIn("stranded-browser-processes", outcome.alerts)
        self.assertEqual(signaller.sent, [])

    def test_a_young_reparented_browser_raises_no_alert(self) -> None:
        outcome, _, _, _ = self.harness.run(
            [
                process(
                    7000,
                    1,
                    comm="chrome",
                    argv=[CHROME_EXE, "--headless"],
                    exe=CHROME_EXE,
                    cwd=self.harness.worktree("money-monk"),
                    rss_kb=800_000,
                    age_seconds=240.0,
                )
            ]
        )
        self.assertNotIn("stranded-browser-processes", outcome.alerts)

    def test_the_guards_own_ancestry_is_never_signalled(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 999_999, 10069)
        self.harness.task("money-monk-a1", worktree, status="done: shipped")
        tree = bridge_tree(999_999, worktree)
        outcome, signaller, _, _ = self.harness.run(tree, mode="once")
        self.assertEqual(decisions(outcome)[999_999], ("refused", "self-or-ancestor-in-tree"))
        self.assertEqual(signaller.sent, [])


class AgeAndSizeTests(GuardTestCase):
    def test_a_young_root_is_refused(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-a1", worktree, status="done: shipped")
        outcome, signaller, _, _ = self.harness.run(
            bridge_tree(1000, worktree, age_seconds=120.0), mode="once"
        )
        self.assertEqual(decisions(outcome)[1000], ("refused", "root-below-age-grace"))
        self.assertEqual(signaller.sent, [])

    def test_a_young_member_of_an_old_tree_is_refused(self) -> None:
        """A fresh child means the tree is still doing something."""
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-a1", worktree, status="done: shipped")
        outcome, signaller, _, _ = self.harness.run(
            bridge_tree(1000, worktree, watchdog_age_seconds=30.0), mode="once"
        )
        self.assertEqual(decisions(outcome)[1000], ("refused", "member-below-age-grace"))
        self.assertEqual(signaller.sent, [])

    def test_a_small_tree_is_below_the_rss_floor(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-a1", worktree, status="done: shipped")
        tree = bridge_tree(1000, worktree)
        for member in tree:
            member["rss_kb"] = 1_000
        outcome, _, _, _ = self.harness.run(tree)
        self.assertEqual(decisions(outcome)[1000], ("refused", "below-rss-floor"))


class CleanupTests(GuardTestCase):
    def _eligible_tree(self, base: int = 1000, project: str = "money-monk") -> str:
        worktree = self.harness.worktree(project)
        self.harness.bind_session(f"{project}-a1", base, 10069)
        self.harness.task(f"{project}-task-a1", worktree, status="done: shipped")
        return worktree

    def test_term_alone_clears_a_cooperative_tree(self) -> None:
        worktree = self._eligible_tree()
        outcome, signaller, _, source = self.harness.run(
            bridge_tree(1000, worktree), mode="once"
        )
        cleanup = outcome.cleanups[0]
        self.assertEqual(cleanup.outcome, "verified-absent")
        self.assertEqual(cleanup.killed, ())
        self.assertEqual({sig for _pid, sig in signaller.sent}, {15})
        self.assertEqual({pid for pid, _sig in signaller.sent}, {1000, 1001, 1002, 1003, 1004})
        self.assertEqual(source.read().processes, ())

    def test_term_is_delivered_leaves_first(self) -> None:
        worktree = self._eligible_tree()
        _, signaller, _, _ = self.harness.run(bridge_tree(1000, worktree), mode="once")
        order = [pid for pid, _sig in signaller.sent]
        self.assertLess(order.index(1004), order.index(1003), "watchdog before its parent")
        self.assertEqual(order[-1], 1000, "the root is signalled last")

    def test_kill_is_used_only_for_members_that_survive_term(self) -> None:
        worktree = self._eligible_tree()
        outcome, signaller, _, _ = self.harness.run(
            bridge_tree(1000, worktree), mode="once", term_survivors=[1003, 1004]
        )
        cleanup = outcome.cleanups[0]
        self.assertEqual(cleanup.outcome, "verified-absent")
        self.assertEqual(set(cleanup.killed), {1003, 1004})
        killed = {pid for pid, sig in signaller.sent if sig == 9}
        self.assertEqual(killed, {1003, 1004}, "KILL touches only TERM survivors")

    def test_a_pid_recycled_before_the_signal_is_skipped(self) -> None:
        worktree = self._eligible_tree()
        processes = bridge_tree(1000, worktree)
        source_document = {
            "processes": processes,
            "meminfo": dict(HEALTHY_MEMINFO),
            "tcp": [],
        }
        source = guard.FixtureSource(source_document)
        config = self.harness.config()
        inventory = source.read()
        candidate = guard.find_candidates(
            inventory,
            config,
            guard.read_session_bindings(str(self.harness.axi_state)),
            guard.read_worktree_owners(
                str(self.harness.firstmate_state), self.harness.clock, 3600.0
            ),
            OWNER_UID,
        )[0]
        # The watchdog's PID is recycled between inventory and signal time.
        for record in source_document["processes"]:
            if record["pid"] == 1004:
                record["identity"] = "start-recycled"
        signaller = guard.RecordingSignaller(source)
        result = guard.clean_tree(
            candidate, source, signaller, config, lambda: self.harness.clock, self.harness.advance
        )
        self.assertIn(1004, result.skipped_identity)
        self.assertNotIn(1004, [pid for pid, _sig in signaller.sent])

    def test_repeated_runs_are_idempotent(self) -> None:
        worktree = self._eligible_tree()
        processes = bridge_tree(1000, worktree)
        first, first_signaller, _, _ = self.harness.run(processes, mode="once")
        self.assertEqual(len(first.cleanups), 1)
        self.assertGreater(len(first_signaller.sent), 0)

        remaining = [record for record in processes if record["pid"] not in range(1000, 1005)]
        second, second_signaller, _, _ = self.harness.run(remaining, mode="once")
        self.assertEqual(second.cleanups, [])
        self.assertEqual(second.candidates, [])
        self.assertEqual(second_signaller.sent, [], "a second run is a pure no-op")

    def test_dry_run_decides_identically_but_never_signals(self) -> None:
        worktree = self._eligible_tree()
        live, live_signaller, _, _ = self.harness.run(bridge_tree(1000, worktree), mode="once")
        dry, dry_signaller, reporter, _ = self.harness.run(
            bridge_tree(1000, worktree), mode="dry-run"
        )
        self.assertEqual(decisions(live), decisions(dry))
        self.assertGreater(len(live_signaller.sent), 0)
        self.assertEqual(dry_signaller.sent, [])
        self.assertTrue(any(line.startswith("planned:") for line in reporter.lines))

    def test_work_per_run_is_capped(self) -> None:
        first = self.harness.worktree("alpha")
        second = self.harness.worktree("beta")
        third = self.harness.worktree("gamma")
        for index, (name, worktree, base) in enumerate(
            (("alpha", first, 1000), ("beta", second, 2000), ("gamma", third, 3000))
        ):
            self.harness.bind_session(f"{name}-a1", base, 9500 + index)
            self.harness.task(f"{name}-task-a1", worktree, status="done: shipped")
        processes = (
            bridge_tree(1000, first) + bridge_tree(2000, second) + bridge_tree(3000, third)
        )
        outcome, signaller, _, _ = self.harness.run(processes, mode="once")
        self.assertEqual(len(outcome.cleanups), 2, "leak_max_trees_per_run defaults to 2")
        deferred = [c for c in outcome.candidates if c.decision == "deferred"]
        self.assertEqual(len(deferred), 1)
        self.assertEqual(deferred[0].reason, "run-cap-reached")
        touched = {pid for pid, _sig in signaller.sent}
        self.assertNotIn(deferred[0].root.pid, touched)


class MixedOwnershipTests(GuardTestCase):
    def test_only_the_unowned_tree_of_a_mixed_pair_is_touched(self) -> None:
        """The counterfactual that motivated the whole design.

        Both trees are reparented, both are ~20 hours old, both hold ~400 MB,
        and both are listening with no client. The only difference is that one
        worktree still has a live owner. Exactly one tree may be signalled.
        """
        live_worktree = self.harness.worktree("artist-archiver", slot=3)
        dead_worktree = self.harness.worktree("money-monk", slot=2)
        self.harness.bind_session("ara8", 2000, 9884)
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("linear-ara-8-a1", live_worktree, status="working: implementing")
        self.harness.task("money-monk-m1-a1", dead_worktree, status="done: PR merged")

        processes = bridge_tree(1000, dead_worktree) + bridge_tree(2000, live_worktree)
        processes.append(
            process(
                5000,
                4999,
                comm="claude",
                argv=["claude"],
                exe="/usr/bin/claude",
                cwd=live_worktree,
                rss_kb=450_000,
            )
        )
        outcome, signaller, _, _ = self.harness.run(
            processes,
            mode="once",
            tcp=[{"port": 9884, "state": "LISTEN"}, {"port": 10069, "state": "LISTEN"}],
        )
        verdicts = decisions(outcome)
        self.assertEqual(verdicts[1000], ("eligible", "owning-task-completed"))
        self.assertEqual(verdicts[2000], ("refused", "owner-process-alive"))

        touched = {pid for pid, _sig in signaller.sent}
        self.assertEqual(touched, {1000, 1001, 1002, 1003, 1004})
        self.assertTrue(
            touched.isdisjoint({2000, 2001, 2002, 2003, 2004, 5000}),
            "no member of the live session or its agent may be signalled",
        )


class MemoryPolicyTests(GuardTestCase):
    def test_low_memory_with_no_eligible_tree_alerts_and_kills_nothing(self) -> None:
        live_worktree = self.harness.worktree("artist-archiver", slot=3)
        self.harness.bind_session("ara8", 2000, 9884)
        self.harness.task("linear-ara-8-a1", live_worktree, status="working: implementing")
        processes = bridge_tree(2000, live_worktree)
        processes.append(
            process(
                5000,
                4999,
                comm="claude",
                argv=["claude"],
                exe="/usr/bin/claude",
                cwd=live_worktree,
            )
        )
        outcome, signaller, reporter, _ = self.harness.run(
            processes, mode="once", meminfo=STARVED_MEMINFO
        )
        self.assertEqual(outcome.level, "critical")
        self.assertIn("pressure-without-eligible-leak", outcome.alerts)
        self.assertEqual(outcome.cleanups, [])
        self.assertEqual(signaller.sent, [], "low memory never authorises a kill")
        self.assertTrue(
            any("low-memory-never-authorises-a-kill" in line for line in reporter.lines)
        )

    def test_an_eligible_tree_is_cleaned_before_any_pressure(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-a1", worktree, status="done: shipped")
        outcome, signaller, _, _ = self.harness.run(
            bridge_tree(1000, worktree), mode="once", meminfo=HEALTHY_MEMINFO
        )
        self.assertEqual(outcome.level, "ok")
        self.assertEqual(len(outcome.cleanups), 1)
        self.assertGreater(len(signaller.sent), 0)

    def test_memory_levels_are_purely_observational(self) -> None:
        config = self.harness.config()
        healthy = guard.read_memory(HEALTHY_MEMINFO)
        starved = guard.read_memory(STARVED_MEMINFO)
        self.assertEqual(guard.memory_level(healthy, config)[0], "ok")
        self.assertEqual(guard.memory_level(starved, config)[0], "critical")

    def test_swap_pressure_is_reported_so_swap_cannot_hide_a_leak(self) -> None:
        config = self.harness.config()
        swapping = guard.read_memory(
            {
                "MemTotal": 8_131_784,
                "MemAvailable": 4_000_000,
                "SwapTotal": 2_097_148,
                "SwapFree": 1_400_000,
            }
        )
        level, reasons = guard.memory_level(swapping, config)
        self.assertEqual(level, "warn")
        self.assertIn("swap-used-warn", reasons)


class ReportingTests(GuardTestCase):
    SECRET = "dop_v1_examplesecrettoken"

    def test_no_command_line_content_reaches_the_report_or_the_state_file(self) -> None:
        worktree = self.harness.worktree("money-monk")
        self.harness.bind_session("mm-a1", 1000, 10069)
        self.harness.task("money-monk-a1", worktree, status="done: shipped")
        processes = bridge_tree(1000, worktree)
        processes[0]["argv"] = ["node", AXI_BRIDGE_SCRIPT, f"--token={self.SECRET}"]
        outcome, _, reporter, _ = self.harness.run(processes)
        rendered = "\n".join(reporter.lines) + json.dumps(outcome.to_state(0.0))
        self.assertNotIn(self.SECRET, rendered)
        self.assertNotIn("--token", rendered)

    def test_classification_drops_argv_from_the_process_record(self) -> None:
        _, _, _, source = self.harness.run(
            bridge_tree(1000, self.harness.worktree("money-monk"))
        )
        record = source.read().processes[0]
        self.assertFalse(hasattr(record, "argv"))

    def test_report_output_is_bounded(self) -> None:
        processes: list[dict] = []
        for index in range(30):
            processes.extend(
                bridge_tree(1000 + index * 10, self.harness.worktree(f"project{index}"))
            )
        _, _, reporter, _ = self.harness.run(processes, max_log_lines=12)
        self.assertLessEqual(len(reporter.lines), 13)  # 12 emitted plus the truncation note
        self.assertTrue(reporter.lines[-1].startswith("note: output_truncated_lines="))

    def test_state_document_records_every_refusal_reason(self) -> None:
        live_worktree = self.harness.worktree("artist-archiver", slot=3)
        self.harness.bind_session("ara8", 2000, 9884)
        self.harness.task("linear-ara-8-a1", live_worktree, status="working: implementing")
        outcome, _, _, _ = self.harness.run(bridge_tree(2000, live_worktree))
        document = outcome.to_state(1234.0)
        self.assertEqual(document["trees"][0]["decision"], "refused")
        self.assertEqual(document["trees"][0]["reason"], "owning-task-not-terminal")
        self.assertEqual(document["trees"][0]["session"], "ara8")


class SafetyPropertyTests(unittest.TestCase):
    """Properties of the guard source itself."""

    SOURCE = GUARD_PATH.read_text(encoding="utf-8")

    def test_source_contains_no_broad_or_pattern_killing(self) -> None:
        for forbidden in ("pkill", "killall", "killpg", "pgrep", "subprocess"):
            self.assertNotIn(forbidden, self.SOURCE, f"{forbidden} must never appear")

    def test_the_only_signal_call_is_a_single_pid_os_kill(self) -> None:
        self.assertEqual(self.SOURCE.count("os.kill("), 1)

    def test_real_signaller_refuses_pid_zero_one_and_negatives(self) -> None:
        signaller = guard.RealSignaller()
        for pid in (-1000, -1, 0, 1):
            self.assertEqual(signaller.send(pid, 15), "refused")

    def test_owner_command_matching_can_only_protect(self) -> None:
        """Name matching appears only in the protective owner-liveness check."""
        self.assertIn("owner_commands", self.SOURCE)
        self.assertIn("Protective only", self.SOURCE)
        self.assertNotIn("owner_commands", self.SOURCE.split("def decide(")[1].split("def clean_tree(")[0])

    def test_synthetic_inventories_cannot_drive_a_real_run(self) -> None:
        self.assertIn("refusing to act against a synthetic inventory", self.SOURCE)


class SelfAncestryTests(unittest.TestCase):
    def test_ancestry_walks_the_parent_chain_of_this_process(self) -> None:
        ancestry = guard.self_ancestry(os.getpid())
        self.assertIn(os.getpid(), ancestry)
        self.assertIn(os.getppid(), ancestry)
        self.assertNotIn(0, ancestry)

    def test_a_missing_process_yields_only_itself(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(guard.self_ancestry(4242, proc_root=tmp), frozenset({4242}))


class WorktreeResolutionTests(unittest.TestCase):
    ROOT = "/home/bibi/.treehouse"

    def test_pool_worktree_paths_resolve_to_their_root(self) -> None:
        self.assertEqual(
            guard.resolve_worktree(f"{self.ROOT}/money-monk-431ad6/2/money-monk/src", self.ROOT),
            f"{self.ROOT}/money-monk-431ad6/2/money-monk",
        )

    def test_paths_outside_the_pool_resolve_to_nothing(self) -> None:
        for path in ("/home/bibi/firstmate", "/tmp", None, self.ROOT, f"{self.ROOT}/only/two"):
            self.assertIsNone(guard.resolve_worktree(path, self.ROOT))

    def test_traversal_cannot_escape_the_pool_root(self) -> None:
        self.assertIsNone(guard.resolve_worktree(f"{self.ROOT}/../../etc/passwd", self.ROOT))


class ConfigTests(unittest.TestCase):
    def test_every_configured_key_is_actually_consulted(self) -> None:
        """A key nobody reads is a policy the operator thinks they set."""
        source = GUARD_PATH.read_text(encoding="utf-8")
        consulted = {"owner_user"}  # read through resolve_owner_uid, not config[...]
        for key in guard.DEFAULTS:
            with self.subTest(key=key):
                self.assertTrue(
                    key in consulted or f'config["{key}"]' in source,
                    f"{key} is configurable but never read",
                )

    def test_defaults_load_without_a_file(self) -> None:
        config = guard.load_config(None)
        self.assertEqual(config["leak_min_age_seconds"], 3600)
        self.assertEqual(config["owner_user"], "bibi")

    def test_unknown_keys_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "guard.conf"
            path.write_text("bogus_key=1\n")
            with self.assertRaises(guard.ConfigError):
                guard.load_config(str(path))

    def test_non_numeric_thresholds_are_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "guard.conf"
            path.write_text("leak_min_age_seconds=soon\n")
            with self.assertRaises(guard.ConfigError):
                guard.load_config(str(path))


class SessionBindingTests(unittest.TestCase):
    def test_malformed_and_unsafe_bindings_are_ignored(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            sessions = base / "sessions"
            (sessions / "good").mkdir(parents=True)
            (sessions / "good" / "bridge.pid").write_text('{"pid": 4242, "port": 9884}')
            (sessions / "broken").mkdir(parents=True)
            (sessions / "broken" / "bridge.pid").write_text("not json")
            (sessions / "init-claim").mkdir(parents=True)
            (sessions / "init-claim" / "bridge.pid").write_text('{"pid": 1, "port": 9884}')

            bindings = guard.read_session_bindings(str(base))
            self.assertEqual(bindings[4242].name, "good")
            self.assertEqual(bindings[4242].port, 9884)
            self.assertNotIn(1, bindings)
            self.assertEqual(len(bindings), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
