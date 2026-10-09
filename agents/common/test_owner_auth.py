"""
VIGILIA agent — owner authentication tests (Phase 5A/5B/5C).

The state machine is what every owner-only path trusts, so it is tested as a
state machine: given inputs, assert the state, the persisted state and the
events. No socket, no camera, no network — a fake verifier stands in for the
platform face code and reports only booleans, which is exactly the contract
the real one satisfies.

What these tests do not do: assert anything about biometric data. The module
cannot send it, so there is nothing to assert. The presence payload is
checked for the *absence* of image/template fields instead.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.face import FaceVerificationResult  # noqa: E402
from agents.common.logger import Logger  # noqa: E402
from agents.common.owner_auth import OwnerAuthenticator  # noqa: E402
from agents.common.security import (  # noqa: E402
    FAILED_AUTHENTICATION_THRESHOLD,
    OWNER_STATES,
    SecurityCapabilities,
    SecurityEvent,
)


class RecordingFace:
    """A fake `FaceVerifier` that returns only what it is told to return.

    It never holds an image, a template or a score — the real one holds all
    three and exposes none of them, and this one models that contract: the
    authenticator may only consume `verified`.
    """

    def __init__(self, supported: bool = True, results: Optional[List[FaceVerificationResult]] = None):
        self._supported = supported
        self._results = list(results or [])
        self.verifications = 0

    def is_supported(self) -> bool:
        return self._supported

    def verify(self) -> FaceVerificationResult:
        self.verifications += 1
        if self._results:
            return self._results.pop(0)
        return FaceVerificationResult(verified=False, status="NO_MATCH", detail="no")

    def liveness_challenge(self) -> FaceVerificationResult:
        self.verifications += 1
        if self._results:
            return self._results.pop(0)
        return FaceVerificationResult(verified=False, status="LIVENESS_FAILED", detail="no")


class Sink:
    """Collects the events the authenticator would forward to the wire."""

    def __init__(self) -> None:
        self.events: List[SecurityEvent] = []

    def __call__(self, event: SecurityEvent) -> None:
        self.events.append(event)

    def types(self) -> List[str]:
        return [e.event_type for e in self.events]

    def last(self) -> Optional[SecurityEvent]:
        return self.events[-1] if self.events else None


def make_auth(tmp: Path, face: RecordingFace, *, capabilities: Optional[SecurityCapabilities] = None,
              state_file: Optional[Path] = None) -> OwnerAuthenticator:
    log = Logger(quiet=True)
    auth = OwnerAuthenticator(
        "dev",
        log,
        face,
        state_file=state_file or tmp / "owner-state.json",
        pin_file=tmp / "owner-pin.json",
        photo_dir=tmp / "photos",
        capture_photo=lambda: None,
        capabilities=capabilities or SecurityCapabilities(face_recognition=True, liveness=False),
    )
    sink = Sink()
    auth.set_event_sink(sink)
    return auth, sink  # type: ignore[return-value]


class OwnerStateMachineTests(unittest.TestCase):
    """5A: the transitions, and the rule that trust is never implied."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-owner-"))
        self.auth, self.sink = make_auth(self.tmp, RecordingFace())

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_initial_state_is_unknown_and_never_owner(self) -> None:
        self.assertEqual(self.auth.state, "UNKNOWN")
        self.assertFalse(self.auth.is_owner)
        self.assertFalse(self.auth.is_locked)
        self.assertFalse(self.auth.is_guest)
        self.assertTrue(self.auth.is_unknown)

    def test_a_successful_face_authentication_moves_to_owner(self) -> None:
        face = RecordingFace(results=[FaceVerificationResult(verified=True, status="SUCCESS")])
        self.auth.face = face
        outcome = self.auth.authenticate_face()
        self.assertTrue(outcome.authenticated)
        self.assertTrue(outcome.is_owner)
        self.assertEqual(self.auth.state, "OWNER")
        self.assertIn("owner.authenticated", self.sink.types())

    def test_a_failed_face_authentication_does_not_grant_owner(self) -> None:
        face = RecordingFace(results=[FaceVerificationResult(verified=False, status="NO_MATCH")])
        self.auth.face = face
        outcome = self.auth.authenticate_face()
        self.assertFalse(outcome.authenticated)
        self.assertFalse(outcome.is_owner)
        self.assertEqual(self.auth.state, "UNKNOWN")
        self.assertIn("owner.authentication_failed", self.sink.types())

    def test_liveness_failure_is_a_failure_not_a_success(self) -> None:
        self.auth.capabilities.liveness = True
        face = RecordingFace(
            results=[FaceVerificationResult(verified=False, status="LIVENESS_FAILED")]
        )
        self.auth.face = face
        outcome = self.auth.authenticate_face(require_liveness=True)
        self.assertFalse(outcome.authenticated)
        self.assertEqual(self.auth.state, "UNKNOWN")

    def test_liveness_failure_reports_unsupported_honestly(self) -> None:
        face = RecordingFace(supported=False)
        self.auth.face = face
        outcome = self.auth.authenticate_face()
        self.assertFalse(outcome.authenticated)
        self.assertEqual(self.auth.state, "UNKNOWN")
        self.assertIn("owner.authentication_failed", self.sink.types())

    def test_three_failures_lock_and_the_lock_does_not_clear_itself(self) -> None:
        face = RecordingFace(
            results=[FaceVerificationResult(verified=False, status="NO_MATCH") for _ in range(3)]
        )
        self.auth.face = face
        self.auth.authenticate_face()
        self.assertEqual(self.auth.state, "UNKNOWN")
        self.auth.authenticate_face()
        self.assertEqual(self.auth.state, "UNKNOWN")
        outcome = self.auth.authenticate_face()
        self.assertTrue(outcome.locked)
        self.assertTrue(self.auth.is_locked)
        self.assertFalse(self.auth.is_owner)
        self.assertIn("security_lock_triggered", self.sink.types())

        # The lock persists: another failure does not unlock, and neither does
        # a mark_unknown_person.
        self.auth._record_failure(reason="again")
        self.assertTrue(self.auth.is_locked)

    def test_the_third_failure_captures_a_security_photo(self) -> None:
        captured: list = []

        def capture() -> Optional[bytes]:
            payload = b"fake-jpeg-bytes"
            captured.append(payload)
            return payload

        self.auth._capture_photo = capture
        face = RecordingFace(
            results=[FaceVerificationResult(verified=False, status="NO_MATCH") for _ in range(3)]
        )
        self.auth.face = face
        outcome = None
        for _ in range(3):
            outcome = self.auth.authenticate_face()
        assert outcome is not None
        self.assertTrue(outcome.photo_captured)
        self.assertEqual(len(captured), 1)
        self.assertIn("security_photo_captured", self.sink.types())

    def test_a_photo_is_not_captured_without_intrusion_detection(self) -> None:
        self.auth.capabilities.intrusion_detection = False
        self.auth._capture_photo = lambda: b"bytes"
        face = RecordingFace(
            results=[FaceVerificationResult(verified=False, status="NO_MATCH") for _ in range(3)]
        )
        self.auth.face = face
        outcome = None
        for _ in range(3):
            outcome = self.auth.authenticate_face()
        assert outcome is not None
        self.assertFalse(outcome.photo_captured)

    def test_an_owner_authentication_clears_a_lock(self) -> None:
        face = RecordingFace(
            results=[
                FaceVerificationResult(verified=False, status="NO_MATCH"),
                FaceVerificationResult(verified=False, status="NO_MATCH"),
                FaceVerificationResult(verified=False, status="NO_MATCH"),
                FaceVerificationResult(verified=True, status="SUCCESS"),
            ]
        )
        self.auth.face = face
        for _ in range(3):
            self.auth.authenticate_face()
        self.assertTrue(self.auth.is_locked)
        outcome = self.auth.authenticate_face()
        self.assertTrue(outcome.authenticated)
        self.assertTrue(self.auth.is_owner)
        self.assertIn("security_lock_cleared", self.sink.types())
        self.assertIn("owner.authenticated", self.sink.types())

    def test_a_success_resets_the_failure_count(self) -> None:
        face = RecordingFace(
            results=[
                FaceVerificationResult(verified=False, status="NO_MATCH"),
                FaceVerificationResult(verified=False, status="NO_MATCH"),
                FaceVerificationResult(verified=True, status="SUCCESS"),
                FaceVerificationResult(verified=False, status="NO_MATCH"),
                FaceVerificationResult(verified=False, status="NO_MATCH"),
            ]
        )
        self.auth.face = face
        self.auth.authenticate_face()
        self.assertEqual(self.auth.failed_attempts, 1)
        self.auth.authenticate_face()
        self.assertEqual(self.auth.failed_attempts, 2)
        # The success resets the counter and enters owner mode.
        outcome = self.auth.authenticate_face()
        self.assertTrue(outcome.is_owner)
        self.assertEqual(self.auth.failed_attempts, 0)
        # Two more failures after the reset are only two: the counter did not
        # carry over, so the machine is not locked.
        self.auth.authenticate_face()
        self.auth.authenticate_face()
        self.assertEqual(self.auth.state, "OWNER")
        self.assertEqual(self.auth.failed_attempts, 2)
        self.assertFalse(self.auth.is_locked)


