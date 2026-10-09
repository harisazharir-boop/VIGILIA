"""
VIGILIA agent — owner authentication contract (Phase 5A).

The Python mirror of `shared/src/types.ts` (OWNER_STATES, OwnerPresence,
SecurityCapabilities, SecurityEvent, DailyCleanupConfig). The TypeScript
contract is authoritative; the two must stay in sync.

Where biometric data lives
--------------------------
**On the device, and only on the device.** This module defines the shape of
the *result* of an authentication, and that shape carries no biometric
field — no image, no embedding, no template, no confidence score. The owner's
face template is stored in a local file the agent owns (mode 0600), is never
attached to a frame, and has no code path that could send it. The backend
receives `state` and `failedAttempts`; that is all it needs and all it gets.

Cameras and liveness
--------------------
Face verification is platform code (`agents/<platform>/src/face.py`) and is
gated by `FaceVerifier.is_supported()`. A host with no camera, no PIL or no
usable capture device reports exactly that, and the owner authentication
state falls back to PIN (or to `UNKNOWN` if the operator never enrolled
either). Nothing is faked: there is no "pretend the owner is present" path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

#: The four trust states. Explicit, so no code path infers trust.
OWNER_STATES = ("OWNER", "GUEST", "UNKNOWN", "LOCKED")

#: Failure count that raises a security lock. The phase fixes this at 3.
FAILED_AUTHENTICATION_THRESHOLD = 3

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _epoch() -> float:
    return datetime.now(timezone.utc).timestamp()

@dataclass
class SecurityCapabilities:
    """Safe capability flags the device reports. None is a secret."""

    face_recognition: bool = False
    liveness: bool = False
    guest_mode: bool = True
    intrusion_detection: bool = True
    daily_cleanup: bool = False

    def to_payload(self) -> dict:
        return {
            "faceRecognition": bool(self.face_recognition),
            "liveness": bool(self.liveness),
            "guestMode": bool(self.guest_mode),
            "intrusionDetection": bool(self.intrusion_detection),
            "dailyCleanup": bool(self.daily_cleanup),
        }

    @classmethod
    def from_payload(cls, data: Optional[dict]) -> "SecurityCapabilities":
        data = data or {}
        return cls(
            face_recognition=bool(data.get("faceRecognition", False)),
            liveness=bool(data.get("liveness", False)),
            guest_mode=bool(data.get("guestMode", True)),
            intrusion_detection=bool(data.get("intrusionDetection", True)),
            daily_cleanup=bool(data.get("dailyCleanup", False)),
        )


@dataclass
class OwnerPresence:
    """The device's owner-presence claim — the *outcome*, never biometrics."""

    device_id: str
    state: str = "UNKNOWN"
    since: float = field(default_factory=_epoch)
    last_authenticated_at: Optional[float] = None
    failed_attempts: int = 0
    capabilities: SecurityCapabilities = field(default_factory=SecurityCapabilities)
    last_daily_cleanup_date: Optional[str] = None
    timestamp: str = ""

    def with_timestamp(self) -> "OwnerPresence":
        return OwnerPresence(
            device_id=self.device_id,
            state=self.state,
            since=self.since,
            last_authenticated_at=self.last_authenticated_at,
            failed_attempts=self.failed_attempts,
            capabilities=self.capabilities,
            last_daily_cleanup_date=self.last_daily_cleanup_date,
            timestamp=_now(),
        )

    def to_payload(self) -> dict:
        payload = {
            "deviceId": self.device_id,
            "state": self.state,
            # Epoch seconds as an integer: the contract is `z.number().int()`,
            # and `time.time()` carries a fractional part the schema refuses.
            "since": int(self.since),
            "failedAttempts": max(0, min(999, int(self.failed_attempts))),
            "capabilities": self.capabilities.to_payload(),
            "timestamp": self.timestamp or _now(),
        }
        if self.last_authenticated_at is not None:
            payload["lastAuthenticatedAt"] = int(self.last_authenticated_at)
        if self.last_daily_cleanup_date is not None:
            payload["lastDailyCleanupDate"] = self.last_daily_cleanup_date
        return payload

    def validate(self) -> list[str]:
        issues: list[str] = []
        if self.state not in OWNER_STATES:
            issues.append(f"unknown owner state: {self.state}")
        if self.failed_attempts < 0:
            issues.append("failedAttempts is negative")
        return issues


#: Security event types, mirrored from the TS contract. The backend derives
#: the severity from the type, so this tuple is the whole vocabulary a device
#: may speak.
SECURITY_EVENT_TYPES = (
    "owner.authenticated",
    "owner.authentication_failed",
    "owner.locked",
    "unknown_person_detected",
    "guest.mode_activated",
    "guest.mode_deactivated",
    "security_photo_captured",
    "security_lock_triggered",
    "security_lock_cleared",
    "daily_cleanup.started",
    "daily_cleanup.completed",
    "daily_cleanup.skipped",
    "daily_cleanup.failed",
)

