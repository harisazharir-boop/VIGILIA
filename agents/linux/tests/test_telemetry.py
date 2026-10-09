"""
VIGILIA agent — telemetry contract tests.

The Python telemetry shape must stay in sync with the TypeScript contract in
shared/src/types.ts and shared/src/schemas.ts; these tests pin the wire format.

Run:  python3 -m unittest discover -s agents/linux -p 'test_*.py' -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.telemetry import Heartbeat, Telemetry  # noqa: E402


class TelemetryPayloadTests(unittest.TestCase):
    def test_omit_unset_fields(self) -> None:
        telemetry = Telemetry(device_id="dev1")
        payload = telemetry.to_payload()
        self.assertEqual(set(payload.keys()), {"deviceId", "timestamp"})

    def test_include_set_scalar_fields(self) -> None:
        telemetry = Telemetry(
            device_id="dev1",
            cpu=12.5,
            ram=80.0,
            storage=66.6,
            battery=95.0,
            os="Ubuntu 24.04.1 LTS",
            hostname="laptop",
            uptime=3600,
            gpu=5.0,
            temp=52.5,
        )
        payload = telemetry.to_payload()
        self.assertEqual(payload["cpu"], 12.5)
        self.assertEqual(payload["ram"], 80.0)
        self.assertEqual(payload["storage"], 66.6)
        self.assertEqual(payload["battery"], 95.0)
        self.assertEqual(payload["os"], "Ubuntu 24.04.1 LTS")
        self.assertEqual(payload["hostname"], "laptop")
        self.assertEqual(payload["uptime"], 3600)
        self.assertEqual(payload["gpu"], 5.0)
        self.assertEqual(payload["temp"], 52.5)

    def test_network_is_converted_to_camelcase(self) -> None:
        telemetry = Telemetry(
            device_id="dev1",
            network={"connected": True, "download_mbps": 120.5, "upload_mbps": 40.0, "interface": "wlan0"},
        )
        payload = telemetry.to_payload()
        self.assertEqual(
            payload["network"],
            {
                "connected": True,
                "downloadMbps": 120.5,
                "uploadMbps": 40.0,
                "interface": "wlan0",
            },
        )

    def test_network_omits_null_fields(self) -> None:
        """Null keys are dropped: the backend's zod schema marks these
        optional, and `optional` rejects an explicit null."""
        telemetry = Telemetry(
            device_id="dev1",
            network={"connected": True, "interface": "wlan0"},
        )
        payload = telemetry.to_payload()
        self.assertEqual(payload["network"], {"connected": True, "interface": "wlan0"})

    def test_timestamp_is_iso8601_utc(self) -> None:
        telemetry = Telemetry(device_id="dev1").with_timestamp()
        self.assertTrue(telemetry.timestamp.endswith("Z"))
        self.assertNotIn("+00:00", telemetry.timestamp)
        # 2026-09-28T12:00:00.000Z
        self.assertRegex(telemetry.timestamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_with_timestamp_copies_every_field(self) -> None:
        telemetry = Telemetry(device_id="dev1", cpu=10.0, ram=20.0, network={"connected": True})
        stamped = telemetry.with_timestamp()
        self.assertEqual(stamped.cpu, 10.0)
        self.assertEqual(stamped.ram, 20.0)
        self.assertEqual(stamped.network, {"connected": True})
        self.assertTrue(stamped.timestamp)

    def test_to_payload_always_carries_a_timestamp(self) -> None:
        payload = Telemetry(device_id="dev1").to_payload()
        self.assertIn("timestamp", payload)
        self.assertTrue(payload["timestamp"])


class HeartbeatPayloadTests(unittest.TestCase):
    def test_default_status_is_online(self) -> None:
        heartbeat = Heartbeat(device_id="dev1")
        self.assertEqual(heartbeat.status, "online")

    def test_payload_shape(self) -> None:
        heartbeat = Heartbeat(device_id="dev1", status="standby")
        payload = heartbeat.to_payload()
        self.assertEqual(payload, {
            "deviceId": "dev1",
            "status": "standby",
            "timestamp": payload["timestamp"],
        })
        self.assertTrue(payload["timestamp"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()