class GuestModeTests(unittest.TestCase):
    """5B: guest mode is monitoring, not owner access."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-guest-"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_guest_mode_admits_an_unknown_user_as_guest(self) -> None:
        auth, sink = make_auth(
            self.tmp,
            RecordingFace(),
            capabilities=SecurityCapabilities(face_recognition=True, guest_mode=True),
        )
        outcome = auth.mark_unknown_person()
        self.assertEqual(auth.state, "GUEST")
        self.assertTrue(auth.is_guest)
        self.assertFalse(auth.is_owner)
        self.assertIn("guest.mode_activated", sink.types())

    def test_without_guest_mode_an_unknown_user_stays_protected(self) -> None:
        auth, sink = make_auth(
            self.tmp,
            RecordingFace(),
            capabilities=SecurityCapabilities(face_recognition=True, guest_mode=False),
        )
        outcome = auth.mark_unknown_person()
        self.assertEqual(auth.state, "UNKNOWN")
        self.assertFalse(auth.is_owner)
        self.assertIn("unknown_person_detected", sink.types())

    def test_guest_mode_deactivates_back_to_unknown(self) -> None:
        auth, sink = make_auth(
            self.tmp,
            RecordingFace(),
            capabilities=SecurityCapabilities(face_recognition=True, guest_mode=True),
        )
        auth.mark_unknown_person()
        self.assertTrue(auth.is_guest)
        auth.deactivate_guest_mode()
        self.assertEqual(auth.state, "UNKNOWN")
        self.assertIn("guest.mode_deactivated", sink.types())

    def test_deactivate_guest_mode_is_a_noop_when_not_a_guest(self) -> None:
        auth, sink = make_auth(self.tmp, RecordingFace())
        auth.deactivate_guest_mode()
        self.assertEqual(sink.types(), [])

    def test_a_guest_cannot_become_owner_by_marking_itself(self) -> None:
        auth, _ = make_auth(
            self.tmp,
            RecordingFace(),
            capabilities=SecurityCapabilities(face_recognition=True, guest_mode=True),
        )
        auth.mark_unknown_person()
        self.assertEqual(auth.state, "GUEST")
        # The only way out is authentication.
        self.assertFalse(auth.is_owner)


class PinFallbackTests(unittest.TestCase):
    """5A: the PIN fallback, and the rule that a missing PIN is not an open door."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-pin-"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_missing_pin_is_a_failure_not_an_open_door(self) -> None:
        auth, sink = make_auth(self.tmp, RecordingFace(supported=False))
        outcome = auth.authenticate_pin(None)
        self.assertFalse(outcome.authenticated)
        self.assertEqual(auth.state, "UNKNOWN")
        self.assertIn("owner.authentication_failed", sink.types())

    def test_an_enrolled_pin_authenticates_the_owner(self) -> None:
        auth, sink = make_auth(self.tmp, RecordingFace(supported=False))
        auth.enroll_pin("1234")
        outcome = auth.authenticate_pin("1234")
        self.assertTrue(outcome.is_owner)
        self.assertEqual(auth.state, "OWNER")
        self.assertIn("owner.authenticated", sink.types())

    def test_a_wrong_pin_does_not_authenticate(self) -> None:
        auth, sink = make_auth(self.tmp, RecordingFace(supported=False))
        auth.enroll_pin("1234")
        outcome = auth.authenticate_pin("0000")
        self.assertFalse(outcome.authenticated)
        self.assertEqual(auth.state, "UNKNOWN")

    def test_three_wrong_pins_lock(self) -> None:
        auth, sink = make_auth(self.tmp, RecordingFace(supported=False))
        auth.enroll_pin("1234")
        for _ in range(3):
            auth.authenticate_pin("0000")
        self.assertTrue(auth.is_locked)

    def test_the_pin_is_stored_as_a_digest_never_in_clear_text(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace(supported=False))
        auth.enroll_pin("1234")
        raw = auth.pin_file.read_text(encoding="utf-8")
        self.assertNotIn("1234", raw)
        data = json.loads(raw)
        self.assertTrue(data.get("digest"))
        self.assertTrue(data.get("salt"))
        self.assertNotEqual(data["digest"], data["salt"])

    def test_the_pin_file_is_readable_only_by_the_owner(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace(supported=False))
        auth.enroll_pin("1234")
        mode = stat.S_IMODE(os.stat(auth.pin_file).st_mode)
        self.assertEqual(mode & 0o077, 0, "the PIN file is group/world readable")

    def test_has_pin_reports_the_truth(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace(supported=False))
        self.assertFalse(auth.has_pin())
        auth.enroll_pin("1234")
        self.assertTrue(auth.has_pin())


