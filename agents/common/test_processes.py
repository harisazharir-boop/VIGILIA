"""
VIGILIA — process contract tests (Python mirror of shared/src/schemas.ts).

Pins the wire shape of the `processes` frame: camelCase keys, omitted-when-
absent optional fields, status normalisation from both platform alphabets, and
the cap that keeps a snapshot from flooding a mobile socket.

Run:  python3 -m unittest discover -s agents/common -p 'test_*.py' -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.processes import (  # noqa: E402
    DEFAULT_PROCESS_LIMIT,
    PROCESS_HARD_CAP,
    PROCESS_STATUSES,
    ProcessInfo,
    ProcessSnapshot,
    build_snapshot,
    normalize_status,
)


class ProcessInfoPayloadTests(unittest.TestCase):
    def test_required_fields_always_present(self) -> None:
        payload = ProcessInfo(pid=1, name="firefox").to_payload()
        self.assertEqual(payload["pid"], 1)
        self.assertEqual(payload["name"], "firefox")
        self.assertEqual(payload["cpu"], 0.0)

    def test_optional_fields_are_omitted_when_absent(self) -> None:
        payload = ProcessInfo(pid=1, name="x", cpu=5.0).to_payload()
        self.assertEqual(
            set(payload.keys()),
            {"pid", "name", "cpu"},
            "unset optional fields must not appear on the wire",
        )

    def test_every_optional_field_is_emitted_when_set(self) -> None:
        proc = ProcessInfo(
            pid=42,
            name="code",
            cpu=12.5,
            memory_mb=1024.0,
            memory_percent=6.25,
            threads=11,
            parent_pid=1,
            status="running",
            executable_path="/usr/bin/code",
            username="anyeong",
            started_at="2026-09-29T00:00:00Z",
            command_line="/usr/bin/code --foo",
        )
        payload = proc.to_payload()
        self.assertEqual(payload["memoryMb"], 1024.0)
        self.assertEqual(payload["memoryPercent"], 6.2)
        self.assertEqual(payload["threads"], 11)
        self.assertEqual(payload["parentPid"], 1)
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["executablePath"], "/usr/bin/code")
        self.assertEqual(payload["username"], "anyeong")
        self.assertEqual(payload["startedAt"], "2026-09-29T00:00:00Z")
        self.assertEqual(payload["commandLine"], "/usr/bin/code --foo")

    def test_round_trips_through_from_payload(self) -> None:
        original = ProcessInfo(pid=7, name="app", cpu=3.0, memory_mb=64.0, status="sleeping")
        restored = ProcessInfo.from_payload(original.to_payload())
        self.assertEqual(restored.pid, 7)
        self.assertEqual(restored.name, "app")
        self.assertEqual(restored.cpu, 3.0)
        self.assertEqual(restored.memory_mb, 64.0)
        self.assertEqual(restored.status, "sleeping")

    def test_cpu_is_rounded_to_one_decimal(self) -> None:
        self.assertEqual(ProcessInfo(pid=1, name="x", cpu=12.456).to_payload()["cpu"], 12.5)

    def test_validation_catches_out_of_range_fields(self) -> None:
        self.assertTrue(ProcessInfo(pid=1, name="x", cpu=150.0).validate())
        self.assertTrue(ProcessInfo(pid=1, name="x", cpu=-1.0).validate())
        self.assertTrue(ProcessInfo(pid=1, name="x", memory_percent=150.0).validate())
        self.assertTrue(ProcessInfo(pid=1, name="x", status="bogus").validate())
        self.assertTrue(ProcessInfo(pid=1, name="").validate(), "an empty name is invalid")
        self.assertFalse(ProcessInfo(pid=1, name="ok", cpu=1.0).validate(),
                         "a well-formed entry has no issues")


class NormalizeStatusTests(unittest.TestCase):
    def test_linux_state_letters(self) -> None:
        self.assertEqual(normalize_status("R"), "running")
        self.assertEqual(normalize_status("S"), "sleeping")
        self.assertEqual(normalize_status("D"), "sleeping")
        self.assertEqual(normalize_status("Z"), "zombie")
        self.assertEqual(normalize_status("T"), "stopped")
        self.assertEqual(normalize_status("t"), "stopped")
        self.assertEqual(normalize_status("I"), "idle")

    def test_case_does_not_matter(self) -> None:
        self.assertEqual(normalize_status("r"), "running")
        self.assertEqual(normalize_status("z"), "zombie")

    def test_windows_state_names(self) -> None:
        self.assertEqual(normalize_status("Running"), "running")
        self.assertEqual(normalize_status("Suspended"), "sleeping")
        self.assertEqual(normalize_status("Terminated"), "stopped")
        self.assertEqual(normalize_status("Wait"), "sleeping")

    def test_psutil_state_names(self) -> None:
        """psutil spells states the same way on both platforms."""
        self.assertEqual(normalize_status("running"), "running")
        self.assertEqual(normalize_status("sleeping"), "sleeping")
        self.assertEqual(normalize_status("disk-sleep"), "sleeping")
        self.assertEqual(normalize_status("idle"), "idle")
        self.assertEqual(normalize_status("stopped"), "stopped")
        self.assertEqual(normalize_status("traced"), "stopped")
        self.assertEqual(normalize_status("zombie"), "zombie")
        self.assertEqual(normalize_status("dead"), "stopped")

    def test_unknown_states_map_to_unknown_not_none(self) -> None:
        self.assertEqual(normalize_status("Q"), "unknown")
        self.assertEqual(normalize_status("Paging"), "unknown")

    def test_empty_is_omitted(self) -> None:
        self.assertIsNone(normalize_status(None))
        self.assertIsNone(normalize_status(""))
        self.assertIsNone(normalize_status("   "))


class ProcessSnapshotTests(unittest.TestCase):
    def _entries(self, count: int) -> list[ProcessInfo]:
        return [ProcessInfo(pid=i, name=f"app{i}", cpu=float(i)) for i in range(count)]

    def test_payload_shape(self) -> None:
        snapshot = ProcessSnapshot(
            device_id="dev",
            processes=[ProcessInfo(pid=1, name="a", cpu=1.0)],
            total_count=1,
            limit=64,
            timestamp="2026-09-29T00:00:00Z",
        )
        payload = snapshot.to_payload()
        self.assertEqual(payload["deviceId"], "dev")
        self.assertEqual(payload["totalCount"], 1)
        self.assertEqual(payload["limit"], 64)
        self.assertEqual(payload["timestamp"], "2026-09-29T00:00:00Z")
        self.assertIsInstance(payload["processes"], list)
        self.assertEqual(len(payload["processes"]), 1)

    def test_with_timestamp_stamps_in_utc(self) -> None:
        snapshot = build_snapshot("dev", self._entries(3)).with_timestamp()
        self.assertTrue(snapshot.timestamp.endswith("Z"))
        self.assertLess(len(snapshot.timestamp), 32)

    def test_build_snapshot_orders_by_resource_use(self) -> None:
        entries = [
            ProcessInfo(pid=1, name="idle", cpu=0.0, memory_percent=0.1),
            ProcessInfo(pid=2, name="hog", cpu=90.0, memory_percent=80.0),
            ProcessInfo(pid=3, name="mid", cpu=40.0, memory_percent=1.0),
        ]
        snapshot = build_snapshot("dev", entries, limit=10)
        self.assertEqual([proc.pid for proc in snapshot.processes], [2, 3, 1])

    def test_build_snapshot_caps_and_records_the_true_total(self) -> None:
        snapshot = build_snapshot("dev", self._entries(100), limit=10)
        self.assertEqual(len(snapshot.processes), 10)
        self.assertEqual(snapshot.total_count, 100)
        self.assertEqual(snapshot.limit, 10)

    def test_build_snapshot_clamps_the_limit_to_the_hard_cap(self) -> None:
        snapshot = build_snapshot("dev", self._entries(10), limit=99999)
        self.assertEqual(snapshot.limit, PROCESS_HARD_CAP)

    def test_build_snapshot_clamps_a_nonpositive_limit(self) -> None:
        snapshot = build_snapshot("dev", self._entries(5), limit=0)
        self.assertEqual(snapshot.limit, 1)
        self.assertEqual(len(snapshot.processes), 1)

    def test_validation_rejects_an_oversized_snapshot(self) -> None:
        # An untruncated list above the hard cap must not validate, so the
        # agent drops the frame locally instead of the backend rejecting it.
        snapshot = ProcessSnapshot(
            device_id="dev",
            processes=[ProcessInfo(pid=i, name=f"p{i}", cpu=0.0) for i in range(PROCESS_HARD_CAP + 1)],
            total_count=PROCESS_HARD_CAP + 1,
            limit=PROCESS_HARD_CAP + 1,
        )
        issues = snapshot.validate()
        self.assertTrue(any("more than" in issue for issue in issues),
                        "a frame above the hard cap must not validate")

    def test_validation_rejects_an_empty_device_id(self) -> None:
        snapshot = build_snapshot("", self._entries(1), limit=10)
        self.assertTrue(snapshot.validate())

    def test_validation_accepts_the_default_limit(self) -> None:
        snapshot = build_snapshot("dev", self._entries(2), limit=DEFAULT_PROCESS_LIMIT).with_timestamp()
        self.assertEqual(snapshot.validate(), [])


if __name__ == "__main__":
    unittest.main()
