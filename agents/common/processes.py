"""
VIGILIA agent — process telemetry contract (Phase 3A).

The Python mirror of `shared/src/types.ts` (ProcessInfo / ProcessSnapshot)
and `shared/src/schemas.ts` (processInfoSchema / processSnapshotSchema).
The TypeScript contract is authoritative; the two must stay in sync.

This is read-only telemetry. It carries no control capability — there is no
kill verb here. Process *control* (APPLICATION_OPEN / APPLICATION_CLOSE /
APPLICATION_RESTART) is a separate command allowlist that Phase 3B adds on
top of the Phase 2 command surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# Statuses the wire contract accepts. Platform collectors map their native
# alphabets onto these (Linux `R`/`S`/`D`, Windows `Running`/`Suspended`);
# a platform that cannot determine a state leaves it out rather than
# inventing a value.
PROCESS_STATUSES = (
    "running",
    "sleeping",
    "stopped",
    "zombie",
    "idle",
    "unknown",
)

#: Absolute ceiling the schema accepts, independent of the configurable
#: VIGILIA_PROCESS_LIMIT. The backend rejects (does not truncate) a frame
#: above this, so a misbehaving agent cannot flood a mobile socket.
PROCESS_HARD_CAP = 512

#: Default cap applied per snapshot when no configuration is supplied.
DEFAULT_PROCESS_LIMIT = 64

#: Length caps mirrored from processInfoSchema, so a collector on a hostile
#: host cannot emit a frame the backend rejects.
MAX_NAME_LENGTH = 512
MAX_PATH_LENGTH = 2048
MAX_USERNAME_LENGTH = 256
MAX_COMMAND_LINE_LENGTH = 8192

_LINUX_STATUS_MAP = {
    "R": "running",
    "S": "sleeping",
    "D": "sleeping",  # uninterruptible disk sleep — still a sleep state
    "Z": "zombie",
    "T": "stopped",
    "t": "stopped",
    "I": "idle",
    "W": "running",  # paging, only on ancient kernels; treat as runnable
    "P": "running",
    "X": "stopped",
    "K": "sleeping",  # wakekill — a task being killed out of an uninterruptible wait
}

_WINDOWS_STATUS_MAP = {
    "running": "running",
    "sleeping": "sleeping",  # psutil's own spelling on Windows
    "suspended": "sleeping",
    "suspendedusermode": "sleeping",
    "new": "running",
    "ready": "running",
    "runningready": "running",
    "terminated": "stopped",
    "stopped": "stopped",
    "wait": "sleeping",
    "transition": "running",
    "unknown": "unknown",
}

#: psutil's own status spellings, which both platforms report. They are
#: matched case-insensitively and take precedence over the single-letter and
#: native-name tables when they collide.
_PSUTIL_STATUS_MAP = {
    "running": "running",
    "sleeping": "sleeping",
    "disk-sleep": "sleeping",
    "idle": "idle",
    "stopped": "stopped",
    "traced": "stopped",  # stopped by a debugger — a stopped state
    "zombie": "zombie",
    "dead": "stopped",
    "wake-one": "running",
    "locked": "stopped",
}


def normalize_status(raw: Optional[str]) -> Optional[str]:
    """Map a native process state onto the wire enum, or None to omit it.

    A one- or two-character code is a Linux state letter and matched
    case-insensitively; anything longer is a process-state name (psutil's own
    spelling, a Windows `ThreadState`, or a native name) and matched
    case-insensitively too. An unrecognised value becomes `unknown` rather
    than being dropped, because a state we cannot name is still a state we
    should report as unclassifiable.
    """
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if len(text) <= 2:
        return _LINUX_STATUS_MAP.get(text.upper(), "unknown")
    key = text.lower()
    if key in _PSUTIL_STATUS_MAP:
        return _PSUTIL_STATUS_MAP[key]
    return _WINDOWS_STATUS_MAP.get(key, "unknown")


def _trim(value: Optional[str], limit: int) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return text[:limit]


@dataclass
class ProcessInfo:
    """One process, as seen by an agent."""

    pid: int
    name: str
    cpu: float = 0.0
    memory_mb: Optional[float] = None
    memory_percent: Optional[float] = None
    threads: Optional[int] = None
    parent_pid: Optional[int] = None
    status: Optional[str] = None
    executable_path: Optional[str] = None
    username: Optional[str] = None
    started_at: Optional[str] = None
    command_line: Optional[str] = None

    @classmethod
    def from_payload(cls, data: dict) -> "ProcessInfo":
        return cls(
            pid=int(data.get("pid") or -1),
            name=str(data.get("name") or "unknown"),
            cpu=float(data.get("cpu") or 0.0),
            memory_mb=data.get("memoryMb"),
            memory_percent=data.get("memoryPercent"),
            threads=data.get("threads"),
            parent_pid=data.get("parentPid"),
            status=data.get("status"),
            executable_path=data.get("executablePath"),
            username=data.get("username"),
            started_at=data.get("startedAt"),
            command_line=data.get("commandLine"),
        )

    def to_payload(self) -> dict:
        """JSON payload accepted by the backend (camelCase)."""
        payload: dict = {"pid": self.pid, "name": self.name, "cpu": round(self.cpu, 1)}
        if self.memory_mb is not None:
            payload["memoryMb"] = round(float(self.memory_mb), 1)
        if self.memory_percent is not None:
            payload["memoryPercent"] = round(float(self.memory_percent), 1)
        if self.threads is not None:
            payload["threads"] = int(self.threads)
        if self.parent_pid is not None:
            payload["parentPid"] = int(self.parent_pid)
        if self.status is not None:
            payload["status"] = self.status
        if self.executable_path is not None:
            payload["executablePath"] = self.executable_path
        if self.username is not None:
            payload["username"] = self.username
        if self.started_at is not None:
            payload["startedAt"] = self.started_at
        if self.command_line is not None:
            payload["commandLine"] = self.command_line
        return payload

    def validate(self) -> list[str]:
        """Return issues that would make the backend reject this entry."""
        issues: list[str] = []
        if not isinstance(self.pid, int):
            issues.append("pid must be an integer")
        elif self.pid < -1:
            issues.append("pid must be >= -1")
        if not self.name or not self.name.strip():
            issues.append("name must not be empty")
        if not (-0.0 <= float(self.cpu) <= 100.0):
            issues.append("cpu must be within 0..100")
        if self.memory_percent is not None and not (0.0 <= float(self.memory_percent) <= 100.0):
            issues.append("memoryPercent must be within 0..100")
        if self.status is not None and self.status not in PROCESS_STATUSES:
            issues.append(f"status must be one of {PROCESS_STATUSES}")
        if len(self.name) > MAX_NAME_LENGTH:
            issues.append(f"name longer than {MAX_NAME_LENGTH}")
        if self.executable_path and len(self.executable_path) > MAX_PATH_LENGTH:
            issues.append(f"executablePath longer than {MAX_PATH_LENGTH}")
        if self.username and len(self.username) > MAX_USERNAME_LENGTH:
            issues.append(f"username longer than {MAX_USERNAME_LENGTH}")
        if self.command_line and len(self.command_line) > MAX_COMMAND_LINE_LENGTH:
            issues.append(f"commandLine longer than {MAX_COMMAND_LINE_LENGTH}")
        return issues


@dataclass
class ProcessSnapshot:
    """The `processes` frame an agent pushes."""

    device_id: str
    processes: list[ProcessInfo] = field(default_factory=list)
    total_count: int = 0
    limit: int = DEFAULT_PROCESS_LIMIT
    timestamp: str = ""

    def with_timestamp(self) -> "ProcessSnapshot":
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return ProcessSnapshot(
            device_id=self.device_id,
            processes=list(self.processes),
            total_count=self.total_count,
            limit=self.limit,
            timestamp=stamp,
        )

    def to_payload(self) -> dict:
        return {
            "deviceId": self.device_id,
            "processes": [proc.to_payload() for proc in self.processes],
            "totalCount": int(self.total_count),
            "limit": int(self.limit),
            "timestamp": self.timestamp
            or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not self.device_id:
            issues.append("deviceId must not be empty")
        if len(self.processes) > PROCESS_HARD_CAP:
            issues.append(f"more than {PROCESS_HARD_CAP} processes")
        if self.total_count < 0:
            issues.append("totalCount must be >= 0")
        # A total above the hard cap is only legitimate if the cap was applied
        # (totalCount counts the untruncated list). When the entry list itself
        # is already over the cap, the frame is genuinely oversized.
        if self.total_count > PROCESS_HARD_CAP and len(self.processes) > PROCESS_HARD_CAP:
            issues.append(f"more than {PROCESS_HARD_CAP} processes")
        if not (1 <= self.limit <= PROCESS_HARD_CAP):
            issues.append(f"limit must be within 1..{PROCESS_HARD_CAP}")
        for proc in self.processes:
            issues.extend(proc.validate())
        return issues


def build_snapshot(
    device_id: str,
    entries: list[ProcessInfo],
    limit: int = DEFAULT_PROCESS_LIMIT,
) -> ProcessSnapshot:
    """Order by resource use, apply the cap and record the true total.

    A non-positive or non-integer `limit` clamps to 1 rather than being
    treated as "unlimited": the wire contract always carries a finite cap.
    """
    try:
        requested = int(limit)
    except (TypeError, ValueError):
        requested = DEFAULT_PROCESS_LIMIT
    cap = max(1, min(requested, PROCESS_HARD_CAP))
    ordered = sorted(entries, key=lambda item: (item.cpu + 10.0 * (item.memory_percent or 0.0)), reverse=True)
    return ProcessSnapshot(
        device_id=device_id,
        processes=ordered[:cap],
        total_count=len(entries),
        limit=cap,
    )


__all__ = [
    "PROCESS_STATUSES",
    "PROCESS_HARD_CAP",
    "DEFAULT_PROCESS_LIMIT",
    "MAX_NAME_LENGTH",
    "MAX_PATH_LENGTH",
    "MAX_USERNAME_LENGTH",
    "MAX_COMMAND_LINE_LENGTH",
    "ProcessInfo",
    "ProcessSnapshot",
    "build_snapshot",
    "normalize_status",
]
