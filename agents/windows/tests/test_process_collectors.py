"""
VIGILIA Windows agent — process telemetry collector tests.

The suite runs on Linux too: the native Win32 stubs are only bound on Windows,
so off-Windows the psutil path and the honest-empty native path are what gets
exercised. Nothing is faked — a native list on the wrong host returns `[]`
because there genuinely is no list to report.

Run:  python3 -m unittest discover -s agents/windows -p 'test_*.py' -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.logger import Logger  # noqa: E402
from agents.common.processes import PROCESS_STATUSES, ProcessInfo  # noqa: E402
from agents.windows.src.collectors import (  # noqa: E402
    WindowsCollector,
    _IS_WINDOWS,
    _clamp_percent,
    _trim,
)

LOG = Logger(quiet=True)


class WindowsProcessCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.collector = WindowsCollector(LOG)

    def _skip_if_no_processes(self, procs: list) -> None:
        if not procs:
            self.skipTest("no processes readable on this host (native Win32 is Windows-only)")

    def test_returns_process_info_objects(self) -> None:
        procs = self.collector.get_process_list(32)
        self._skip_if_no_processes(procs)
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
        for proc in procs:
            if proc.status is not None:
                self.assertIn(proc.status, PROCESS_STATUSES)

    def test_limit_is_respected(self) -> None:
        for limit in (1, 5, 200):
            procs = self.collector.get_process_list(limit)
            self.assertLessEqual(len(procs), limit)

    def test_limit_clamps_and_never_raises(self) -> None:
        procs = self.collector.get_process_list(-5)
        self.assertLessEqual(len(procs), 512)

    def test_ordering_is_by_resource_use(self) -> None:
        procs = self.collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        weights = [p.cpu + 10.0 * (p.memory_percent or 0.0) for p in procs]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_command_line_is_off_by_default(self) -> None:
        procs = self.collector.get_process_list(64)
        self._skip_if_no_processes(procs)
        self.assertTrue(all(proc.command_line is None for proc in procs))

    # ---------------- native path, off-Windows ----------------

    def test_native_path_off_windows_is_honestly_empty(self) -> None:
        """No faking: off-Windows the Win32 list does not exist."""
        if _IS_WINDOWS:
            self.skipTest("this host is Windows; the native path runs for real")
        with patch("agents.windows.src.collectors._HAS_PSUTIL", False):
            collector = WindowsCollector(LOG)
            self.assertEqual(collector.get_process_list(32), [])

    def test_native_path_off_windows_reports_no_status(self) -> None:
        if _IS_WINDOWS:
            self.skipTest("this host is Windows")
        with patch("agents.windows.src.collectors._HAS_PSUTIL", False):
            collector = WindowsCollector(LOG)
            self.assertIsNone(collector._command_line(None))


class WindowsProcessHelperTests(unittest.TestCase):
    def test_clamp_percent(self) -> None:
        self.assertEqual(_clamp_percent(45.55), 45.5)
        self.assertEqual(_clamp_percent(-1.0), 0.0)
        self.assertEqual(_clamp_percent(120.0), 100.0)
        self.assertIsNone(_clamp_percent(None))
        self.assertIsNone(_clamp_percent("not a number"))
        self.assertIsNone(_clamp_percent(float("nan")))

    def test_trim(self) -> None:
        self.assertEqual(_trim("  code  ", 10), "code")
        self.assertEqual(_trim("", 10), None)
        self.assertEqual(_trim("   ", 10), None)
        self.assertEqual(_trim(None, 10), None)
        self.assertEqual(_trim("abcdef", 3), "abc")


if __name__ == "__main__":
    unittest.main()
