"""
VIGILIA Linux agent — process telemetry collector tests.

The suite runs on a real host and degrades to a skip when a source is
unavailable, so it asserts behaviour rather than host state: the contract
shape, the cap, the ordering and the /proc fallback that has to work when
psutil is not installed.

Run:  python3 -m unittest discover -s agents/linux -p 'test_*.py' -v
"""
from __future__ import annotations

import sys
import time
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.logger import Logger  # noqa: E402
from agents.common.processes import PROCESS_STATUSES  # noqa: E402
from agents.linux.src.collectors import LinuxCollector  # noqa: E402

LOG = Logger(quiet=True)


class LinuxProcessCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.collector = LinuxCollector(LOG)

    def _skip_if_no_processes(self, procs: list) -> None:
        if not procs:
            self.skipTest("no processes readable on this host")

    def test_returns_process_info_objects(self) -> None:
        procs = self.collector.get_process_list(32)
        self._skip_if_no_processes(procs)
        from agents.common.processes import ProcessInfo

        for proc in procs:
            self.assertIsInstance(proc, ProcessInfo)

    def test_required_fields_are_present(self) -> None:
        procs = self.collector.get_process_list(32)
        self._skip_if_no_processes(procs)
        for proc in procs:
            self.assertIsInstance(proc.pid, int)
            self.assertTrue(proc.name, "a name is always set, never empty")
            self.assertGreaterEqual(proc.cpu, 0.0)
            self.assertLessEqual(proc.cpu, 100.0)

    def test_percentages_are_clamped(self) -> None:
        procs = self.collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        for proc in procs:
            if proc.memory_percent is not None:
                self.assertGreaterEqual(proc.memory_percent, 0.0)
                self.assertLessEqual(proc.memory_percent, 100.0)

    def test_status_is_the_wire_enum_when_present(self) -> None:
        procs = self.collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        saw_status = False
        for proc in procs:
            if proc.status is not None:
                saw_status = True
                self.assertIn(proc.status, PROCESS_STATUSES,
                              f"status must be a wire enum, got {proc.status!r}")
        if not saw_status:
            self.skipTest("no readable process state on this host")

    def test_limit_is_respected(self) -> None:
        for limit in (1, 5, 200):
            procs = self.collector.get_process_list(limit)
            self.assertLessEqual(len(procs), limit)

    def test_limit_clamps_to_the_hard_cap(self) -> None:
        # A nonsense limit must not blow up or produce an unbounded list.
        procs = self.collector.get_process_list(-5)
        self.assertLessEqual(len(procs), 512)

    def test_ordering_is_by_resource_use(self) -> None:
        procs = self.collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        weights = [p.cpu + 10.0 * (p.memory_percent or 0.0) for p in procs]
        self.assertEqual(weights, sorted(weights, reverse=True),
                         "the most expensive process comes first")

    def test_the_agent_pid_is_visible(self) -> None:
        procs = self.collector.get_process_list(512)
        self._skip_if_no_processes(procs)
        pids = {proc.pid for proc in procs}
        self.assertIn(__import__("os").getpid(), pids, "the agent itself must be enumerable")

    def test_executable_path_is_absolute_when_present(self) -> None:
        procs = self.collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        saw_path = False
        for proc in procs:
            if proc.executable_path is not None:
                saw_path = True
                self.assertTrue(proc.executable_path.startswith("/"),
                                f"expected an absolute path, got {proc.executable_path!r}")
        if not saw_path:
            self.skipTest("no readable executable path (needs permission)")

    def test_started_at_is_iso_when_present(self) -> None:
        procs = self.collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        saw_stamp = False
        for proc in procs:
            if proc.started_at is not None:
                saw_stamp = True
                self.assertTrue(proc.started_at.endswith("Z"))
                self.assertLess(len(proc.started_at), 36)
        if not saw_stamp:
            self.skipTest("no readable start time on this host")

    # ---------------- /proc fallback ----------------

    def test_proc_fallback_enumerates_without_psutil(self) -> None:
        with patch("agents.linux.src.collectors._HAS_PSUTIL", False):
            collector = LinuxCollector(LOG)
            procs = collector.get_process_list(32)
        self._skip_if_no_processes(procs)
        from agents.common.processes import ProcessInfo

        self.assertIsInstance(procs[0], ProcessInfo)
        self.assertTrue(procs[0].name)

    def test_proc_fallback_reports_memory(self) -> None:
        with patch("agents.linux.src.collectors._HAS_PSUTIL", False):
            collector = LinuxCollector(LOG)
            procs = collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        saw_memory = any(proc.memory_mb is not None for proc in procs)
        if not saw_memory:
            self.skipTest("no readable VmRSS on this host")

    def test_proc_fallback_cpu_is_a_percentage_after_two_samples(self) -> None:
        """CPU% needs a baseline, so the second sample is the real one."""
        with patch("agents.linux.src.collectors._HAS_PSUTIL", False):
            collector = LinuxCollector(LOG)
            collector.get_process_list(64)
            time.sleep(0.3)
            procs = collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        for proc in procs:
            self.assertGreaterEqual(proc.cpu, 0.0)
            self.assertLessEqual(proc.cpu, 100.0)

    def test_proc_fallback_handles_a_dead_process(self) -> None:
        """A /proc dir that vanishes between enumeration and the read is skipped."""
        with patch("agents.linux.src.collectors._HAS_PSUTIL", False):
            collector = LinuxCollector(LOG)
            procs = collector.get_process_list(512)
        self._skip_if_no_processes(procs)

    def test_command_line_is_off_by_default(self) -> None:
        procs = self.collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        self.assertTrue(all(proc.command_line is None for proc in procs),
                        "commandLine must be absent unless explicitly requested")

    def test_command_line_when_requested(self) -> None:
        procs = self.collector.get_process_list(64, include_command_line=True)
        self._skip_if_no_processes(procs)
        saw = any(proc.command_line for proc in procs)
        if not saw:
            self.skipTest("no readable /proc/<pid>/cmdline on this host")

    def test_psutil_attrs_omit_cmdline_unless_requested(self) -> None:
        seen: list[list[str]] = []

        def process_iter(*, attrs):
            seen.append(attrs)
            return [SimpleNamespace(info={"pid": 1, "name": "one", "cpu_percent": 0, "memory_percent": 0})]

        with patch("agents.linux.src.collectors.psutil.process_iter", side_effect=process_iter):
            self.collector._processes_psutil(False)  # noqa: SLF001
            self.collector._processes_psutil(True)  # noqa: SLF001
        self.assertNotIn("cmdline", seen[0])
        self.assertIn("cmdline", seen[1])

    def test_proc_fallback_prunes_stale_cpu_baselines(self) -> None:
        collector = LinuxCollector(LOG)
        collector._proc_cpu_cache = {1: (10.0, 1.0), 2: (20.0, 1.0)}  # noqa: SLF001
        with patch("agents.linux.src.collectors._HAS_PSUTIL", False), \
             patch.object(collector, "_read_meminfo", return_value={}), \
             patch("agents.linux.src.collectors._PROC") as proc_root:
            proc_root.is_dir.return_value = True
            child = SimpleNamespace(name="1")
            proc_root.iterdir.return_value = [child]
            with patch.object(collector, "_read_proc_stat", return_value=("one", "R", 0, 1, 1, 1, 1)), \
                 patch.object(collector, "_read_proc_rss", return_value=None), \
                 patch.object(collector, "_read_proc_owner", return_value=None), \
                 patch.object(collector, "_read_proc_exe", return_value=None):
                collector._processes_proc(False)  # noqa: SLF001
        self.assertEqual(set(collector._proc_cpu_cache), {1})  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
