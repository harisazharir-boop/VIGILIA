"""
VIGILIA agent — platform interface.

Every platform agent (agents/linux, agents/windows) implements the same
conceptual surface. The methods are read-only by design; the agent reports
system state and never mutates it.
"""
from __future__ import annotations

import abc
from typing import Optional

from agents.common.processes import ProcessInfo
from agents.common.storage import StorageSummary
from agents.common.telemetry import NetworkStatus


class PlatformCollector(abc.ABC):
    """Read-only system telemetry surface shared by all platforms."""

    @abc.abstractmethod
    def get_system_info(self) -> dict:
        """Static identification: os, hostname, versions, hardware totals."""

    @abc.abstractmethod
    def get_cpu_usage(self) -> Optional[float]:
        """CPU utilisation as a percentage in [0, 100]."""

    @abc.abstractmethod
    def get_memory_usage(self) -> Optional[float]:
        """RAM utilisation as a percentage in [0, 100]."""

    @abc.abstractmethod
    def get_storage_usage(self) -> Optional[float]:
        """Storage utilisation of the root volume, percentage in [0, 100]."""

    @abc.abstractmethod
    def get_battery_status(self) -> Optional[float]:
        """Battery charge as a percentage in [0, 100]."""

    @abc.abstractmethod
    def get_network_status(self) -> Optional[NetworkStatus]:
        """Connectivity state of the primary interface."""

    @abc.abstractmethod
    def get_processes(self) -> list[dict]:
        """Top processes by resource use. cpu/ram percentages, name, pid."""

    @abc.abstractmethod
    def get_process_list(self, limit: int, include_command_line: bool = False) -> list[ProcessInfo]:
        """Enumerate processes, most expensive first, capped at `limit`."""

    @abc.abstractmethod
    def get_storage_summary(self, device_id: str) -> StorageSummary:
        """Read-only disk breakdown for this host (Phase 4A). Never mutates."""