@dataclass
class SecurityEvent:
    """One security event the device reports.

    The metadata keys are the only ones the backend's strict schema accepts,
    and this class cannot hold anything else: there is no field for a photo,
    a path, a credential or biometric data. A security *photo* is captured as
    part of an intrusion event and stays on the device; the event says it
    happened, and the mobile client asks for it separately if it wants it.
    """

    device_id: str
    event_type: str
    description: str
    since: Optional[float] = None
    metadata: Optional[dict] = None
    request_id: Optional[str] = None

    def to_payload(self) -> dict:
        payload: dict = {
            "deviceId": self.device_id,
            "eventType": self.event_type,
            "description": self.description[:280],
        }
        if self.since is not None:
            payload["since"] = int(self.since)
        if self.metadata:
            payload["metadata"] = strip_metadata(self.metadata)
        if self.request_id:
            payload["requestId"] = self.request_id[:64]
        return payload

    def validate(self) -> list[str]:
        issues: list[str] = []
        if self.event_type not in SECURITY_EVENT_TYPES:
            issues.append(f"unknown event type: {self.event_type}")
        if not self.description.strip():
            issues.append(f"{self.event_type}: description is empty")
        if self.metadata is not None:
            for key in self.metadata:
                if key not in SECURITY_METADATA_KEYS:
                    issues.append(f"{self.event_type}: unknown metadata key {key}")
        return issues


#: The safe metadata keys, mirrored from the TS contract.
SECURITY_METADATA_KEYS = (
    "failedAttempts",
    "bytesFreed",
    "actions",
    "reason",
    "actionId",
)

def strip_metadata(metadata: dict) -> dict:
    """Keep only the enumerated, safe metadata keys, coerced to safe ranges."""
    out: dict = {}
    for key, value in (metadata or {}).items():
        if key not in SECURITY_METADATA_KEYS:
            continue
        if key == "failedAttempts":
            out[key] = max(0, min(999, int(value or 0)))
        elif key == "bytesFreed":
            out[key] = max(0, int(value or 0))
        elif key == "actions":
            out[key] = max(0, min(64, int(value or 0)))
        elif key == "reason":
            out[key] = str(value)[:64]
        elif key == "actionId":
            from agents.common.cleaner import is_clean_action

            if is_clean_action(value):
                out[key] = value
    return out


#: Daily Auto Cleanup triggers. One exists, so the field is a closed enum.
DAILY_CLEANUP_TRIGGERS = ("AFTER_OWNER_LOGIN",)

#: Categories safe to run automatically. A subset of `CLEAN_ACTIONS`, not a
#: second allowlist: every id here must be a valid clean action.
DAILY_SAFE_CLEAN_ACTIONS = (
    "CLEAN_TEMP",
    "CLEAN_BROWSER_CACHE",
    "CLEAN_THUMBNAIL_CACHE",
    "CLEAN_PACKAGE_CACHE",
    "CLEAN_OLD_LOGS",
    "CLEAN_TRASH",
)

#: Reasons a daily cleanup was skipped, mirrored from the TS contract.
DAILY_CLEANUP_SKIP_REASONS = (
    "NOT_AUTHENTICATED",
    "ALREADY_DONE",
    "DISABLED",
    "NO_CATEGORIES",
)

DAILY_CLEANUP_SKIP_LABELS = {
    "NOT_AUTHENTICATED": "Owner not authenticated",
    "ALREADY_DONE": "Already completed today",
    "DISABLED": "Daily cleanup is disabled",
    "NO_CATEGORIES": "No safe categories selected",
}

#: The conservative default: **off**, no trash, no arbitrary age.
DEFAULT_DAILY_CLEANUP_CONFIG = {
    "enabled": False,
    "trigger": "AFTER_OWNER_LOGIN",
    "categories": (
        "CLEAN_TEMP",
        "CLEAN_BROWSER_CACHE",
        "CLEAN_THUMBNAIL_CACHE",
        "CLEAN_PACKAGE_CACHE",
    ),
    "trashEnabled": False,
    "logRetentionDays": 30,
}

def is_daily_safe_action(action_id: object) -> bool:
    """True only for an action id the daily cleanup may run."""
    return isinstance(action_id, str) and action_id in DAILY_SAFE_CLEAN_ACTIONS


__all__ = [
    "OWNER_STATES",
    "FAILED_AUTHENTICATION_THRESHOLD",
    "SECURITY_EVENT_TYPES",
    "SECURITY_METADATA_KEYS",
    "DAILY_CLEANUP_TRIGGERS",
    "DAILY_SAFE_CLEAN_ACTIONS",
    "DAILY_CLEANUP_SKIP_REASONS",
    "DAILY_CLEANUP_SKIP_LABELS",
    "DEFAULT_DAILY_CLEANUP_CONFIG",
    "SecurityCapabilities",
    "OwnerPresence",
    "SecurityEvent",
    "strip_metadata",
    "is_daily_safe_action",
]
