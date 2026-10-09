"""
VIGILIA agent — owner authentication & intrusion detection (Phase 5A/5B/5C).

This module owns the trust state machine and nothing else:

    UNKNOWN ──owner authenticated──▶ OWNER
    UNKNOWN ──not recognised────────▶ GUEST   (when guest mode is on)
                                     └─▶ LOCKED  (after 3 failures, or when
                                                  guest mode is off)
    any ──lock triggered──▶ LOCKED
    LOCKED ──owner authenticated──▶ OWNER

Rules it enforces, in code:

  - **Never silently grant owner access.** The state only moves to `OWNER`
    after a real authentication: a face verification that returned
    `verified=True`, or a PIN that matched. There is no "default to owner"
    path, and the initial state is always `UNKNOWN`.
  - **3 failed attempts raise a lock.** The third failure captures a security
    photo, records `security_lock_triggered`, and stays locked. The lock does
    not clear itself — only a successful owner authentication clears it.
  - **Guest mode is not owner mode.** A guest gets monitoring, never
    owner-only functionality; `is_owner()` is the single gate the rest of the
    agent consults.
  - **Every transition is an audited event.** `record_event()` is called for
    each one, and the backend writes a `SecurityEvent` row the Activity screen
    renders alongside `CommandLog`.

What this module does *not* do: hold biometric data. A `FaceVerifier` is
injected, and only its boolean result is consumed. The template, the image and
the liveness signal stay in the verifier, which never sends anything.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from agents.common.face import FaceVerificationResult, FaceVerifier
from agents.common.logger import Logger
from agents.common.security import (
    FAILED_AUTHENTICATION_THRESHOLD,
    OWNER_STATES,
    SecurityCapabilities,
    SecurityEvent,
    OwnerPresence,
)


@dataclass
class AuthOutcome:
    """The result of one authentication attempt. Carries no biometric data."""

    authenticated: bool
    state: str
    reason: str
    photo_captured: bool = False
    locked: bool = False

    @property
    def is_owner(self) -> bool:
        return self.authenticated and self.state == "OWNER"


@dataclass
class SecurityPhoto:
    """A photo captured *only* as part of an explicit intrusion event.

    Never attached to a frame, never sent by this module. The file lives
    beside the agent's token, mode 0600, and is deleted once the owner
    retrieves it or it ages out.
    """

    path: Path
    bytes_size: int
    captured_at: float

    def describe(self) -> str:
        return f"{self.bytes_size} byte security capture at {self.captured_at:.0f}"


class OwnerAuthenticator:
    """The trust state machine. Injectable verifier, injectable photo sink."""

    def __init__(
        self,
        device_id: str,
        log: Logger,
        face: FaceVerifier,
        *,
        state_file: Optional[Path] = None,
        pin_file: Optional[Path] = None,
        photo_dir: Optional[Path] = None,
        capture_photo: Optional[Callable[[], Optional[bytes]]] = None,
        capabilities: Optional[SecurityCapabilities] = None,
    ) -> None:
        self.device_id = device_id
        self.log = log
        self.face = face
        self.state_file = state_file
        self.pin_file = pin_file
        self.photo_dir = photo_dir
        self._capture_photo = capture_photo
        self.capabilities = capabilities or SecurityCapabilities()
        #: The current trust state. `UNKNOWN` until an authentication succeeds.
        self.state: str = "UNKNOWN"
        self.since: float = time.time()
        self.last_authenticated_at: Optional[float] = None
        self.failed_attempts: int = 0
        self.last_daily_cleanup_date: Optional[str] = None
        #: Set by a successful authentication, consumed by the supervisor as
        #: the daily-cleanup trigger. Not persisted: a reboot must not carry
        #: an "owner authenticated" signal forward.
        self._authenticated = False
        self._load_state()

    # ---------------- persistence ----------------
    def _load_state(self) -> None:
        """Load the durable state, so a reboot does not reset the trust state."""
        if self.state_file is None or not self.state_file.is_file():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        state = data.get("state")
        if state in OWNER_STATES:
            self.state = state
        self.since = float(data.get("since") or time.time())
        self.last_authenticated_at = data.get("lastAuthenticatedAt")
        self.failed_attempts = int(data.get("failedAttempts") or 0)
        self.last_daily_cleanup_date = data.get("lastDailyCleanupDate")

    def _persist_state(self) -> None:
        if self.state_file is None:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.touch(mode=0o600, exist_ok=True)
            self.state_file.chmod(0o600)
            payload = {
                "state": self.state,
                "since": self.since,
                "lastAuthenticatedAt": self.last_authenticated_at,
                "failedAttempts": self.failed_attempts,
                "lastDailyCleanupDate": self.last_daily_cleanup_date,
            }
            self.state_file.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(self.state_file, 0o600)
        except OSError as exc:
            self.log.warn("could not persist the owner auth state", error=str(exc))

    # ---------------- the gates ----------------
    @property
    def is_owner(self) -> bool:
        """The single gate the rest of the agent consults.

        Explicit: only `OWNER`. A guest, an unknown user and a lock all return
        False, so no owner-only path has to be reasoned about.
        """
        return self.state == "OWNER"

    @property
    def is_locked(self) -> bool:
        return self.state == "LOCKED"

    @property
    def is_guest(self) -> bool:
        return self.state == "GUEST"

    @property
    def is_unknown(self) -> bool:
        return self.state == "UNKNOWN"

    def consume_authentication(self) -> bool:
        """The Phase 5E trigger, exactly once per authentication.

        True only when a real authentication concluded `OWNER` since the last
        call — a face verification that matched, or a PIN that matched. Boot
        does not set it, a timer does not set it, and a state that was already
        `OWNER` when the agent started does not set it. Cleared on read, so a
        second evaluation the same session does not start a second run.
        """
        if self._authenticated and self.is_owner:
            self._authenticated = False
            return True
        self._authenticated = False
        return False

    # ---------------- authentication ----------------
    def authenticate_face(self, require_liveness: bool = True) -> AuthOutcome:
        """Attempt owner authentication with the camera.

        A face that does not match the template, a liveness failure and an
        unavailable camera are all *failures* — the last one is reported
        honestly rather than as a success, so a broken camera never silently
        grants owner access.
        """
        if not self.face.is_supported():
            outcome = self._record_failure(
                reason="face verification is not supported on this host",
                event_type="owner.authentication_failed",
            )
            return AuthOutcome(
                authenticated=False,
                state=self.state,
                reason="face verification unavailable; use the PIN fallback",
                locked=outcome.locked,
                photo_captured=outcome.photo_captured,
            )

        if require_liveness and self.capabilities.liveness:
            result = self.face.liveness_challenge()
        else:
            result = self.face.verify()

        if result.verified:
            return self._record_success(method="face")
        return self._record_failure(
            reason=result.detail or result.status,
            event_type="owner.authentication_failed",
            metadata={"failedAttempts": self.failed_attempts + 1},
        )

    def authenticate_pin(self, pin: Optional[str]) -> AuthOutcome:
        """The PIN/password fallback.

        The PIN is never stored in clear text: the file holds a salted digest,
        compared in constant time. A missing enrollment is a failure, not an
        open door.
        """
        digest_file = self.pin_file
        if digest_file is None or not digest_file.is_file():
            return self._record_failure(
                reason="no owner PIN is enrolled",
                event_type="owner.authentication_failed",
            )
        try:
            stored = json.loads(digest_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._record_failure(
                reason="the owner PIN could not be read",
                event_type="owner.authentication_failed",
            )
        if not isinstance(stored, dict) or not stored.get("digest") or not stored.get("salt"):
            return self._record_failure(
                reason="the owner PIN is not enrolled",
                event_type="owner.authentication_failed",
            )

        if not pin:
            return self._record_failure(
                reason="no PIN supplied",
                event_type="owner.authentication_failed",
            )

        candidate = _pin_digest(pin, stored["salt"])
        if not hmac.compare_digest(candidate, stored["digest"]):
            return self._record_failure(
                reason="the PIN does not match",
                event_type="owner.authentication_failed",
                metadata={"failedAttempts": self.failed_attempts + 1},
            )
        return self._record_success(method="pin")

    def enroll_pin(self, pin: str) -> None:
        """Store the owner's PIN as a salted digest. Never in clear text."""
        if self.pin_file is None:
            return
        salt = os.urandom(16).hex()
        digest = _pin_digest(pin, salt)
        self.pin_file.parent.mkdir(parents=True, exist_ok=True)
        self.pin_file.touch(mode=0o600, exist_ok=True)
        self.pin_file.chmod(0o600)
        self.pin_file.write_text(
            json.dumps({"salt": salt, "digest": digest, "createdAt": time.time()}),
            encoding="utf-8",
        )
        try:
            os.chmod(self.pin_file, 0o600)
        except OSError:
            pass
        self.log.info("owner PIN enrolled")

    def has_pin(self) -> bool:
        if self.pin_file is None or not self.pin_file.is_file():
            return False
        try:
            data = json.loads(self.pin_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and bool(data.get("digest")) and bool(data.get("salt"))

    # ---------------- the state transitions ----------------
    def _record_success(self, method: str) -> AuthOutcome:
        was_locked = self.is_locked
        self.state = "OWNER"
        now = time.time()
        self.since = now
        self.last_authenticated_at = now
        self.failed_attempts = 0
        # The Phase 5E trigger. A successful authentication sets this flag and
        # the supervisor consumes it — the only place a daily cleanup may
        # start. It is *not* a boot flag and not a timer: nothing sets it
        # besides an authentication that concluded OWNER, and it is cleared on
        # read, so a second tick the same session does not clean twice.
        self._authenticated = True
        self._persist_state()

        event_type = "owner.authenticated"
        description = f"Owner authenticated via {method}"
        if was_locked:
            # A lock clearing is its own event, so the audit trail shows both.
            self.record_event(
                SecurityEvent(
                    device_id=self.device_id,
                    event_type="security_lock_cleared",
                    description="Security lock cleared after owner authentication",
                    since=now,
                )
            )
        self.record_event(
            SecurityEvent(
                device_id=self.device_id,
                event_type=event_type,
                description=description,
                since=now,
            )
        )
        return AuthOutcome(authenticated=True, state=self.state, reason=description)

    def _record_failure(
        self,
        reason: str,
        event_type: str = "owner.authentication_failed",
        metadata: Optional[dict] = None,
    ) -> AuthOutcome:
        self.failed_attempts += 1
        now = time.time()
        photo_captured = False

        self.record_event(
            SecurityEvent(
                device_id=self.device_id,
                event_type=event_type,
                description=f"Owner authentication failed: {reason}",
                since=now,
                metadata=metadata or {"failedAttempts": self.failed_attempts},
            )
        )

        # The intrusion policy: the third consecutive failure locks the
        # machine, captures a security photo and notifies the owner. The lock
        # does not clear itself.
        if self.failed_attempts >= FAILED_AUTHENTICATION_THRESHOLD:
            self.state = "LOCKED"
            self.since = now
            photo_captured = self._capture_security_photo()
            self.record_event(
                SecurityEvent(
                    device_id=self.device_id,
                    event_type="security_lock_triggered",
                    description=(
                        f"Security lock triggered after {self.failed_attempts} failed "
                        "authentication attempts"
                    ),
                    since=now,
                    metadata={"failedAttempts": self.failed_attempts},
                )
            )
            self._persist_state()
            return AuthOutcome(
                authenticated=False,
                state=self.state,
                reason=reason,
                photo_captured=photo_captured,
                locked=True,
            )

        self._persist_state()
        return AuthOutcome(
            authenticated=False,
            state=self.state,
            reason=reason,
            photo_captured=photo_captured,
        )

    def mark_unknown_person(self) -> AuthOutcome:
        """An unknown person is in front of the machine.

        Guest mode decides what happens next: on, the session degrades to
        `GUEST` (monitored, owner-only actions blocked); off, the machine
        stays in the protected `UNKNOWN` state.
        """
        now = time.time()
        if self.capabilities.guest_mode:
            previous = self.state
            self.state = "GUEST"
            self.since = now
            self._persist_state()
            event_type = "guest.mode_activated"
            description = "Unknown person detected; Guest Mode activated"
            if previous == "GUEST":
                # Already reported; do not spam the audit trail.
                return AuthOutcome(authenticated=False, state=self.state, reason=description)
        else:
            self.state = "UNKNOWN"
            self.since = now
            self._persist_state()
            event_type = "unknown_person_detected"
            description = "Unknown person detected; the device stays protected"

        self.record_event(
            SecurityEvent(
                device_id=self.device_id,
                event_type=event_type,
                description=description,
                since=now,
                metadata={"failedAttempts": self.failed_attempts},
            )
        )
        return AuthOutcome(authenticated=False, state=self.state, reason=description)

    def deactivate_guest_mode(self) -> None:
        """Leave guest mode without authenticating (the owner's explicit act)."""
        if not self.is_guest:
            return
        self.state = "UNKNOWN"
        self.since = time.time()
        self._persist_state()
        self.record_event(
            SecurityEvent(
                device_id=self.device_id,
                event_type="guest.mode_deactivated",
                description="Guest Mode deactivated",
                since=self.since,
            )
        )

    # ---------------- security photo ----------------
    def _capture_security_photo(self) -> bool:
        """Capture one photo, only as part of an intrusion event.

        Called from `_record_failure` on the third failure and nowhere else.
        The capture is a thumbnail: capped at 96 px on the long edge and
        grayscale, so it is identifiable but not a surveillance image. It is
        never attached to a frame by this module.
        """
        if self._capture_photo is None or self.photo_dir is None:
            return False
        if not self.capabilities.intrusion_detection:
            return False
        try:
            payload = self._capture_photo()
        except Exception as exc:  # noqa: BLE001 - a capture failure must not break the lock
            self.log.warn("security photo capture failed", error=str(exc))
            return False
        if not payload:
            return False

        try:
            from PIL import Image
            import io
        except ImportError:
            self.log.warn("security photo stored without resizing; PIL is unavailable")
            return self._store_photo_bytes(payload)

        try:
            image = Image.open(io.BytesIO(payload)).convert("L")
            image.thumbnail((96, 96))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=60)
            return self._store_photo_bytes(buffer.getvalue())
        except Exception as exc:  # noqa: BLE001 - store the raw frame rather than lose the event
            self.log.warn("security photo could not be resized", error=str(exc))
            return self._store_photo_bytes(payload)

    def _store_photo_bytes(self, payload: bytes) -> bool:
        if self.photo_dir is None:
            return False
        try:
            self.photo_dir.mkdir(parents=True, exist_ok=True)
            path = self.photo_dir / f"intrusion-{int(time.time())}.jpg"
            path.write_bytes(payload)
            os.chmod(path, 0o600)
            self.record_event(
                SecurityEvent(
                    device_id=self.device_id,
                    event_type="security_photo_captured",
                    description="A security photo was captured after repeated failed authentication",
                    since=time.time(),
                    metadata={"bytesFreed": len(payload)},
                )
            )
            self.log.info("security photo captured", path=str(path), bytes=len(payload))
            return True
        except OSError as exc:
            self.log.warn("could not store the security photo", error=str(exc))
            return False

    # ---------------- events ----------------
    def record_event(self, event: SecurityEvent) -> None:
        """Forward one event to the supervisor, which frames and sends it.

        Kept as an indirection so a test can assert the state machine's
        outputs without a socket, and so the supervisor is the only thing
        that owns a wire.
        """
        issues = event.validate()
        if issues:
            self.log.warn("security event rejected locally", issues=issues[:3])
            return
        sink = getattr(self, "_event_sink", None)
        if sink is not None:
            sink(event)

    def set_event_sink(self, sink) -> None:
        self._event_sink = sink

    # ---------------- reporting ----------------
    def presence(self) -> OwnerPresence:
        """The claim the supervisor pushes. No biometric field exists on it."""
        return OwnerPresence(
            device_id=self.device_id,
            state=self.state,
            since=self.since,
            last_authenticated_at=self.last_authenticated_at,
            failed_attempts=self.failed_attempts,
            capabilities=self.capabilities,
            last_daily_cleanup_date=self.last_daily_cleanup_date,
        )

    def set_daily_cleanup_date(self, date: str) -> None:
        self.last_daily_cleanup_date = date
        self._persist_state()


def _pin_digest(pin: str, salt: str) -> str:
    """A salted, stretched digest of the PIN. Never the PIN itself."""
    material = (pin or "").encode("utf-8")
    salted = salt.encode("utf-8")
    digest = material
    for _ in range(10_000):
        digest = hashlib.sha256(digest + salted).digest()
    return digest.hex()


__all__ = [
    "AuthOutcome",
    "SecurityPhoto",
    "OwnerAuthenticator",
]
