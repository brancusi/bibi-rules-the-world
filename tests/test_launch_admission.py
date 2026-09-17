#!/usr/bin/env python3
"""Deterministic tests for launch admission.

The readings are the ones sysstat recorded on the managed Droplet around the
2026-09-17 wedge (`sar -r -S -q MEM`), so every threshold in the reviewed
policy is pinned to the moment it would have mattered:

  * 09-15 12:10  a busy but healthy fleet: swap 57% used, 2.8 GiB available.
  * 09-16 08:10  swap past its critical mark, 2.6 GiB available.
  * 09-17 05:00  a near miss: 0.4 GiB available, tasks stalling on memory.
  * 09-17 07:30  five minutes before the triggering launch: swap 100% used,
                 0.77 GiB available, and no stall yet - the last calm reading.
  * 09-17 08:00  the wedge: 0.27 GiB available, 93% of time stalled.

No test reads a real process, sends a real signal, launches a real command,
or touches the real reservation ledger.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_memory_guard import (  # noqa: E402
    HEALTHY_MEMINFO,
    GuardHarness,
    GuardTestCase,
    bridge_tree,
    guard,
    process,
)

MEM_TOTAL = 8_131_792
SWAP_TOTAL = 2_097_148


def meminfo(available_kb: int, swap_free_kb: int, swap_total_kb: int = SWAP_TOTAL) -> dict:
    return {
        "MemTotal": MEM_TOTAL,
        "MemAvailable": available_kb,
        "SwapTotal": swap_total_kb,
        "SwapFree": swap_free_kb,
    }


def pressure(some_avg60: float, some_avg10: float | None = None) -> dict:
    return {
        "some_avg10": some_avg60 if some_avg10 is None else some_avg10,
        "some_avg60": some_avg60,
        "full_avg60": some_avg60 * 0.7,
    }


BUSY_FLEET = (meminfo(2_802_780, 903_000), pressure(0.0))
SWAP_CRITICAL = (meminfo(2_634_880, 239_000), pressure(0.0))
NEAR_MISS = (meminfo(407_120, 140), pressure(15.74, 4.64))
BEFORE_TRIGGER = (meminfo(769_520, 60), pressure(0.01, 0.0))
WEDGED = (meminfo(271_360, 104), pressure(92.89, 92.12))


def facts(reading: tuple[dict, dict | None]):
    document = {"meminfo": reading[0]}
    if reading[1] is not None:
        document["pressure"] = reading[1]
    return guard.FixtureSource(document).read_memory_facts()


def decide(reading, pending_kb: int = 0, **overrides):
    config = guard.load_config(None)
    config.update(overrides)
    return guard.decide_admission(facts(reading), pending_kb, config)


class IncidentShapedPressureTests(unittest.TestCase):
    def test_the_triggering_launch_is_refused_before_any_stall_is_visible(self) -> None:
        admission = decide(BEFORE_TRIGGER)
        self.assertEqual(admission.decision, "refuse")
        self.assertEqual(admission.exit_code, guard.EXIT_REFUSED_PRESSURE)
        self.assertIn(guard.REASON_HEADROOM, admission.reasons)
        self.assertIn(guard.REASON_SWAP_EXHAUSTED, admission.reasons)
        # PSI was calm at 07:30, so headroom - not stall - has to carry this.
        self.assertNotIn(guard.REASON_STALL, admission.reasons)
        self.assertEqual(admission.floor_percent, 25)
        self.assertLess(admission.projected_available_kb, 0)

    def test_the_wedged_host_is_refused_on_headroom_and_on_stall(self) -> None:
        admission = decide(WEDGED)
        self.assertEqual(admission.decision, "refuse")
        self.assertIn(guard.REASON_HEADROOM, admission.reasons)
        self.assertIn(guard.REASON_STALL, admission.reasons)

    def test_the_near_miss_two_hours_earlier_is_refused(self) -> None:
        admission = decide(NEAR_MISS)
        self.assertEqual(admission.decision, "refuse")
        self.assertIn(guard.REASON_STALL, admission.reasons)

    def test_fleet_growth_stops_once_swap_is_no_longer_a_cushion(self) -> None:
        """The earliest refusal: a day before the wedge, with 2.6 GiB available."""
        admission = decide(SWAP_CRITICAL)
        self.assertEqual(admission.decision, "refuse")
        self.assertFalse(admission.swap_cushion)
        self.assertEqual(
            admission.reasons, (guard.REASON_HEADROOM, guard.REASON_SWAP_EXHAUSTED)
        )

    def test_stall_alone_refuses_even_with_ample_available_memory(self) -> None:
        admission = decide((HEALTHY_MEMINFO, pressure(35.0)))
        self.assertEqual(admission.reasons, (guard.REASON_STALL,))

    def test_a_refusal_explains_itself_in_numbers_an_operator_can_check(self) -> None:
        text = "\n".join(guard.explain_refusal(decide(BEFORE_TRIGGER)))
        self.assertIn("MemAvailable is 751 MB", text)
        self.assertIn("budgeted at 768 MB", text)
        self.assertIn("1985 MB (25% of RAM)", text)
        self.assertIn("swap is 100% used", text)
        self.assertIn("never kills a worker", text)


class HealthyStateTests(unittest.TestCase):
    def test_a_healthy_host_admits_cleanly(self) -> None:
        admission = decide((HEALTHY_MEMINFO, pressure(0.0)))
        self.assertTrue(admission.admitted)
        self.assertEqual(admission.exit_code, guard.EXIT_ADMIT)
        self.assertEqual(admission.level, "ok")
        self.assertEqual(admission.reasons, ())
        self.assertTrue(admission.swap_cushion)
        self.assertEqual(admission.floor_percent, 15)

    def test_a_busy_fleet_is_admitted_but_warned_days_before_any_refusal(self) -> None:
        admission = decide(BUSY_FLEET)
        self.assertTrue(admission.admitted)
        self.assertEqual(admission.level, "warn")
        self.assertIn("swap-used-warn", admission.reasons)

    def test_old_cold_pages_in_swap_do_not_refuse_a_host_with_ample_ram(self) -> None:
        admission = decide((meminfo(5_000_000, 400_000), pressure(0.0)))
        self.assertFalse(admission.swap_cushion)
        self.assertTrue(admission.admitted)
        self.assertEqual(admission.level, "critical")
        self.assertIn("swap-used-critical", admission.reasons)

    def test_a_swapless_host_is_held_to_the_stricter_floor(self) -> None:
        swapless = meminfo(2_500_000, 0, swap_total_kb=0)
        admission = decide((swapless, pressure(0.0)))
        self.assertEqual(admission.decision, "refuse")
        self.assertIn(guard.REASON_NO_SWAP, admission.reasons)
        self.assertTrue(decide((meminfo(2_500_000, SWAP_TOTAL), pressure(0.0))).admitted)

    def test_a_kernel_without_psi_is_decided_on_meminfo_alone(self) -> None:
        admission = decide((HEALTHY_MEMINFO, None))
        self.assertTrue(admission.admitted)
        self.assertIsNone(admission.memory_stall_avg60)
        self.assertEqual(decide((BEFORE_TRIGGER[0], None)).decision, "refuse")

    def test_a_disabled_gate_admits_and_says_why(self) -> None:
        admission = decide(WEDGED, launch_gate_enabled=False)
        self.assertTrue(admission.admitted)
        self.assertEqual(admission.reasons, (guard.REASON_GATE_DISABLED,))


class UnavailableTelemetryTests(unittest.TestCase):
    def assert_refused_for_telemetry(self, reading: dict, missing: str) -> None:
        admission = decide((reading, None))
        self.assertEqual(admission.decision, "refuse")
        self.assertEqual(admission.level, "unknown")
        self.assertEqual(admission.reasons, (guard.REASON_TELEMETRY,))
        self.assertEqual(admission.exit_code, guard.EXIT_REFUSED_TELEMETRY)
        self.assertIn(missing, admission.missing)

    def test_an_empty_reading_is_refused_not_mistaken_for_a_healthy_host(self) -> None:
        # read_memory() alone reports 100% available here, which is the trap.
        self.assertEqual(guard.read_memory({}).available_percent, 100.0)
        self.assert_refused_for_telemetry({}, "MemTotal")

    def test_a_reading_without_mem_available_is_refused(self) -> None:
        self.assert_refused_for_telemetry({"MemTotal": MEM_TOTAL}, "MemAvailable")

    def test_an_implausible_reading_is_refused(self) -> None:
        self.assert_refused_for_telemetry(
            {"MemTotal": 1_000, "MemAvailable": 2_000}, "MemAvailable-exceeds-MemTotal"
        )

    def test_an_unreadable_proc_is_reported_as_unavailable_not_raised(self) -> None:
        with TemporaryDirectory() as tmp:
            read = guard.ProcSource(proc_root=tmp).read_memory_facts()
        self.assertEqual(read.meminfo, {})
        self.assertIsNone(read.pressure)
        config = guard.load_config(None)
        self.assertEqual(
            guard.decide_admission(read, 0, config).exit_code, guard.EXIT_REFUSED_TELEMETRY
        )

    def test_real_proc_documents_are_parsed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pressure").mkdir()
            (root / "meminfo").write_text(
                "MemTotal:        8131792 kB\nMemAvailable:     769520 kB\n"
                "SwapTotal:       2097148 kB\nSwapFree:              60 kB\n"
            )
            (root / "pressure" / "memory").write_text(
                "some avg10=83.46 avg60=62.77 avg300=30.03 total=1649\n"
                "full avg10=59.11 avg60=46.18 avg300=22.08 total=887\n"
            )
            read = guard.ProcSource(proc_root=tmp).read_memory_facts()
        self.assertEqual(read.meminfo["MemAvailable"], 769_520)
        self.assertEqual(read.pressure.some_avg60, 62.77)
        self.assertEqual(read.pressure.full_avg60, 46.18)

    def test_a_malformed_pressure_file_is_absent_not_fatal(self) -> None:
        self.assertIsNone(guard.parse_pressure("not a psi document\n"))

    def test_the_scheduled_run_alerts_when_telemetry_is_unavailable(self) -> None:
        with TemporaryDirectory() as tmp:
            outcome, signaller, _, _ = GuardHarness(Path(tmp)).run(
                [], mode="once", meminfo={"SwapTotal": 0}
            )
        self.assertIn(guard.REASON_TELEMETRY, outcome.alerts)
        self.assertIn("launch-admission-closed", outcome.alerts)
        self.assertEqual(signaller.sent, [])


class ReservationLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = str(Path(self._tmp.name) / "runtime" / "bibi-memory-guard")
        self.clock = 1_000_000.0
        self.config = guard.load_config(None)

    def ledger(self, **kwargs) -> "guard.LaunchLedger":
        return guard.LaunchLedger(
            kwargs.pop("directory", self.directory),
            float(self.config["launch_reservation_seconds"]),
            now=lambda: self.clock,
            sleep=lambda _seconds: None,
            **kwargs,
        )

    def admit(self, reading, reserve: bool = True):
        return guard.admit_launch(
            facts(reading), self.config, self.ledger(writable=reserve), reserve
        )

    def test_a_burst_of_launches_cannot_all_spend_the_same_headroom(self) -> None:
        # 3.2 GiB available admits exactly two 768 MiB workers above the floor.
        reading = (meminfo(3_300_000, SWAP_TOTAL), pressure(0.0))
        decisions = [self.admit(reading).decision for _ in range(4)]
        self.assertEqual(decisions, ["admit", "admit", "refuse", "refuse"])

    def test_reservations_expire_once_proc_can_show_the_memory_for_real(self) -> None:
        reading = (meminfo(3_300_000, SWAP_TOTAL), pressure(0.0))
        self.admit(reading)
        self.admit(reading)
        self.assertEqual(self.admit(reading).decision, "refuse")
        self.clock += float(self.config["launch_reservation_seconds"]) + 1
        third = self.admit(reading)
        self.assertTrue(third.admitted)
        self.assertEqual(third.pending_reserved_kb, 0)

    def test_a_refused_launch_reserves_nothing(self) -> None:
        self.admit(BEFORE_TRIGGER)
        with self.ledger(writable=False) as ledger:
            self.assertEqual(ledger.pending_kb, 0)

    def test_a_pure_query_creates_nothing_and_reserves_nothing(self) -> None:
        admission = self.admit((HEALTHY_MEMINFO, None), reserve=False)
        self.assertTrue(admission.admitted)
        self.assertFalse(Path(self.directory).exists())

    def test_a_pure_query_still_sees_existing_reservations(self) -> None:
        reading = (meminfo(3_300_000, SWAP_TOTAL), pressure(0.0))
        self.admit(reading)
        self.assertEqual(
            self.admit(reading, reserve=False).pending_reserved_kb,
            int(self.config["launch_worker_reserve_kb"]),
        )

    def test_an_unusable_ledger_falls_back_to_proc_facts_and_says_so(self) -> None:
        ledger = self.ledger(directory=None)
        admission = guard.admit_launch(facts((HEALTHY_MEMINFO, None)), self.config, ledger, True)
        self.assertTrue(admission.admitted)
        self.assertEqual(ledger.status, "unavailable")
        refused = guard.admit_launch(facts(BEFORE_TRIGGER), self.config, self.ledger(directory=None), True)
        self.assertEqual(refused.decision, "refuse")

    def test_garbage_and_future_entries_are_ignored(self) -> None:
        Path(self.directory).mkdir(parents=True)
        (Path(self.directory) / guard.LaunchLedger.FILE_NAME).write_text(
            f"not a line\n{self.clock + 86_400:.0f} 999999999\n{self.clock - 10:.0f} -5\n"
            f"{self.clock - 10:.0f} 1000\n"
        )
        with self.ledger(writable=False) as ledger:
            self.assertEqual(ledger.pending_kb, 1000)

    def test_a_symlinked_ledger_is_never_followed(self) -> None:
        Path(self.directory).mkdir(parents=True)
        target = Path(self._tmp.name) / "victim"
        target.write_text("keep\n")
        (Path(self.directory) / guard.LaunchLedger.FILE_NAME).symlink_to(target)
        ledger = self.ledger()
        guard.admit_launch(facts((HEALTHY_MEMINFO, None)), self.config, ledger, True)
        self.assertEqual(ledger.status, "unavailable")
        self.assertEqual(target.read_text(), "keep\n")

    def test_the_ledger_stays_bounded(self) -> None:
        self.config["launch_worker_reserve_kb"] = 1
        reading = (HEALTHY_MEMINFO, None)
        for _ in range(guard.LaunchLedger.MAX_ENTRIES + 40):
            self.admit(reading)
        lines = (Path(self.directory) / guard.LaunchLedger.FILE_NAME).read_text().splitlines()
        self.assertLessEqual(len(lines), guard.LaunchLedger.MAX_ENTRIES)


class ActiveWorkPreservationTests(GuardTestCase):
    """Pressure may close the gate. It may never touch a running worker."""

    def live_fleet(self) -> list[dict]:
        processes: list[dict] = []
        for index in range(15):
            worktree = self.harness.worktree(f"project{index}", slot=index + 1)
            self.harness.task(f"lane-{index}", worktree, status="working: implementing")
            processes.append(
                process(
                    5000 + index,
                    4000,
                    comm="claude",
                    argv=["claude"],
                    exe="/home/bibi/.local/share/claude/versions/2.1.274",
                    cwd=worktree,
                    rss_kb=450_000,
                )
            )
        # One lane is also running browser QA, the family the reaper knows.
        self.harness.bind_session("lane0", 2000, 9884)
        processes.extend(bridge_tree(2000, self.harness.worktree("project0", slot=1)))
        return processes

    def test_the_incident_state_closes_the_gate_and_signals_nothing(self) -> None:
        outcome, signaller, reporter, _ = self.harness.run(
            self.live_fleet(), mode="once", meminfo=WEDGED[0]
        )
        self.assertEqual(signaller.sent, [], "memory pressure must never signal a live worker")
        self.assertEqual(outcome.cleanups, [])
        self.assertEqual(outcome.level, "critical")
        self.assertEqual(outcome.admission.decision, "refuse")
        self.assertIn("launch-admission-closed", outcome.alerts)
        self.assertIn("pressure-without-eligible-leak", outcome.alerts)
        self.assertTrue(any(line.startswith("admission: decision=refuse") for line in reporter.lines))
        self.assertEqual(outcome.to_state(0.0)["admission"]["decision"], "refuse")

    def test_a_closed_gate_never_widens_kill_eligibility(self) -> None:
        def verdicts(outcome) -> list[tuple[int, str, str]]:
            return [(c.root.pid, c.decision, c.reason) for c in outcome.candidates]

        healthy, _, _, _ = self.harness.run(self.live_fleet(), meminfo=HEALTHY_MEMINFO)
        wedged, _, _, _ = self.harness.run(self.live_fleet(), meminfo=WEDGED[0])
        self.assertEqual(verdicts(healthy), verdicts(wedged))
        self.assertTrue(all(decision == "refused" for _, decision, _ in verdicts(wedged)))

    def test_one_cgroup_oom_is_not_evidence_of_host_pressure(self) -> None:
        """The Captain Channel hit its own 256 MiB MemoryMax at 08:50.

        A unit dying at its own cgroup ceiling changes nothing in /proc/meminfo,
        and admission reads nothing else: no journal, no unit state, no process.
        A host with headroom stays open whatever any single cgroup just did.
        """
        admission = decide((HEALTHY_MEMINFO, pressure(0.0)))
        self.assertTrue(admission.admitted)
        source = Path(guard.__file__).read_text(encoding="utf-8")
        admission_code = source.split("# Launch admission\n")[1].split("# Tree discovery and eligibility\n")[0]
        for forbidden in (r"\boom\b", "journal", "cgroup", "systemctl", r"os\.kill", r"signal\.", "Signaller", r"\.send\("):
            self.assertIsNone(
                re.search(forbidden, admission_code, re.IGNORECASE),
                f"admission must not consult {forbidden}",
            )

    def test_the_admit_command_cannot_signal_or_inventory_anything(self) -> None:
        with TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "facts.json"
            fixture.write_text(json.dumps({"meminfo": WEDGED[0], "pressure": WEDGED[1]}))
            stdout, stderr = StringIO(), StringIO()
            with mock.patch.object(guard.os, "kill", side_effect=AssertionError("signalled")), \
                    mock.patch.object(guard, "RealSignaller", side_effect=AssertionError("signaller")), \
                    mock.patch.object(guard, "RecordingSignaller", side_effect=AssertionError("signaller")), \
                    mock.patch.object(guard.ProcSource, "read", side_effect=AssertionError("inventoried")), \
                    mock.patch.object(guard.FixtureSource, "read", side_effect=AssertionError("inventoried")), \
                    redirect_stdout(stdout), redirect_stderr(stderr):
                code = guard.main(["admit", "--config", "/nonexistent", "--proc-source", str(fixture)])
        self.assertEqual(code, guard.EXIT_REFUSED_PRESSURE)
        self.assertIn("admission: decision=refuse", stdout.getvalue())
        self.assertIn("launch REFUSED", stderr.getvalue())
        self.assertIn("never kills a worker", stderr.getvalue())


class AdmitCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.launched: list[tuple[str, list[str]]] = []

    def config_file(self, available_kb: int) -> str:
        # The real /proc is read, so the floor is set from a percentage that the
        # host under test either certainly clears (0) or certainly misses (100).
        path = self.tmp / "guard.conf"
        path.write_text(
            f"launch_ledger_dir={self.tmp / 'ledger'}\n"
            "launch_worker_reserve_kb=1\n"
            f"launch_min_mem_available_percent={available_kb}\n"
            f"launch_min_mem_available_no_swap_percent={available_kb}\n"
            "launch_max_memory_stall_percent=101\n"
        )
        return str(path)

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        # The live path reads this host's real /proc, so it runs with every
        # destructive or process-inventory entry point rigged to fail the test.
        stdout, stderr = StringIO(), StringIO()
        with mock.patch.object(guard.os, "kill", side_effect=AssertionError("signalled")), \
                mock.patch.object(guard, "RealSignaller", side_effect=AssertionError("signaller")), \
                mock.patch.object(guard.ProcSource, "read", side_effect=AssertionError("inventoried")), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = guard.main(argv, execvp=lambda name, args: self.launched.append((name, list(args))))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_an_admitted_launch_starts_exactly_the_given_command(self) -> None:
        code, stdout, stderr = self.run_main(
            ["admit", "--config", self.config_file(0), "--", "claude", "--model", "x", "--", "brief"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.launched, [("claude", ["claude", "--model", "x", "--", "brief"])])
        self.assertEqual(stdout, "", "stdout belongs to the launched command")
        self.assertIn("admission: decision=admit", stderr)
        self.assertTrue((self.tmp / "ledger" / guard.LaunchLedger.FILE_NAME).read_text().strip())

    def test_a_refused_launch_never_starts_the_command(self) -> None:
        code, _, stderr = self.run_main(
            ["admit", "--config", self.config_file(100), "--", "claude", "brief"]
        )
        self.assertEqual(code, guard.EXIT_REFUSED_PRESSURE)
        self.assertEqual(self.launched, [])
        self.assertIn("launch REFUSED", stderr)
        self.assertIn("Nothing was started and nothing was stopped", stderr)

    def test_no_reserve_is_a_pure_query(self) -> None:
        code, stdout, _ = self.run_main(["admit", "--no-reserve", "--config", self.config_file(0)])
        self.assertEqual(code, 0)
        self.assertIn("ledger=empty", stdout)
        self.assertFalse((self.tmp / "ledger").exists())

    def test_json_output_is_the_recorded_shape(self) -> None:
        code, stdout, _ = self.run_main(
            ["admit", "--no-reserve", "--json", "--config", self.config_file(0)]
        )
        self.assertEqual(code, 0)
        document = json.loads(stdout[stdout.index("{") :])
        self.assertEqual(document["decision"], "admit")
        self.assertIn("projected_available_kb", document)

    def test_a_missing_command_reports_not_found(self) -> None:
        def missing(_name, _args):
            raise FileNotFoundError(2, "No such file or directory")

        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = guard.main(["admit", "--config", self.config_file(0), "--", "nope"], execvp=missing)
        self.assertEqual(code, 127)
        self.assertIn("launch_failed", stderr.getvalue())

    def test_synthetic_facts_can_never_launch_a_command(self) -> None:
        fixture = self.tmp / "facts.json"
        fixture.write_text(json.dumps({"meminfo": HEALTHY_MEMINFO}))
        code, _, stderr = self.run_main(
            ["admit", "--config", self.config_file(0), "--proc-source", str(fixture), "--", "claude"]
        )
        self.assertEqual(code, 2)
        self.assertEqual(self.launched, [])
        self.assertIn("synthetic", stderr)

    def test_a_command_is_only_accepted_by_admit(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            guard.main(["report", "--", "claude"])
        with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
            guard.main(["admit", "--"])
        self.assertEqual(self.launched, [])

    def test_the_admission_policy_is_validated_like_the_rest(self) -> None:
        path = self.tmp / "bad.conf"
        path.write_text("launch_gate_enabled=maybe\n")
        with self.assertRaises(guard.ConfigError):
            guard.load_config(str(path))
        path.write_text("launch_worker_reserve_kb=lots\n")
        with self.assertRaises(guard.ConfigError):
            guard.load_config(str(path))


if __name__ == "__main__":
    unittest.main()
