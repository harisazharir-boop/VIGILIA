"""
VIGILIA agent — telemetry contract.

This module is the single source of truth for the shape of an agent report.
The Python mirror lives here; the authoritative TypeScript contract lives in
`shared/src/types.ts` and `shared/src/schemas.ts`, and the two must stay
in sync.

Telemetry is strictly read-only system statistics. The agent does not and
will not expose file access, shell execution, or system mutation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, TypedDict


class NetworkStatus(TypedDict, total=False):
    connected: bool
    ssid: str
    download_mbps: float
    upload_mbps: float
    interface: str


@dataclass
class Telemetry:
    """One telemetry sample reported to the backend."""

    device_id: str
    cpu: Optional[float] = None
    ram: Optional[float] = None
    storage: Optional[float] = None
    battery: Optional[float] = None
    os: Optional[str] = None
    hostname: Optional[str] = None
    uptime: Optional[int] = None
    gpu: Optional[float] = None
    temp: Optional[float] = None
    network: Optional[NetworkStatus] = None
    hardware: Optional[dict] = None
    network_telemetry: Optional[dict] = None
    timestamp: str = ""

    def with_timestamp(self) -> "Telemetry":
        return Telemetry(
            device_id=self.device_id,
            cpu=self.cpu,
            ram=self.ram,
            storage=self.storage,
            battery=self.battery,
            os=self.os,
            hostname=self.hostname,
            uptime=self.uptime,
            gpu=self.gpu,
            temp=self.temp,
            network=self.network,
            hardware=self.hardware,
            network_telemetry=self.network_telemetry,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )

    def to_payload(self) -> dict:
        """JSON payload accepted by the backend (camelCase)."""
        payload = {
            "deviceId": self.device_id,
            "timestamp": self.timestamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        mapping = {
            "cpu": self.cpu,
            "ram": self.ram,
            "storage": self.storage,
            "battery": self.battery,
            "os": self.os,
            "hostname": self.hostname,
            "uptime": self.uptime,
            "gpu": self.gpu,
            "temp": self.temp,
        }
        for key, value in mapping.items():
            if value is not None:
                payload[key] = value
        if self.network is not None:
            network = dict(self.network)
            payload["network"] = {
                key: value
                for key, value in {
                    "connected": network.get("connected", True),
                    "ssid": network.get("ssid"),
                    "downloadMbps": network.get("download_mbps"),
                    "uploadMbps": network.get("upload_mbps"),
                    "interface": network.get("interface"),
                }.items()
                if value is not None
            }
        if self.hardware is not None:
            payload["hardware"] = self.hardware
        if self.network_telemetry is not None:
            payload["networkTelemetry"] = self.network_telemetry
        return payload


@dataclass
class Heartbeat:
    device_id: str
    status: str = "online"

    def to_payload(self) -> dict:
        return {
            "deviceId": self.device_id,
            "status": self.status,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }


# ---------------- remote commands ----------------
#
# The command allowlist is duplicated by design: the authoritative contract
# lives in shared/src/types.ts (DEVICE_COMMANDS) and the backend rejects
# anything not in it. The agent re-validates on receipt and never executes a
# command it does not know, so a crafted frame from a compromised backend
# cannot turn it into a shell.

COMMAND_ALLOWLIST = (
    "LOCK",
    "SLEEP",
    "HIBERNATE",
    "RESTART",
    "SHUTDOWN",
    "APPLICATION_OPEN",
    "APPLICATION_CLOSE",
    "APPLICATION_RESTART",
)

#: The three commands that take an application target. Kept separate so a
#: caller can tell "needs a target" from "is a known command".
APPLICATION_COMMANDS = (
    "APPLICATION_OPEN",
    "APPLICATION_CLOSE",
    "APPLICATION_RESTART",
)

#: Cleanup commands (Phase 4B). Not part of `COMMAND_ALLOWLIST` above because
#: they are not executed by the command executor: they are served by the
#: platform's cleaner, which resolves the structured action id to its own
#: fixed roots. The two lists are disjoint on purpose — a cleanup is a
#: different kind of action, and keeping it out of the command executor means
#: the power-command path cannot reach the deletion code.
CLEAN_COMMANDS = (
    "CLEAN_TEMP",
    "CLEAN_BROWSER_CACHE",
    "CLEAN_TRASH",
    "CLEAN_PACKAGE_CACHE",
    "CLEAN_USER_CACHE",
    "CLEAN_THUMBNAIL_CACHE",
    "CLEAN_OLD_LOGS",
)



def is_allowed_command(command: Optional[str]) -> bool:
    return isinstance(command, str) and command in COMMAND_ALLOWLIST


def is_application_command(command: Optional[str]) -> bool:
    return isinstance(command, str) and command in APPLICATION_COMMANDS


def is_clean_command(command: Optional[str]) -> bool:
    """True for a Phase 4B cleanup action id."""
    return isinstance(command, str) and command in CLEAN_COMMANDS



def requires_target(command: Optional[str]) -> bool:
    """True when the command must carry an `applicationId`."""
    return is_application_command(command)


@dataclass
class CommandDispatch:
    """One `command.dispatch` frame received from the backend."""

    request_id: str
    command: str
    deadline: float = 0.0
    note: Optional[str] = None
    #: Application target for `APPLICATION_*` commands. Always an
    #: `applicationId`, never an executable path.
    target: Optional[str] = None

    @classmethod
    def from_payload(cls, data: dict) -> "CommandDispatch":
        return cls(
            request_id=str(data.get("requestId") or ""),
            command=str(data.get("command") or ""),
            deadline=float(data.get("deadline") or 0.0),
            note=data.get("note"),
            target=data.get("target"),
        )

    @property
    def expired(self) -> bool:
        if not self.deadline:
            return False
        return time.time() > self.deadline

    @property
    def is_valid(self) -> bool:
        """Frame-level validity: a known command, and a target where required.

        A cleanup command is valid here only when it names a *clean action id*
        from the allowlist — never a path, never a glob. The target field is
        not consulted for cleanup: there is nothing a cleanup could do with a
        target, and the field is not present in the cleanup request shape.
        """
        if is_allowed_command(self.command):
            if requires_target(self.command) and not self.target:
                return False
            return True
        return is_clean_command(self.command)


@dataclass
class CommandResult:
    """The `command.result` frame the agent answers a dispatch with."""

    request_id: str
    status: str = "succeeded"
    result: Optional[str] = None
    duration_ms: Optional[int] = None

    def to_payload(self) -> dict:
        payload = {
            "requestId": self.request_id,
            "status": self.status,
        }
        if self.result is not None:
            payload["result"] = self.result[:1000]
        if self.duration_ms is not None:
            payload["durationMs"] = self.duration_ms
        return payload



def system_info_payload(agent_version: str) -> dict:
    """Static identification block sent at registration time."""
    raise NotImplementedError("implemented per platform in agents/<platform>/src/collectors.py")


__all__ = [
    "Telemetry",
    "Heartbeat",
    "NetworkStatus",
    "CommandDispatch",
    "CommandResult",
    "COMMAND_ALLOWLIST",
    "APPLICATION_COMMANDS",
    "CLEAN_COMMANDS",
    "is_allowed_command",
    "is_application_command",
    "is_clean_command",
    "requires_target",
    "asdict",
]