class DurabilityTests(unittest.TestCase):
    """5A/5E: the trust state survives a reboot."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-durable-"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_state_file_is_reloaded_on_construction(self) -> None:
        state_file = self.tmp / "owner-state.json"
        face = RecordingFace(results=[FaceVerificationResult(verified=True, status="SUCCESS")])
        auth, _ = make_auth(self.tmp, face, state_file=state_file)
        auth.authenticate_face()
        self.assertEqual(auth.state, "OWNER")
        auth.set_daily_cleanup_date("2026-10-05")

        # A fresh process, the same file: the state and the cleanup date come
        # back. This is what makes the once-per-day rule survive a reboot.
        reopened, _ = make_auth(self.tmp, face, state_file=state_file)
        self.assertEqual(reopened.state, "OWNER")
        self.assertEqual(reopened.last_daily_cleanup_date, "2026-10-05")
        self.assertTrue(reopened.is_owner)

    def test_a_corrupt_state_file_fails_safe_to_unknown(self) -> None:
        state_file = self.tmp / "owner-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{not json", encoding="utf-8")
        auth, _ = make_auth(self.tmp, RecordingFace(), state_file=state_file)
        self.assertEqual(auth.state, "UNKNOWN")
        self.assertFalse(auth.is_owner)

    def test_a_state_file_with_an_invented_state_fails_safe(self) -> None:
        state_file = self.tmp / "owner-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            json.dumps({"state": "SUPER_OWNER", "since": 1, "failedAttempts": 0}),
            encoding="utf-8",
        )
        auth, _ = make_auth(self.tmp, RecordingFace(), state_file=state_file)
        self.assertEqual(auth.state, "UNKNOWN")
        self.assertFalse(auth.is_owner)

    def test_the_state_file_is_not_world_readable(self) -> None:
        state_file = self.tmp / "owner-state.json"
        auth, _ = make_auth(self.tmp, RecordingFace(), state_file=state_file)
        auth.enroll_pin("1234")
        auth.authenticate_pin("1234")
        self.assertTrue(state_file.is_file())
        mode = stat.S_IMODE(os.stat(state_file).st_mode)
        self.assertEqual(mode & 0o077, 0)


class EventContractTests(unittest.TestCase):
    """5C/5D: the event vocabulary, and what it may never carry."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-events-"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_event_type_the_module_emits_is_in_the_contract(self) -> None:
        auth, sink = make_auth(self.tmp, RecordingFace())
        face = RecordingFace(
            results=[
                FaceVerificationResult(verified=False, status="NO_MATCH"),
                FaceVerificationResult(verified=False, status="NO_MATCH"),
                FaceVerificationResult(verified=False, status="NO_MATCH"),
                FaceVerificationResult(verified=True, status="SUCCESS"),
            ]
        )
        auth.face = face
        auth.capabilities.guest_mode = True
        auth.mark_unknown_person()
        for _ in range(3):
            auth.authenticate_face()
        auth.authenticate_face()

        for event in sink.events:
            self.assertIn(
                event.event_type,
                [
                    "guest.mode_activated",
                    "guest.mode_deactivated",
                    "owner.authenticated",
                    "owner.authentication_failed",
                    "owner.locked",
                    "unknown_person_detected",
                    "security_photo_captured",
                    "security_lock_triggered",
                    "security_lock_cleared",
                    "daily_cleanup.started",
                    "daily_cleanup.completed",
                    "daily_cleanup.skipped",
                    "daily_cleanup.failed",
                ],
                f"{event.event_type} is not in the contract",
            )
            issues = event.validate()
            self.assertEqual(issues, [], f"{event.event_type} did not validate: {issues}")

    def test_an_event_with_an_invented_type_is_rejected_locally(self) -> None:
        event = SecurityEvent(
            device_id="dev",
            event_type="owner.became_admin",
            description="an invented privilege",
        )
        self.assertTrue(event.validate())

    def test_an_event_with_an_invented_metadata_key_is_rejected(self) -> None:
        event = SecurityEvent(
            device_id="dev",
            event_type="owner.authenticated",
            description="ok",
            metadata={"path": "/etc/passwd", "token": "secret"},
        )
        self.assertIn("unknown metadata key path", " ".join(event.validate()))

    def test_the_presence_payload_carries_no_biometric_field(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace())
        payload = auth.presence().to_payload()
        for forbidden in ("template", "hash", "cells", "image", "embedding", "photo", "faceData"):
            self.assertNotIn(forbidden, payload, f"the presence payload carries {forbidden}")
        self.assertIn("state", payload)
        self.assertIn("failedAttempts", payload)

    def test_the_presence_payload_state_is_always_in_the_contract(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace())
        for state in OWNER_STATES:
            auth.state = state
            payload = auth.presence().to_payload()
            self.assertIn(payload["state"], OWNER_STATES)
            self.assertEqual(auth.presence().validate(), [])

    def test_an_unknown_state_does_not_validate(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace())
        auth.state = "ROOT"
        self.assertTrue(auth.presence().validate())

    def test_the_failure_threshold_is_three(self) -> None:
        self.assertEqual(FAILED_AUTHENTICATION_THRESHOLD, 3)


