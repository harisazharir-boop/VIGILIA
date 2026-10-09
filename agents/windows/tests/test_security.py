"""
VIGILIA Windows agent — Phase 5 supervisor tests.

Windows has no camera capture in the stdlib agent, so the honest platform
answer is "face recognition unavailable" and the owner authenticates with a
PIN. This suite asserts that honesty and, mainly, the Phase 5E trigger:

    * the connect path does not clean
    * an unknown user, a guest and a lock do not clean
    * only a real authentication that concluded OWNER starts a cleanup
    * the second evaluation the same session does not start a second run
    * the frames carry the state and never biometric data

Nothing touches the host or the network: the collector is the real Windows
one (its collectors report `UNAVAILABLE` off-Windows) and the socket is a
recording stand-in.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.config import AgentConfig  # noqa: E402
from agents.common.logger import Logger  # noqa: E402
from agents.common.security import SecurityCapabilities  # noqa: E402
from agents.windows import main as windows_main  # noqa: E402
from agents.windows.src.collectors import WindowsCollector  # noqa: E402


class RecordingCleaner:
    """Records the action ids the supervisor asked for. Deletes nothing."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def execute(self, action_id: str, options: Optional[dict] = None, **kwargs):
        self.calls.append(action_id)
        from agents.common.cleaner import CleanResult

        return CleanResult(action_id=action_id, status="SUCCESS", bytes_freed=0, items_removed=0)


class RecordingFace:
    """Windows reports no camera, so `is_supported()` is false here too."""

    def is_supported(self) -> bool:
        return False

    def is_enrolled(self) -> bool:
        return False

    def verify(self):
        from agents.common.face import FaceVerificationResult

        return FaceVerificationResult(verified=False, status="UNSUPPORTED", unsupported=True)

    def liveness_challenge(self):
        from agents.common.face import FaceVerificationResult

        return FaceVerificationResult(verified=False, status="UNSUPPORTED", unsupported=True)


@dataclass
class FakeConfig:
    agent_name: str = "VIGILIA Windows Test"
    backend_url: str = "http://127.0.0.1:3999"
    token_file: Optional[Path] = None
    token: Optional[str] = None
    telemetry_interval: int = 3600
    heartbeat_interval: int = 3600
    process_interval: int = 3600
    process_limit: int = 32
    process_include_command_line: bool = False
    application_interval: int = 3600
    storage_interval: int = 3600
    security_interval: int = 3600
    quiet: bool = True
    state_dir: Path = Path("/nonexistent-state-dir")

    @property
    def verify_url(self) -> str:
        return self.backend_url.rstrip("/") + "/api/auth/verify"

    @property
    def register_url(self) -> str:
        return self.backend_url.rstrip("/") + "/api/devices/register"


class WindowsSecuritySupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-win-sec-"))
        self.state_dir = self.tmp / ".vigilia"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log = Logger(quiet=True)
        self.cleaner = RecordingCleaner()

        self._real_face_verifier = windows_main.FaceVerifier
        windows_main.FaceVerifier = lambda *a, **kw: RecordingFace()

        self.supervisor = windows_main.AgentSupervisor(
            FakeConfig(token_file=self.tmp / "agent-token", state_dir=self.state_dir),
            WindowsCollector(self.log),
            self.log,
        )
        self.supervisor.cleaner = self.cleaner
        self.supervisor.daily_cleanup.cleaner = self.cleaner
        self.supervisor.daily_cleanup.state_file = self.state_dir / "daily-cleanup.json"
        self.supervisor.owner.capabilities = SecurityCapabilities(
            face_recognition=False,
            liveness=False,
            guest_mode=True,
            intrusion_detection=True,
            daily_cleanup=True,
        )
        self.supervisor.security_capabilities = self.supervisor.owner.capabilities
        self.supervisor.daily_cleanup.set_config(
            {
                "enabled": True,
                "trigger": "AFTER_OWNER_LOGIN",
                "categories": ["CLEAN_TEMP", "CLEAN_BROWSER_CACHE"],
                "trashEnabled": False,
                "logRetentionDays": 30,
            }
        )
        self.frames: List[Dict[str, Any]] = []
        self.supervisor._send = self._record

    def tearDown(self) -> None:
        windows_main.FaceVerifier = self._real_face_verifier
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self, frame: Dict[str, Any]) -> bool:
        self.frames.append(frame)
        return True

    # ---------------- the honest platform answer ----------------

    def test_windows_reports_face_recognition_unavailable(self) -> None:
        self.assertFalse(self.supervisor.security_capabilities.face_recognition)
        self.assertFalse(self.supervisor.security_capabilities.liveness)

    # ---------------- the trigger ----------------

    def test_a_boot_connect_never_starts_a_cleanup(self) -> None:
        self.supervisor._send_security_state()
        self.assertEqual(self.cleaner.calls, [])
        self.assertEqual([f["type"] for f in self.frames], ["security.state"])

    def test_an_unknown_user_never_starts_a_cleanup(self) -> None:
        self.supervisor._security_tick()
        self.assertFalse(self.supervisor.owner.is_owner)
        self.assertEqual(self.cleaner.calls, [])

    def test_a_guest_never_starts_a_cleanup(self) -> None:
        self.supervisor.owner.state = "GUEST"
        self.supervisor._security_tick()
        self.assertEqual(self.cleaner.calls, [])

    def test_a_locked_device_never_starts_a_cleanup(self) -> None:
        self.supervisor.owner.state = "LOCKED"
        self.supervisor._security_tick()
        self.assertEqual(self.cleaner.calls, [])

    def test_a_state_that_was_owner_before_the_agent_started_does_not_clean(self) -> None:
        # A durable `OWNER` row loaded at boot is not an authentication. The
        # flag is not set, so no cleanup starts.
        self.supervisor.owner.state = "OWNER"
        self.supervisor._security_tick()
        self.assertEqual(self.cleaner.calls, [])

    def test_only_a_real_pin_authentication_starts_a_cleanup(self) -> None:
        self.supervisor.owner.enroll_pin("1234")
        self.assertTrue(self.supervisor.owner.is_unknown)
        self.supervisor.owner.authenticate_pin("1234")
        self.assertTrue(self.supervisor.owner.is_owner)
        self.supervisor._security_tick()
        self.assertEqual(sorted(self.cleaner.calls), ["CLEAN_BROWSER_CACHE", "CLEAN_TEMP"])

    def test_a_second_tick_the_same_session_does_not_clean(self) -> None:
        self.supervisor.owner.enroll_pin("1234")
        self.supervisor.owner.authenticate_pin("1234")
        self.supervisor._security_tick()
        first = len(self.cleaner.calls)
        self.assertGreater(first, 0)
        self.supervisor._security_tick()
        self.assertEqual(len(self.cleaner.calls), first)

    def test_a_disabled_config_does_not_clean_even_for_the_owner(self) -> None:
        self.supervisor.owner.enroll_pin("1234")
        self.supervisor.owner.authenticate_pin("1234")
        self.supervisor.daily_cleanup.set_config(
            {**self.supervisor.daily_cleanup.config(), "enabled": False}
        )
        self.supervisor._security_tick()
        self.assertEqual(self.cleaner.calls, [])

    # ---------------- the frames ----------------

    def test_the_security_state_frame_carries_no_biometric_data(self) -> None:
        self.supervisor._send_security_state()
        payload = json.dumps(self.frames[-1])
        for forbidden in ("template", "hash", "cells", "image", "embedding", "photo", "faceData"):
            self.assertNotIn(forbidden, payload, f"the frame carries {forbidden}")
        self.assertIn("state", self.frames[-1]["data"])

    def test_the_security_state_frame_carries_the_honest_capabilities(self) -> None:
        self.supervisor._send_security_state()
        caps = self.frames[-1]["data"]["capabilities"]
        self.assertFalse(caps["faceRecognition"])
        self.assertFalse(caps["liveness"])

    def test_an_invalid_security_event_is_not_sent(self) -> None:
        from agents.common.security import SecurityEvent

        self.supervisor._send_security_event(
            SecurityEvent(
                device_id="dev",
                event_type="owner.became_admin",
                description="an invented privilege",
            )
        )
        self.assertEqual([f for f in self.frames if f["type"] == "security.event"], [])


if __name__ == "__main__":
    unittest.main()
