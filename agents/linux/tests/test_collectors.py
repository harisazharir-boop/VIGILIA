"""
VIGILIA Linux agent — collector tests.

Run:  python3 -m unittest discover -s agents/linux -p 'test_*.py' -v
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.logger import Logger  # noqa: E402
from agents.common.version import AGENT_VERSION, PROTOCOL_VERSION  # noqa: E402
from agents.linux.src.collectors import LinuxCollector  # noqa: E402

LOG = Logger(quiet=True)


class LinuxCollectorTests(unittest.TestCase):
    def test_shared_version_contract_is_available(self) -> None:
        self.assertEqual(AGENT_VERSION, "1.0.0")
        self.assertEqual(PROTOCOL_VERSION, 1)
    def test_phase8_hardware_telemetry_is_bounded_and_honest(self) -> None:
        hardware = self.collector.get_hardware_telemetry()
        self.assertEqual(set(hardware["capabilities"]), {"gpu", "gpuVram", "cpuTemperature", "gpuTemperature", "fanSpeed", "battery", "charging"})
        if "cpuTemperatureC" in hardware:
            self.assertGreaterEqual(hardware["cpuTemperatureC"], -40)
            self.assertLessEqual(hardware["cpuTemperatureC"], 200)
        if "fanRpm" in hardware: self.assertGreaterEqual(hardware["fanRpm"], 0)

    def test_phase8_network_telemetry_is_bounded(self) -> None:
        network = self.collector.get_network_telemetry()
        self.assertIsInstance(network["connected"], bool)
        self.assertLessEqual(len(network.get("dns", [])), 4)
        for key in ("downloadMbps", "uploadMbps"):
            if key in network: self.assertGreaterEqual(network[key], 0)
    def setUp(self) -> None:
        self.collector = LinuxCollector(LOG)

    # ---------------- identification ----------------
    def test_system_info_has_required_keys(self) -> None:
        info = self.collector.get_system_info()
        self.assertIn("agent_version", info)
        self.assertIn("platform", info)
        self.assertIn("os", info)
        self.assertIn("hostname", info)
        self.assertEqual(info["platform"], "linux")

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

    # ---------------- cpu ----------------
    def test_cpu_usage_is_a_percentage(self) -> None:
        value = self.collector.get_cpu_usage()
        if value is None:
            self.skipTest("cpu usage unavailable on this host")
        self.assertIsInstance(value, float)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    def test_cpu_usage_from_proc_fallback(self) -> None:
        """The /proc path must still work when psutil is missing."""
        import time

        with patch("agents.linux.src.collectors._HAS_PSUTIL", False):
            collector = LinuxCollector(LOG)
            # Two /proc/stat snapshots are needed; they must differ or the
            # delta is zero and the collector legitimately returns None.
            time.sleep(0.25)
            value = collector.get_cpu_usage()
        if value is None:
            self.skipTest("no /proc/stat on this host")
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

    def test_memory_usage_from_proc_fallback(self) -> None:
        with patch("agents.linux.src.collectors._HAS_PSUTIL", False):
            collector = LinuxCollector(LOG)
            value = collector.get_memory_usage()
        if value is None:
            self.skipTest("no /proc/meminfo on this host")
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    # ---------------- storage / battery / uptime ----------------
    def test_storage_usage_is_a_percentage(self) -> None:
        value = self.collector.get_storage_usage()
        if value is None:
            self.skipTest("storage usage unavailable")
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    def test_battery_status_is_a_percentage_or_none(self) -> None:
        value = self.collector.get_battery_status()
        if value is None:
            self.skipTest("no battery on this host")
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

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

    # ---------------- processes ----------------
    def test_processes_are_the_top_few(self) -> None:
        procs = self.collector.get_processes()
        self.assertLessEqual(len(procs), 10)
        for proc in procs:
            self.assertIn("pid", proc)
            self.assertIn("name", proc)
            self.assertIn("cpu", proc)
            self.assertIn("ram", proc)


if __name__ == "__main__":
    unittest.main()
