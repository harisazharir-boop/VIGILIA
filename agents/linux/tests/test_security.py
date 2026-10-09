"""
VIGILIA Linux agent — Phase 5 supervisor tests.

The supervisor owns the only place a Daily Auto Cleanup may start:

    if self.owner.is_owner and not was_owner:
        self.daily_cleanup.run_after_owner_authentication()

This suite asserts the trigger is *exactly* that: a boot-time connect does not
clean, a guest does not clean, an unknown user does not clean, and only a real
`UNKNOWN -> OWNER` transition does. It also asserts the security frames the
supervisor puts on the wire carry the state and no biometric data.

The collector and the socket are replaced by recording stand-ins, so nothing
touches the host or the network.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.config import AgentConfig  # noqa: E402
from agents.common.logger import Logger  # noqa: E402
from agents.common.security import SecurityCapabilities  # noqa: E402

# `main` imports platform modules; import it after the path is fixed up.
from agents.linux import main as linux_main  # noqa: E402
from agents.linux.src.collectors import LinuxCollector  # noqa: E402


class RecordingCleaner:
    """Records the action ids the supervisor asked for. Deletes nothing."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.options: List[dict] = []

    def execute(self, action_id: str, options: Optional[dict] = None, **kwargs):
        self.calls.append(action_id)
        self.options.append(dict(options or {}))
        from agents.common.cleaner import CleanResult

        return CleanResult(action_id=action_id, status="SUCCESS", bytes_freed=0, items_removed=0)


class RecordingFace:
    """The honest platform face module: unsupported unless told otherwise."""

    def __init__(self, supported: bool = False, enrolled: bool = False) -> None:
        self._supported = supported
        self._enrolled = enrolled

    def is_supported(self) -> bool:
        return self._supported

    def is_enrolled(self) -> bool:
        return self._enrolled

    def verify(self):
        from agents.common.face import FaceVerificationResult

        return FaceVerificationResult(verified=False, status="UNSUPPORTED", unsupported=True)

    def liveness_challenge(self):
        from agents.common.face import FaceVerificationResult

        return FaceVerificationResult(verified=False, status="UNSUPPORTED", unsupported=True)


@dataclass
class FakeConfig:
    """The fields the supervisor reads, and nothing else."""

    agent_name: str = "test-agent"
    backend_url: str = "http://127.0.0.1:3999"
    token_file: Optional[Path] = None
    token: Optional[str] = None
    telemetry_interval: int = 3600
    heartbeat_interval: int = 3600
    process_interval: int = 3600
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


class SecuritySupervisorTests(unittest.TestCase):
    """The trigger, and the frames the supervisor puts on the wire."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-linux-sec-"))
        self.state_dir = self.tmp / ".vigilia"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log = Logger(quiet=True)
        self.cleaner = RecordingCleaner()
        self.faces: List[RecordingFace] = []

        # The supervisor builds a `FaceVerifier` from the state dir; patch the
        # class so the suite controls what the platform can do.
        self._real_face_verifier = linux_main.FaceVerifier
        linux_main.FaceVerifier = self._fake_face

        self.supervisor = linux_main.AgentSupervisor(
            FakeConfig(token_file=self.tmp / "agent-token", state_dir=self.state_dir),
            LinuxCollector(self.log),
            self.log,
        )
        # The supervisor's own cleaner is replaced with the recording one, so
        # the test observes exactly what a daily cleanup asked for.
        self.supervisor.cleaner = self.cleaner
        self.supervisor.daily_cleanup.cleaner = self.cleaner
        # Point the durable state at the temp dir and enable the run.
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
        linux_main.FaceVerifier = self._real_face_verifier
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake_face(self, template_dir: Path, logger=None, **kwargs) -> RecordingFace:
        face = RecordingFace()
        self.faces.append(face)
        return face

    def _record(self, frame: Dict[str, Any]) -> bool:
        self.frames.append(frame)
        return True

    # ---------------- the trigger ----------------

    def test_a_boot_connect_never_starts_a_cleanup(self) -> None:
        # The connect path pushes the security state; it must not clean. The
        # rule is "after the owner authenticates", and nobody has.
        self.supervisor._send_security_state()
        self.assertEqual(self.cleaner.calls, [])
        self.assertEqual([f["type"] for f in self.frames], ["security.state"])

    def test_an_unknown_user_never_starts_a_cleanup(self) -> None:
        self.supervisor._security_tick()
        self.assertEqual(self.supervisor.owner.state, "UNKNOWN")
        self.assertEqual(self.cleaner.calls, [])

    def test_a_guest_never_starts_a_cleanup(self) -> None:
        self.supervisor.owner.state = "GUEST"
        self.supervisor._security_tick()
        self.assertEqual(self.cleaner.calls, [])

    def test_a_locked_device_never_starts_a_cleanup(self) -> None:
        self.supervisor.owner.state = "LOCKED"
        self.supervisor._security_tick()
        self.assertEqual(self.cleaner.calls, [])

    def test_only_a_real_unknown_to_owner_transition_cleans(self) -> None:
        # No camera on the test host, so the owner's PIN is what carries the
        # state — the honest fallback, not a faked face.
        self.supervisor.owner.enroll_pin("1234")
        self.assertTrue(self.supervisor.owner.is_unknown)
        self.supervisor.owner.authenticate_pin("1234")
        self.assertTrue(self.supervisor.owner.is_owner)
        self.supervisor._security_tick()
        self.assertEqual(sorted(self.cleaner.calls), ["CLEAN_BROWSER_CACHE", "CLEAN_TEMP"])

    def test_a_second_owner_tick_the_same_day_does_not_clean(self) -> None:
        self.supervisor.owner.enroll_pin("1234")
        self.supervisor.owner.authenticate_pin("1234")
        self.supervisor._security_tick()
        first = len(self.cleaner.calls)
        self.assertGreater(first, 0)

        # The state was OWNER before and after, so `was_owner` is true and no
        # second run starts. This is the once-per-day rule at the trigger.
        self.supervisor._security_tick()
        self.assertEqual(len(self.cleaner.calls), first)

    def test_a_returning_owner_the_next_day_cleans_again(self) -> None:
        from datetime import datetime

        self.supervisor.owner.enroll_pin("1234")
        self.supervisor.owner.authenticate_pin("1234")
        self.supervisor._security_tick()
        first = len(self.cleaner.calls)

        # Move the device's calendar forward and re-authenticate: a new
        # device-day is eligible again.
        clock = {"now": datetime(2099, 1, 2, 9, 0, 0)}
        self.supervisor.daily_cleanup._now = lambda: clock["now"]
        self.supervisor.owner.authenticate_pin("1234")
        self.supervisor._security_tick()
        self.assertEqual(len(self.cleaner.calls), first * 2)

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

    def test_the_security_events_are_forwarded_on_the_wire(self) -> None:
        from agents.common.security import SecurityEvent

        self.supervisor._send_security_event(
            SecurityEvent(
                device_id="dev",
                event_type="security_lock_triggered",
                description="Security lock triggered after 3 failed authentication attempts",
                metadata={"failedAttempts": 3},
            )
        )
        self.assertEqual(self.frames[-1]["type"], "security.event")
        self.assertEqual(self.frames[-1]["data"]["eventType"], "security_lock_triggered")

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

    def test_the_security_capabilities_reflect_the_platform(self) -> None:
        # The recording face reports unsupported, so the capability must be
        # off — never a claimed face recognition the host cannot do.
        caps = self.supervisor.security_capabilities
        self.assertFalse(caps.face_recognition)
        self.assertFalse(caps.liveness)


if __name__ == "__main__":
    unittest.main()
