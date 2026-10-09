"""
VIGILIA Windows agent — collector tests.

The suite runs on Linux too: the native Win32 stubs are only bound on Windows,
so the non-native paths (psutil, stdlib fallback) are what gets exercised on
the dev host. Every test degrades to a skip rather than a failure when a
metric is unavailable on the host it runs on.

Run:  python3 -m unittest discover -s agents/windows -p 'test_*.py' -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.logger import Logger  # noqa: E402
from agents.common.version import PROTOCOL_VERSION  # noqa: E402
from agents.windows.src.collectors import AGENT_VERSION, WindowsCollector  # noqa: E402

LOG = Logger(quiet=True)


class WindowsCollectorTests(unittest.TestCase):
    def test_shared_protocol_version_is_available(self) -> None:
        self.assertEqual(PROTOCOL_VERSION, 1)
    def setUp(self) -> None:
        self.collector = WindowsCollector(LOG)

    # ---------------- identification ----------------
    def test_system_info_has_required_keys(self) -> None:
        info = self.collector.get_system_info()
        self.assertIn("agent_version", info)
        self.assertIn("platform", info)
        self.assertIn("os", info)
        self.assertIn("hostname", info)
        self.assertEqual(info["platform"], "windows")
        self.assertEqual(info["agent_version"], AGENT_VERSION)

    def test_system_info_values_are_the_right_type(self) -> None:
        info = self.collector.get_system_info()
        self.assertIsInstance(info["agent_version"], str)
        self.assertIsInstance(info["hostname"], str)
        if "cpu_cores" in info:
            self.assertIsInstance(info["cpu_cores"], int)
            self.assertGreater(info["cpu_cores"], 0)
        if "total_memory_mb" in info:
            self.assertIsInstance(info["total_memory_mb"], int)
            self.assertGreater(info["total_memory_mb"], 0)
        if "total_storage_gb" in info:
            self.assertIsInstance(info["total_storage_gb"], int)
            self.assertGreater(info["total_storage_gb"], 0)

    def test_os_version_reports_the_platform(self) -> None:
        version = self.collector._os_version()
        if version is None:
            self.skipTest("platform.release() unavailable")
        self.assertTrue(version.startswith("Windows"))

    # ---------------- cpu ----------------
    def test_cpu_usage_is_a_percentage(self) -> None:
        value = self.collector.get_cpu_usage()
        if value is None:
            self.skipTest("cpu usage unavailable on this host")
        self.assertIsInstance(value, float)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    def test_cpu_usage_without_psutil(self) -> None:
        """The psutil-free path must still produce a percentage."""
        import time

        with patch("agents.windows.src.collectors._HAS_PSUTIL", False):
            collector = WindowsCollector(LOG)
            time.sleep(0.25)
            value = collector.get_cpu_usage()
        if value is None:
            self.skipTest("no native cpu counters on this host")
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    # ---------------- memory ----------------
    def test_memory_usage_is_a_percentage(self) -> None:
        value = self.collector.get_memory_usage()
        if value is None:
            self.skipTest("memory usage unavailable")
        self.assertIsInstance(value, float)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    def test_memory_usage_without_psutil(self) -> None:
        with patch("agents.windows.src.collectors._HAS_PSUTIL", False):
            collector = WindowsCollector(LOG)
            value = collector.get_memory_usage()
        if value is None:
            self.skipTest("no native memory counters on this host")
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    # ---------------- storage ----------------
    def test_storage_usage_is_a_percentage(self) -> None:
        value = self.collector.get_storage_usage()
        if value is None:
            self.skipTest("storage usage unavailable")
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    def test_storage_usage_without_psutil(self) -> None:
        with patch("agents.windows.src.collectors._HAS_PSUTIL", False):
            collector = WindowsCollector(LOG)
            value = collector.get_storage_usage()
        if value is None:
            self.skipTest("no readable system drive on this host")
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    # ---------------- battery ----------------
    def test_battery_status_is_a_percentage_or_none(self) -> None:
        value = self.collector.get_battery_status()
        if value is None:
            self.skipTest("no battery on this host")
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    def test_battery_unknown_sentinel_is_none(self) -> None:
        """Windows reports 255 when the charge level is unknown."""
        with patch("agents.windows.src.collectors._HAS_PSUTIL", False):
            collector = WindowsCollector(LOG)
            status = collector._power_status()
            if status is None:
                self.skipTest("no native power status on this host")
            with patch.object(type(status), "BatteryLifePercent", 255, create=True):
                self.assertIsNone(collector.get_battery_status())

    # ---------------- uptime ----------------
    def test_uptime_is_non_negative(self) -> None:
        value = self.collector.get_uptime()
        if value is None:
            self.skipTest("uptime unavailable")
        self.assertIsInstance(value, int)
        self.assertGreaterEqual(value, 0)

    # ---------------- network ----------------
    def test_network_status_reports_connectivity(self) -> None:
        value = self.collector.get_network_status()
        if value is None:
            self.skipTest("network status unavailable")
        self.assertIn("connected", value)
        self.assertIsInstance(value["connected"], bool)

    # ---------------- processes ----------------
    def test_processes_are_the_top_few(self) -> None:
        procs = self.collector.get_processes()
        self.assertLessEqual(len(procs), 10)
        for proc in procs:
            self.assertIn("pid", proc)
            self.assertIn("name", proc)
            self.assertIn("cpu", proc)
            self.assertIn("ram", proc)

    def test_processes_without_psutil(self) -> None:
        with patch("agents.windows.src.collectors._HAS_PSUTIL", False):
            collector = WindowsCollector(LOG)
            procs = collector.get_processes()
        self.assertEqual(procs, [])


if __name__ == "__main__":
    unittest.main()