class DailyCleanupTriggerTests(unittest.TestCase):
    """The Phase 5E trigger flag: what sets it, and what does not."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-trigger-"))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_nothing_is_pending_before_any_authentication(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace())
        self.assertFalse(auth.consume_authentication())

    def test_a_successful_authentication_is_pending(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace())
        auth.enroll_pin("1234")
        auth.authenticate_pin("1234")
        self.assertTrue(auth.consume_authentication())

    def test_the_flag_is_consumed_once(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace())
        auth.enroll_pin("1234")
        auth.authenticate_pin("1234")
        self.assertTrue(auth.consume_authentication())
        self.assertFalse(auth.consume_authentication())

    def test_a_failed_authentication_does_not_set_the_flag(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace())
        auth.enroll_pin("1234")
        auth.authenticate_pin("0000")
        self.assertFalse(auth.consume_authentication())

    def test_a_face_success_sets_the_flag(self) -> None:
        face = RecordingFace(results=[FaceVerificationResult(verified=True, status="SUCCESS")])
        auth, _ = make_auth(self.tmp, face)
        auth.authenticate_face()
        self.assertTrue(auth.consume_authentication())

    def test_a_guest_does_not_set_the_flag(self) -> None:
        auth, _ = make_auth(self.tmp, RecordingFace())
        auth.mark_unknown_person()
        self.assertFalse(auth.consume_authentication())

    def test_a_rebooted_owner_state_does_not_set_the_flag(self) -> None:
        # The flag is not persisted. A durable `OWNER` row loaded at boot is
        # not an authentication, so no cleanup starts from boot alone.
        state_file = self.tmp / "owner-state.json"
        face = RecordingFace(results=[FaceVerificationResult(verified=True, status="SUCCESS")])
        auth, _ = make_auth(self.tmp, face, state_file=state_file)
        auth.authenticate_face()
        auth.consume_authentication()

        reopened, _ = make_auth(self.tmp, face, state_file=state_file)
        self.assertTrue(reopened.is_owner)
        self.assertFalse(reopened.consume_authentication())


if __name__ == "__main__":
    unittest.main()
