"""
VIGILIA Windows agent — system collectors.

Read-only telemetry for Windows hosts. Two implementations:
  1. ctypes calls into the Win32 APIs (kernel32/psapi) — no dependency on
     pywin32, WMI pipes or PowerShell.
  2. a pure-stdlib fallback (plus optional psutil) so the agent still reports
     when the native stubs are unavailable on the running machine.

Nothing is executed, written, or mutated. The module only reads counters,
GlobalMemoryStatusEx, GetDiskFreeSpaceExW and GetSystemPowerStatus results.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
import time
from ctypes import POINTER, Structure, byref
from datetime import datetime, timezone
from typing import Optional
from agents.common.logger import Logger
from agents.common.platform import PlatformCollector
from agents.common.storage import StorageSummary
from agents.windows.src.storage import WindowsStorageCollector
from agents.common.processes import (
    MAX_COMMAND_LINE_LENGTH,
    MAX_NAME_LENGTH,
    MAX_PATH_LENGTH,
    MAX_USERNAME_LENGTH,
    ProcessInfo,
    normalize_status,
)
from agents.common.telemetry import NetworkStatus
from agents.common.version import AGENT_VERSION

_IS_WINDOWS = sys.platform == "win32"
# PROCESS_QUERY_LIMITED_INFORMATION (0x1000) + PROCESS_VM_READ (0x0010).
# The limited-information access right works for processes the agent does not
# own (including protected/PPL processes on modern Windows); VM_READ is
# required to read the module name and command line.
_PROCESS_QUERY_LIMITED = 0x1000
_PROCESS_VM_READ = 0x0010
# PROCESSINFOCLASS :: ProcessBasicInformation; returns a PROCESS_BASIC_INFORMATION
# whose InheritedFromUniqueProcessId is the parent pid.
_ProcessBasicInformation = 0

try:
    import psutil  # type: ignore

    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore
    _HAS_PSUTIL = False

# ------------------------- native prototypes -------------------------
# Bound only on Windows; importing this module on Linux (the dev/test host)
# must stay possible so the fallback paths can be exercised.

if _IS_WINDOWS:
    from ctypes import windll, create_string_buffer, c_wchar_p
    from ctypes.wintypes import BOOL, DWORD, HANDLE, LARGE_INTEGER, LPCWSTR

    kernel32 = windll.kernel32

    class MEMORYSTATUSEX(Structure):
        _fields_ = [
            ("dwLength", DWORD),
            ("dwMemoryLoad", DWORD),
            ("ullTotalPhys", LARGE_INTEGER),
            ("ullAvailPhys", LARGE_INTEGER),
            ("ullTotalPageFile", LARGE_INTEGER),
            ("ullAvailPageFile", LARGE_INTEGER),
            ("ullTotalVirtual", LARGE_INTEGER),
            ("ullAvailVirtual", LARGE_INTEGER),
            ("ullAvailExtendedVirtual", LARGE_INTEGER),
        ]

    class SYSTEM_POWER_STATUS(Structure):
        _fields_ = [
            ("ACLineStatus", DWORD),
            ("BatteryFlag", DWORD),
            ("BatteryLifePercent", DWORD),
            ("Reserved1", DWORD),
            ("BatteryLifeTime", DWORD),
            ("BatteryFullLifeTime", DWORD),
        ]

    kernel32.GlobalMemoryStatusEx.argtypes = [POINTER(MEMORYSTATUSEX)]
    kernel32.GlobalMemoryStatusEx.restype = BOOL
    kernel32.GetSystemTimes.argtypes = [
        POINTER(LARGE_INTEGER),
        POINTER(LARGE_INTEGER),
        POINTER(LARGE_INTEGER),
    ]
    kernel32.GetSystemTimes.restype = BOOL
    kernel32.GetDiskFreeSpaceExW.argtypes = [
        LPCWSTR,
        POINTER(LARGE_INTEGER),
        POINTER(LARGE_INTEGER),
        POINTER(LARGE_INTEGER),
    ]
    kernel32.GetDiskFreeSpaceExW.restype = BOOL
    kernel32.GetTickCount64.argtypes = []
    kernel32.GetTickCount64.restype = LARGE_INTEGER
    kernel32.GetSystemPowerStatus.argtypes = [POINTER(SYSTEM_POWER_STATUS)]
    kernel32.GetSystemPowerStatus.restype = BOOL
    kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
    kernel32.OpenProcess.restype = HANDLE
    kernel32.CloseHandle.argtypes = [HANDLE]
    kernel32.CloseHandle.restype = BOOL

    psapi = windll.psapi

    class PROCESS_MEMORY_COUNTERS_EX(Structure):
        _fields_ = [
            ("cb", DWORD),
            ("PageFaultCount", DWORD),
            ("PeakWorkingSetSize", LARGE_INTEGER),
            ("WorkingSetSize", LARGE_INTEGER),
            ("QuotaPeakPagedPoolUsage", LARGE_INTEGER),
            ("QuotaPagedPoolUsage", LARGE_INTEGER),
            ("QuotaPeakNonPagedPoolUsage", LARGE_INTEGER),
            ("QuotaNonPagedPoolUsage", LARGE_INTEGER),
            ("PagefileUsage", LARGE_INTEGER),
            ("PeakPagefileUsage", LARGE_INTEGER),
            ("PrivateUsage", LARGE_INTEGER),
        ]

    class PROCESS_BASIC_INFORMATION(Structure):
        _fields_ = [
            ("Reserved1", HANDLE),
            ("PebBaseAddress", HANDLE),
            ("Reserved2", HANDLE * 2),
            ("UniqueProcessId", HANDLE),
            ("InheritedFromUniqueProcessId", HANDLE),
        ]

    psapi.EnumProcesses.argtypes = [POINTER(DWORD), DWORD, POINTER(DWORD)]
    psapi.EnumProcesses.restype = BOOL
    psapi.EnumProcessModules.argtypes = [HANDLE, POINTER(HANDLE), DWORD, POINTER(DWORD)]
    psapi.EnumProcessModules.restype = BOOL
    psapi.GetModuleBaseNameW.argtypes = [HANDLE, HANDLE, c_wchar_p, DWORD]
    psapi.GetModuleBaseNameW.restype = DWORD
    psapi.GetModuleFileNameExW.argtypes = [HANDLE, HANDLE, c_wchar_p, DWORD]
    psapi.GetModuleFileNameExW.restype = DWORD
    psapi.GetProcessMemoryInfo.argtypes = [HANDLE, POINTER(PROCESS_MEMORY_COUNTERS_EX), DWORD]
    psapi.GetProcessMemoryInfo.restype = BOOL
    kernel32.GetProcessTimes.argtypes = [
        HANDLE,
        POINTER(LARGE_INTEGER),
        POINTER(LARGE_INTEGER),
        POINTER(LARGE_INTEGER),
        POINTER(LARGE_INTEGER),
    ]
    kernel32.GetProcessTimes.restype = BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [HANDLE, DWORD, c_wchar_p, POINTER(DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = BOOL
    kernel32.GetProcessId.argtypes = [HANDLE]
    kernel32.GetProcessId.restype = DWORD

    # NtQueryInformationProcess is an undocumented-but-stable ntdll export used
    # for the parent process id. It is the same call psutil makes for `ppid`.
    ntdll = windll.ntdll
    ntdll.NtQueryInformationProcess.argtypes = [
        HANDLE,
        DWORD,
        POINTER(PROCESS_BASIC_INFORMATION),
        DWORD,
        POINTER(DWORD),
    ]
    ntdll.NtQueryInformationProcess.restype = DWORD
else:
    from ctypes.wintypes import LARGE_INTEGER  # noqa: F401  (used by type hints below)
    MEMORYSTATUSEX = SYSTEM_POWER_STATUS = None  # type: ignore[assignment,misc]
    PROCESS_MEMORY_COUNTERS_EX = PROCESS_BASIC_INFORMATION = None  # type: ignore[assignment,misc]


def _ntdll_query_process_basic_information(handle, info) -> int:
    """Call NtQueryInformationProcess with ProcessBasicInformation.

    Returns the NTSTATUS: 0 (STATUS_SUCCESS) on success. Lives outside the
    class so the module stays importable on a non-Windows host, where the
    caller never reaches it.
    """
    if not _IS_WINDOWS:
        return -1
    returned = DWORD(0)
    return int(ntdll.NtQueryInformationProcess(handle, DWORD(_ProcessBasicInformation), byref(info), DWORD(64), byref(returned)))


# An alias used by the collector so the call site reads like a function call.
ntdll_query_process_basic_information = _ntdll_query_process_basic_information

_SYSTEM_DRIVE = (os.environ.get("SystemDrive") or "C:") + "\\"
_ROOT = "/" if not _IS_WINDOWS else _SYSTEM_DRIVE
_CONNECTIVITY_PROBE = ("1.1.1.1", 53)


class WindowsCollector(PlatformCollector):
    """Read-only Windows telemetry collector."""

    def __init__(self, log: Logger) -> None:
        self.log = log
        self._native = _IS_WINDOWS
        self._prev_cpu = self._read_cpu_times()
        # pid -> (cpu seconds, monotonic seconds) for instantaneous CPU%
        self._proc_cpu_cache: dict[int, tuple[float, float]] = {}
        # Phase 4A: read-only storage analyzer; off-Windows it reports empty.
        self._storage: "WindowsStorageCollector | None" = None
        self._net_previous: tuple[int, int, float] | None = None

    # ---------------- identification ----------------
    def get_system_info(self) -> dict:
        info = {
            "agent_version": AGENT_VERSION,
            "platform": "windows",
            "os": self._pretty_os(),
            "os_version": self._os_version(),
            "hostname": socket.gethostname(),
            "kernel_version": platform.version(),
            "cpu_model": self._cpu_model(),
            "cpu_cores": self._cpu_cores(),
        }
        memory_mb = self._total_memory_mb()
        if memory_mb is not None:
            info["total_memory_mb"] = memory_mb
        storage_gb = self._total_storage_gb()
        if storage_gb is not None:
            info["total_storage_gb"] = storage_gb
        return info

    # ---------------- CPU ----------------
    def get_cpu_usage(self) -> Optional[float]:
        if _HAS_PSUTIL:
            try:
                return round(psutil.cpu_percent(interval=0.4), 1)
            except Exception as exc:  # noqa: BLE001 - fall back to GetSystemTimes
                self.log.debug("psutil cpu failed, using GetSystemTimes", error=str(exc))

        if not self._native:
            return None
        now = self._read_cpu_times()
        prev = self._prev_cpu
        self._prev_cpu = now
        if not now or not prev:
            return None
        idle_now, total_now = now
        idle_prev, total_prev = prev
        elapsed_total = total_now - total_prev
        elapsed_idle = idle_now - idle_prev
        if elapsed_total <= 0:
            return None
        usage = max(0.0, min(100.0, (1.0 - elapsed_idle / elapsed_total) * 100.0))
        return round(usage, 1)

    def get_processes(self) -> list[dict]:
        if _HAS_PSUTIL:
            try:
                procs = []
                for proc in psutil.process_iter(attrs=["pid", "name", "cpu_percent", "memory_percent"]):
                    attrs = proc.info
                    procs.append(
                        {
                            "pid": attrs.get("pid"),
                            "name": attrs.get("name") or "unknown",
                            "cpu": round(float(attrs.get("cpu_percent") or 0.0), 1),
                            "ram": round(float(attrs.get("memory_percent") or 0.0), 1),
                        }
                    )
                procs.sort(key=lambda item: item["ram"], reverse=True)
                return procs[:10]
            except Exception as exc:  # noqa: BLE001
                self.log.debug("psutil processes failed", error=str(exc))
        return []

    # ---------------- process telemetry ----------------
    def get_process_list(self, limit: int, include_command_line: bool = False) -> list[ProcessInfo]:
        """Enumerate processes, most expensive first, capped at `limit`.

        psutil first, then the native `EnumProcesses` + `GetProcessMemoryInfo`
        + `GetProcessTimes` path. Neither spawns anything — there is no WMI
        pipe, no PowerShell, no `tasklist` subprocess.
        """
        cap = max(1, min(int(limit or 64), 512))
        if _HAS_PSUTIL:
            try:
                entries = self._processes_psutil(include_command_line)
            except Exception as exc:  # noqa: BLE001 - fall back to Win32
                self.log.debug("psutil process list failed, using Win32", error=str(exc))
                entries = self._processes_native(include_command_line)
        else:
            entries = self._processes_native(include_command_line)
        entries.sort(key=self._process_weight, reverse=True)
        return entries[:cap]

    def get_storage_summary(self, device_id: str) -> StorageSummary:
        """Read-only disk breakdown (Phase 4A). Off-Windows: honest empty."""
        if self._storage is None:
            self._storage = WindowsStorageCollector(self.log)
        return self._storage.summarize(device_id)

    def _process_weight(self, proc: ProcessInfo) -> float:
        """Ranking key: CPU plus a memory contribution, both in [0, 100]."""
        return proc.cpu + 10.0 * (proc.memory_percent or 0.0)

    def _processes_psutil(self, include_command_line: bool) -> list[ProcessInfo]:
        entries: list[ProcessInfo] = []
        for proc in psutil.process_iter(
            attrs=[
                "pid",
                "name",
                "cpu_percent",
                "memory_percent",
                "memory_info",
                "num_threads",
                "ppid",
                "status",
                "exe",
                "username",
                "create_time",
                "cmdline",
            ]
        ):
            info = proc.info
            try:
                name = (info.get("name") or "").strip() or "unknown"
                entry = ProcessInfo(
                    pid=int(info.get("pid") or -1),
                    name=name[:MAX_NAME_LENGTH],
                    cpu=max(0.0, min(100.0, float(info.get("cpu_percent") or 0.0))),
                    memory_percent=_clamp_percent(info.get("memory_percent")),
                )
                memory_info = info.get("memory_info")
                if memory_info is not None:
                    rss = getattr(memory_info, "rss", None)
                    if rss:
                        entry.memory_mb = round(float(rss) / (1024 * 1024), 1)
                if info.get("num_threads") is not None:
                    entry.threads = int(info["num_threads"])
                if info.get("ppid") is not None:
                    entry.parent_pid = int(info["ppid"])
                entry.status = normalize_status(info.get("status"))
                entry.executable_path = _trim_path(info.get("exe"))
                entry.username = _trim(info.get("username"), MAX_USERNAME_LENGTH)
                create_time = info.get("create_time")
                if create_time:
                    entry.started_at = (
                        datetime.fromtimestamp(float(create_time), tz=timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                if include_command_line:
                    entry.command_line = _trim(" ".join(info.get("cmdline") or []), MAX_COMMAND_LINE_LENGTH)
                entries.append(entry)
            except (TypeError, ValueError, OSError):
                continue
        return entries

    def _processes_native(self, include_command_line: bool) -> list[ProcessInfo]:
        """Win32 enumeration via EnumProcesses — no shell, no WMI, no PS."""
        if not self._native:
            # Off-Windows there is genuinely nothing to report. Returning an
            # empty list keeps the contract honest instead of fabricating.
            return []
        total_physical = self._total_physical_bytes()
        entries: list[ProcessInfo] = []
        for pid in self._enum_pids():
            handle = self._open_process(pid)
            if not handle:
                continue
            try:
                name = self._module_base_name(handle) or "unknown"
                entry = ProcessInfo(
                    pid=pid,
                    name=name[:MAX_NAME_LENGTH],
                    cpu=self._process_cpu_percent(pid, handle),
                )
                working_set = self._working_set_bytes(handle)
                if working_set is not None:
                    entry.memory_mb = round(working_set / (1024 * 1024), 1)
                    if total_physical:
                        entry.memory_percent = round(
                            max(0.0, min(100.0, working_set / float(total_physical) * 100.0)), 1
                        )
                threads, kernel_time, user_time = self._process_times(handle)
                if threads:
                    entry.threads = threads
                parent = self._parent_pid(handle)
                if parent:
                    entry.parent_pid = parent
                # The status verbs are derived from the CPU times: a process
                # with no accumulated kernel or user time since the last sample
                # is idle on Windows rather than a Linux-style "sleeping".
                entry.status = "running" if (kernel_time or 0) + (user_time or 0) > 0 else "idle"
                entry.executable_path = _trim_path(self._full_image_name(handle))
                if include_command_line:
                    entry.command_line = _trim(self._command_line(handle), MAX_COMMAND_LINE_LENGTH)
                started = self._creation_time(handle)
                if started:
                    entry.started_at = started
                entries.append(entry)
            finally:
                kernel32.CloseHandle(handle)
        return entries

    def _enum_pids(self) -> list[int]:
        """All process ids via EnumProcesses, growing the buffer until it fits."""
        size = 1024
        for _ in range(8):
            buffer = (DWORD * size)()
            needed = DWORD(0)
            try:
                if not psapi.EnumProcesses(byref(buffer), DWORD(size * 4), byref(needed)):
                    return []
            except Exception as exc:  # noqa: BLE001
                self.log.debug("EnumProcesses failed", error=str(exc))
                return []
            count = needed.value // 4
            if count < size:
                return [int(buffer[i]) for i in range(count)]
            size *= 2
        return []

    def _open_process(self, pid: int):
        try:
            handle = kernel32.OpenProcess(
                _PROCESS_QUERY_LIMITED | _PROCESS_VM_READ, BOOL(False), DWORD(pid)
            )
        except Exception as exc:  # noqa: BLE001
            self.log.debug("OpenProcess failed", pid=pid, error=str(exc))
            return None
        return handle or None

    def _module_base_name(self, handle) -> Optional[str]:
        module = HANDLE()
        needed = DWORD(0)
        try:
            if not psapi.EnumProcessModules(handle, byref(module), DWORD(8), byref(needed)):
                return None
        except Exception as exc:  # noqa: BLE001
            self.log.debug("EnumProcessModules failed", error=str(exc))
            return None
        buf = create_string_buffer(512 * 2)
        try:
            written = psapi.GetModuleBaseNameW(handle, module, buf, DWORD(512))
        except Exception as exc:  # noqa: BLE001
            self.log.debug("GetModuleBaseNameW failed", error=str(exc))
            return None
        if not written:
            return None
        return buf.value.decode("utf-16-le", errors="replace").strip("\x00") or None

    def _full_image_name(self, handle) -> Optional[str]:
        buf = create_string_buffer(1024 * 2)
        size = DWORD(1024)
        try:
            if not kernel32.QueryFullProcessImageNameW(handle, DWORD(0), buf, byref(size)):
                return None
        except Exception as exc:  # noqa: BLE001
            self.log.debug("QueryFullProcessImageNameW failed", error=str(exc))
            return None
        return buf.value.decode("utf-16-le", errors="replace").strip("\x00") or None

    def _working_set_bytes(self, handle) -> Optional[int]:
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = DWORD(sys.getsizeof(PROCESS_MEMORY_COUNTERS_EX))
        try:
            if not psapi.GetProcessMemoryInfo(handle, byref(counters), counters.cb):
                return None
        except Exception as exc:  # noqa: BLE001
            self.log.debug("GetProcessMemoryInfo failed", error=str(exc))
            return None
        value = counters.WorkingSetSize
        return int(value) if value else None

    def _process_times(self, handle) -> tuple[Optional[int], Optional[int], Optional[int]]:
        """(thread count, kernel 100ns, user 100ns) — or Nones.

        The thread count is not available on the native path without walking
        the toolhelp snapshot; `None` here means the field is simply omitted
        from the snapshot. Kernel/user times come from `GetProcessTimes`.
        """
        creation = LARGE_INTEGER()
        exit_time = LARGE_INTEGER()
        kernel = LARGE_INTEGER()
        user = LARGE_INTEGER()
        try:
            if not kernel32.GetProcessTimes(handle, byref(creation), byref(exit_time), byref(kernel), byref(user)):
                return None, None, None
        except Exception as exc:  # noqa: BLE001
            self.log.debug("GetProcessTimes failed", error=str(exc))
            return None, None, None
        return None, int(kernel.value or 0), int(user.value or 0)

    def _parent_pid(self, handle) -> Optional[int]:
        info = PROCESS_BASIC_INFORMATION()
        try:
            status = ntdll_query_process_basic_information(handle, byref(info))
        except Exception as exc:  # noqa: BLE001
            self.log.debug("NtQueryInformationProcess failed", error=str(exc))
            return None
        if status != 0:
            return None
        value = info.InheritedFromUniqueProcessId
        return int(value) if value else None

    def _creation_time(self, handle) -> Optional[str]:
        creation = LARGE_INTEGER()
        exit_time = LARGE_INTEGER()
        kernel = LARGE_INTEGER()
        user = LARGE_INTEGER()
        try:
            if not kernel32.GetProcessTimes(handle, byref(creation), byref(exit_time), byref(kernel), byref(user)):
                return None
        except Exception as exc:  # noqa: BLE001
            self.log.debug("GetProcessTimes failed", error=str(exc))
            return None
        if not creation.value:
            return None
        # FILETIME is 100ns intervals since 1601-01-01 UTC. Convert to Unix
        # epoch before formatting, so startedAt is a real UTC instant.
        unix_seconds = (int(creation.value) - 116_444_736_000_000_000) / 10_000_000.0
        return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    def _command_line(self, handle) -> Optional[str]:
        # Reading the PEB command line requires PROCESS_VM_READ plus a
        # 32/64-bit-aware walk of the process block. That is a real
        # implementation cost for a field Phase 3A leaves off by default, so
        # the native path reports it as unavailable rather than half-doing it.
        return None

    def _total_physical_bytes(self) -> Optional[int]:
        status = self._memory_status()
        if status is None:
            return None
        total = status.ullTotalPhys
        return int(total) if total else None

    def _process_cpu_percent(self, pid: int, handle) -> float:
        """Instantaneous CPU% for one pid against the previous sample."""
        kernel = LARGE_INTEGER()
        user = LARGE_INTEGER()
        try:
            if not kernel32.GetProcessTimes(
                handle, LARGE_INTEGER(), LARGE_INTEGER(), byref(kernel), byref(user)
            ):
                return 0.0
        except Exception:  # noqa: BLE001
            return 0.0
        total = (int(kernel.value or 0) + int(user.value or 0)) / 10_000_000.0
        previous = self._proc_cpu_cache.get(pid)
        now = time.monotonic()
        self._proc_cpu_cache[pid] = (total, now)
        if previous is None:
            return 0.0
        prev_cpu, prev_wall = previous
        elapsed = max(0.0, total - prev_cpu)
        wall = max(0.0, now - prev_wall)
        if wall <= 0.0:
            return 0.0
        return round(max(0.0, min(100.0, elapsed / wall * 100.0)), 1)

    # ---------------- memory ----------------
    def get_memory_usage(self) -> Optional[float]:
        if _HAS_PSUTIL:
            try:
                return round(psutil.virtual_memory().percent, 1)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("psutil memory failed, using GlobalMemoryStatusEx", error=str(exc))

        status = self._memory_status()
        if status is None:
            return None
        load = status.dwMemoryLoad
        if load is None:
            return None
        return round(float(load), 1)

    # ---------------- storage ----------------
    def get_storage_usage(self) -> Optional[float]:
        total, _, used = self._disk_usage(_ROOT)
        if total is None or total <= 0:
            return None
        return round(used / total * 100.0, 1)

    # ---------------- battery ----------------
    def get_battery_status(self) -> Optional[float]:
        if _HAS_PSUTIL:
            try:
                battery = psutil.sensors_battery()
                if battery is not None:
                    return round(battery.percent, 1)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("psutil battery failed, using GetSystemPowerStatus", error=str(exc))

        status = self._power_status()
        if status is None:
            return None
        percent = status.BatteryLifePercent
        # 255 means "unknown" on Windows.
        if percent is None or percent == 255:
            return None
        return round(float(percent), 1)

    # ---------------- network ----------------
    def get_network_status(self) -> Optional[NetworkStatus]:
        connected = self._is_connected()
        if _HAS_PSUTIL:
            try:
                stats = psutil.net_if_stats()
                for name, stat in stats.items():
                    if stat.isup:
                        return {"connected": connected, "interface": name}
            except Exception as exc:  # noqa: BLE001
                self.log.debug("psutil net failed", error=str(exc))
        return {"connected": connected}

    def get_hardware_telemetry(self) -> dict:
        """Native Win32/psutil values only. GPU/fan sensors stay unavailable."""
        available = None
        if _HAS_PSUTIL:
            try: available = int(psutil.virtual_memory().available / (1024 * 1024))
            except Exception: pass
        battery = self.get_battery_status()
        charging = self._power_status()
        result = {"capabilities": {"gpu": False, "gpuVram": False, "cpuTemperature": False, "gpuTemperature": False, "fanSpeed": False, "battery": battery is not None, "charging": charging is not None}}
        if available is not None: result["ramAvailableMb"] = available
        if charging is not None:
            result["batteryStatus"] = "charging" if charging.ACLineStatus == 1 else "discharging" if charging.ACLineStatus == 0 else "unknown"
        return result

    def get_network_telemetry(self) -> dict:
        connected = self._is_connected()
        interface = None; address = None
        if _HAS_PSUTIL:
            try:
                for name, stat in psutil.net_if_stats().items():
                    if stat.isup:
                        interface = name
                        for item in psutil.net_if_addrs().get(name, []):
                            if item.family == socket.AF_INET and not item.address.startswith("127."): address = item.address; break
                        break
            except Exception: pass
        result = {"capabilities": {"gateway": False, "dns": False, "wifiSignal": False, "latency": False, "packetLoss": False, "throughput": False}, "connected": connected}
        if interface: result["interface"] = interface; result["interfaceType"] = "unknown"
        if address: result["ipAddress"] = address
        return result

    # ---------------- uptime ----------------
    def get_uptime(self) -> Optional[int]:
        if _HAS_PSUTIL:
            try:
                return max(0, int(time.time() - psutil.boot_time()))
            except Exception as exc:  # noqa: BLE001
                self.log.debug("psutil boot time failed, using GetTickCount64", error=str(exc))
        if not self._native:
            return None
        try:
            return max(0, int(kernel32.GetTickCount64() / 1000))
        except Exception as exc:  # noqa: BLE001
            self.log.debug("GetTickCount64 failed", error=str(exc))
        return None

    # ---------------- native helpers ----------------
    def _read_cpu_times(self) -> Optional[tuple[float, float]]:
        """(idle, total) in 100-ns units via GetSystemTimes."""
        if not self._native:
            return None
        idle, kernel, user = LARGE_INTEGER(), LARGE_INTEGER(), LARGE_INTEGER()
        try:
            if not kernel32.GetSystemTimes(byref(idle), byref(kernel), byref(user)):
                return None
        except Exception as exc:  # noqa: BLE001
            self.log.debug("GetSystemTimes failed", error=str(exc))
            return None
        total = kernel.value + user.value
        if total <= 0:
            return None
        return float(idle.value), float(total)

    def _memory_status(self) -> Optional[MEMORYSTATUSEX]:
        if not self._native:
            return None
        status = MEMORYSTATUSEX()
        status.dwLength = DWORD(sys.getsizeof(MEMORYSTATUSEX))
        try:
            if not kernel32.GlobalMemoryStatusEx(byref(status)):
                return None
        except Exception as exc:  # noqa: BLE001
            self.log.debug("GlobalMemoryStatusEx failed", error=str(exc))
            return None
        return status

    def _disk_usage(self, drive: str) -> tuple[Optional[int], Optional[int], Optional[int]]:
        """(total, free, used) in bytes for the given drive."""
        if _HAS_PSUTIL:
            try:
                usage = psutil.disk_usage(drive)
                return usage.total, usage.free, usage.used
            except Exception as exc:  # noqa: BLE001
                self.log.debug("psutil disk usage failed, using GetDiskFreeSpaceExW", error=str(exc))
        if not self._native:
            try:
                usage = shutil.disk_usage(drive)
                return usage.total, usage.free, usage.used
            except OSError as exc:
                self.log.debug("disk usage unavailable", error=str(exc))
                return None, None, None
        free, total, _ = LARGE_INTEGER(), LARGE_INTEGER(), LARGE_INTEGER()
        try:
            if not kernel32.GetDiskFreeSpaceExW(drive, byref(free), byref(total), byref(None)):
                return None, None, None
        except Exception as exc:  # noqa: BLE001
            self.log.debug("GetDiskFreeSpaceExW failed", error=str(exc))
            return None, None, None
        if total.value <= 0:
            return None, None, None
        return total.value, free.value, total.value - free.value

    def _power_status(self) -> Optional[SYSTEM_POWER_STATUS]:
        if not self._native:
            return None
        status = SYSTEM_POWER_STATUS()
        try:
            if not kernel32.GetSystemPowerStatus(byref(status)):
                return None
        except Exception as exc:  # noqa: BLE001
            self.log.debug("GetSystemPowerStatus failed", error=str(exc))
            return None
        return status

    def _total_memory_mb(self) -> Optional[int]:
        status = self._memory_status()
        if status is None:
            return None
        total = status.ullTotalPhys
        if total is None or total <= 0:
            return None
        return int(total / (1024 * 1024))

    def _total_storage_gb(self) -> Optional[int]:
        total, _, _ = self._disk_usage(_ROOT)
        if total is None:
            return None
        return int(total / (1024**3))

    def _is_connected(self) -> bool:
        try:
            with socket.create_connection(_CONNECTIVITY_PROBE, timeout=2):
                return True
        except OSError:
            return False

    # ---------------- stdlib identification helpers ----------------
    def _pretty_os(self) -> str:
        version = self._os_version()
        if version:
            return version
        return platform.platform()

    def _os_version(self) -> Optional[str]:
        try:
            release = platform.release()
            version = platform.version()
        except Exception:  # noqa: BLE001
            return None
        if not release:
            return None
        return f"Windows {release}" + (f" ({version})" if version else "")

    def _cpu_model(self) -> Optional[str]:
        try:
            return platform.processor() or None
        except Exception:  # noqa: BLE001
            return None

    def _cpu_cores(self) -> Optional[int]:
        count = os.cpu_count()
        return count if count else None


# ---------------- process helpers ----------------


def _clamp_percent(value: object) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:  # NaN
        return None
    return round(max(0.0, min(100.0, numeric)), 1)


def _trim(value: object, limit: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _trim_path(value: object) -> Optional[str]:
    return _trim(value, MAX_PATH_LENGTH)
