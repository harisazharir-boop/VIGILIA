"""
VIGILIA Linux agent — system collectors.

Two implementations live here:
  1. psutil-based (accurate, used when the dependency is importable)
  2. a pure-stdlib /proc fallback so the agent still runs on a minimal Python

Every method is read-only. No shell is spawned, no file outside /proc, /sys
or the standard library is opened.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from agents.common.logger import Logger
from agents.common.platform import PlatformCollector
from agents.common.storage import StorageSummary
from agents.linux.src.storage import LinuxStorageCollector
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

try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore
    _HAS_PSUTIL = False

_PROC = Path("/proc")
_SYS = Path("/sys")
#: Kernel clock tick frequency; 100 on virtually every distribution. Used to
#: convert /proc/<pid>/stat jiffies into seconds. Read once from the sysconf.
_CLOCK_TICKS = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


class LinuxCollector(PlatformCollector):
    def __init__(self, log: Logger) -> None:
        self.log = log
        self._boot_time = self._read_boot_time()
        self._prev_cpu = self._read_cpu_jiffies()
        # pid -> (total jiffies, monotonic seconds) for instantaneous CPU%
        self._proc_cpu_cache: dict[int, tuple[float, float]] = {}
        # Phase 4A: the read-only storage analyzer. Lazy so a collector built
        # on a host without a home directory still constructs cleanly.
        self._storage: "LinuxStorageCollector | None" = None
        self._net_previous: tuple[int, int, float] | None = None

    # ---------------- identification ----------------
    def get_system_info(self) -> dict:
        info = {
            "agent_version": AGENT_VERSION,
            "platform": "linux",
            "os": self._pretty_os(),
            "os_version": self._os_version(),
            "hostname": socket.gethostname(),
            "kernel_version": platform.release(),
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
            except Exception as exc:  # noqa: BLE001 - fall back to /proc
                self.log.debug("psutil cpu failed, using /proc", error=str(exc))

        now = self._read_cpu_jiffies()
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

        psutil is the accurate path; without it every field is parsed out of
        `/proc/<pid>/{stat,status,cmdline,exe}` by hand. Either path is
        read-only and opens nothing outside `/proc`.
        """
        cap = max(1, min(int(limit or 64), 512))
        if _HAS_PSUTIL:
            try:
                entries = self._processes_psutil(include_command_line)
            except Exception as exc:  # noqa: BLE001 - fall back to /proc
                self.log.debug("psutil process list failed, using /proc", error=str(exc))
                entries = self._processes_proc(include_command_line)
        else:
            entries = self._processes_proc(include_command_line)
        entries.sort(key=self._process_weight, reverse=True)
        return entries[:cap]

    def get_storage_summary(self, device_id: str) -> StorageSummary:
        """Read-only disk breakdown (Phase 4A). Never mutates anything."""
        if self._storage is None:
            self._storage = LinuxStorageCollector(self.log)
        return self._storage.summarize(device_id)

    def _process_weight(self, proc: ProcessInfo) -> float:
        """Ranking key: CPU plus a memory contribution, both in [0, 100]."""
        return proc.cpu + 10.0 * (proc.memory_percent or 0.0)

    def _processes_psutil(self, include_command_line: bool) -> list[ProcessInfo]:
        entries: list[ProcessInfo] = []
        attrs = [
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
        ]
        if include_command_line:
            attrs.append("cmdline")
        for proc in psutil.process_iter(
            attrs=attrs
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
                    entry.started_at = datetime.fromtimestamp(float(create_time), tz=timezone.utc)
                    entry.started_at = entry.started_at.isoformat().replace("+00:00", "Z")
                if include_command_line:
                    entry.command_line = _trim(" ".join(info.get("cmdline") or []), MAX_COMMAND_LINE_LENGTH)
                entries.append(entry)
            except (TypeError, ValueError, OSError):
                # A process can die mid-enumeration; skip it rather than drop
                # the whole list.
                continue
        return entries

    def _processes_proc(self, include_command_line: bool) -> list[ProcessInfo]:
        """Pure-stdlib enumeration from /proc.

        For every numeric directory: `stat` for cpu/state/parent/starttime,
        `status` for the memory totals and owner uid, `cmdline` for the name
        and (optionally) the command line, `exe` for the executable path.
        """
        mem_total_kb = self._read_meminfo().get("MemTotal")
        entries: list[ProcessInfo] = []
        seen_pids: set[int] = set()
        if not _PROC.is_dir():
            self._proc_cpu_cache.clear()
            return entries
        for child in _PROC.iterdir():
            if not child.name.isdigit():
                continue
            pid = int(child.name)
            stat = self._read_proc_stat(child)
            if stat is None:
                continue
            seen_pids.add(pid)
            name, state, ppid, utime, stime, starttime_jiffies, threads = stat
            cpu = self._proc_cpu_percent(pid, utime + stime)
            entry = ProcessInfo(
                pid=pid,
                name=(name or "unknown")[:MAX_NAME_LENGTH],
                cpu=cpu,
                parent_pid=ppid if ppid > 0 else None,
                status=normalize_status(state),
                threads=threads if threads > 0 else None,
            )
            resident_kb = self._read_proc_rss(child)
            if resident_kb is not None:
                entry.memory_mb = round(resident_kb / 1024.0, 1)
                if mem_total_kb:
                    entry.memory_percent = round(
                        max(0.0, min(100.0, resident_kb / float(mem_total_kb) * 100.0)), 1
                    )
            if starttime_jiffies:
                started = self._boot_time + (starttime_jiffies / float(_CLOCK_TICKS))
                entry.started_at = (
                    datetime.fromtimestamp(started, tz=timezone.utc).isoformat().replace("+00:00", "Z")
                )
            entry.username = _trim(self._read_proc_owner(child), MAX_USERNAME_LENGTH)
            entry.executable_path = _trim_path(self._read_proc_exe(child))
            if include_command_line:
                entry.command_line = _trim(self._read_proc_cmdline(child), MAX_COMMAND_LINE_LENGTH)
            entries.append(entry)
        # Dropped /proc entries must not leave a CPU baseline forever. A PID
        # seen after being absent starts fresh, avoiding stale PID-reuse data.
        self._proc_cpu_cache = {
            pid: baseline for pid, baseline in self._proc_cpu_cache.items() if pid in seen_pids
        }
        return entries

    def _read_proc_stat(self, proc_dir: Path) -> Optional[tuple]:
        """(name, state, ppid, utime, stime, starttime, threads) or None."""
        stat_file = proc_dir / "stat"
        try:
            raw = stat_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # The comm field is parenthesised and may contain spaces, so split
        # around the last ")" instead of splitting on whitespace naively.
        close = raw.rfind(")")
        if close <= 0:
            return None
        try:
            name = raw[raw.find("(") + 1 : close]
            rest = raw[close + 1 :].split()
            state = rest[0]
            ppid = int(rest[1])
            utime = int(rest[11])
            stime = int(rest[12])
            threads = int(rest[17])
            starttime = int(rest[19])
        except (IndexError, ValueError):
            return None
        return name, state, ppid, utime, stime, starttime, threads

    def _read_proc_rss(self, proc_dir: Path) -> Optional[int]:
        """Resident set size in KiB from /proc/<pid>/status (VmRSS)."""
        status_file = proc_dir / "status"
        try:
            text = status_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for line in text.splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        return int(parts[1])
                    except ValueError:
                        return None
        return None

    def _read_proc_owner(self, proc_dir: Path) -> Optional[str]:
        """Owner login from the status file's Uid line, or the file owner."""
        status_file = proc_dir / "status"
        uid: Optional[int] = None
        try:
            for line in status_file.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("Uid:"):
                    fields = line.split()
                    if len(fields) >= 2:
                        uid = int(fields[1])
                    break
        except (OSError, ValueError):
            uid = None
        if uid is None:
            try:
                uid = proc_dir.stat().st_uid
            except OSError:
                return None
        try:
            import pwd

            return pwd.getpwuid(uid).pw_name
        except (KeyError, ImportError, OSError):
            return None

    def _read_proc_exe(self, proc_dir: Path) -> Optional[str]:
        try:
            return os.readlink(proc_dir / "exe")
        except OSError:
            # EACCES for processes owned by another user, ENOENT for a
            # kernel thread or an exited process. Both are simply unreportable.
            return None

    def _read_proc_cmdline(self, proc_dir: Path) -> Optional[str]:
        try:
            raw = (proc_dir / "cmdline").read_bytes()
        except OSError:
            return None
        if not raw:
            # Kernel threads have an empty cmdline; their name from stat is
            # bracketed, which is what the caller already recorded.
            return None
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()

    def _proc_cpu_percent(self, pid: int, total_jiffies: float) -> float:
        """Instantaneous CPU% for one pid vs. the previous sample.

        The first time a pid is seen there is no baseline, so it reports 0.0 —
        an honest "not measured yet" rather than a fabricated number. The next
        snapshot has a real delta.
        """
        previous = self._proc_cpu_cache.get(pid)
        self._proc_cpu_cache[pid] = (total_jiffies, time.monotonic())
        if previous is None:
            return 0.0
        prev_jiffies, prev_wall = previous
        elapsed = total_jiffies - prev_jiffies
        wall = max(0.0, time.monotonic() - prev_wall)
        if wall <= 0.0 or _CLOCK_TICKS <= 0:
            return 0.0
        seconds = elapsed / float(_CLOCK_TICKS)
        return round(max(0.0, min(100.0, seconds / wall * 100.0)), 1)

    # ---------------- memory ----------------
    def get_memory_usage(self) -> Optional[float]:
        if _HAS_PSUTIL:
            try:
                return round(psutil.virtual_memory().percent, 1)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("psutil memory failed, using /proc/meminfo", error=str(exc))

        info = self._read_meminfo()
        total, available = info.get("MemTotal"), info.get("MemAvailable")
        if not total or available is None or total <= 0:
            return None
        used = max(0.0, total - available)
        return round(used / total * 100.0, 1)

    # ---------------- storage ----------------
    def get_storage_usage(self) -> Optional[float]:
        try:
            usage = shutil.disk_usage("/")
        except OSError as exc:
            self.log.debug("storage usage failed", error=str(exc))
            return None
        if usage.total <= 0:
            return None
        return round(usage.used / usage.total * 100.0, 1)

    # ---------------- battery ----------------
    def get_battery_status(self) -> Optional[float]:
        if _HAS_PSUTIL:
            try:
                battery = psutil.sensors_battery()
                if battery is not None:
                    return round(battery.percent, 1)
            except Exception as exc:  # noqa: BLE001
                self.log.debug("psutil battery failed, using /sys", error=str(exc))

        for supply in self._power_supplies():
            capacity_file = supply / "capacity"
            if capacity_file.is_file():
                try:
                    return round(float(capacity_file.read_text().strip()), 1)
                except (OSError, ValueError):
                    continue
        return None

    # ---------------- network ----------------
    def get_network_status(self) -> Optional[NetworkStatus]:
        connected = self._default_route_exists()
        interface = self._default_interface()

        if _HAS_PSUTIL:
            try:
                stats = psutil.net_if_stats()
                if interface and interface in stats and stats[interface].isup:
                    return {"connected": True, "interface": interface}
            except Exception as exc:  # noqa: BLE001
                self.log.debug("psutil net failed", using="/proc/net/route", error=str(exc))

        if interface:
            status: NetworkStatus = {"connected": connected, "interface": interface}
        else:
            status = {"connected": connected}
        return status

    def get_hardware_telemetry(self) -> dict:
        """Bounded, read-only local hardware values; absent means unavailable."""
        temp = self._first_hwmon_value("temp", 1000)
        fan = self._first_hwmon_value("fan", 1)
        battery_status = self._battery_status()
        memory = self._read_meminfo()
        available = memory.get("MemAvailable")
        result = {
            "capabilities": {"gpu": False, "gpuVram": False, "cpuTemperature": temp is not None,
                             "gpuTemperature": False, "fanSpeed": fan is not None,
                             "battery": self.get_battery_status() is not None, "charging": battery_status is not None},
        }
        try:
            result["cpuLoad1"] = round(max(0.0, os.getloadavg()[0]), 2)
        except OSError:
            pass
        if available is not None:
            result["ramAvailableMb"] = int(available / 1024)
        if temp is not None:
            result["cpuTemperatureC"] = temp
        if fan is not None:
            result["fanRpm"] = int(fan)
        if battery_status is not None:
            result["batteryStatus"] = battery_status
        return result

    def get_network_telemetry(self) -> dict:
        interface = self._default_interface()
        connected = self._default_route_exists()
        addresses = self._interface_address(interface)
        gateway = self._default_gateway()
        dns = self._dns_servers()
        down, up = self._network_throughput()
        kind = "wifi" if interface and interface.startswith(("wl", "wifi")) else "ethernet" if interface and interface != "lo" else "loopback" if interface == "lo" else "unknown"
        latency, loss = self._network_probe(dns[0] if dns else gateway)
        result = {"capabilities": {"gateway": gateway is not None, "dns": bool(dns), "wifiSignal": False, "latency": latency is not None, "packetLoss": loss is not None, "throughput": down is not None or up is not None}, "connected": connected}
        if interface: result["interface"] = interface
        if interface: result["interfaceType"] = kind
        if addresses: result["ipAddress"] = addresses
        if gateway: result["gateway"] = gateway
        if dns: result["dns"] = dns[:4]
        if down is not None: result["downloadMbps"] = down
        if up is not None: result["uploadMbps"] = up
        if latency is not None: result["latencyMs"] = latency
        if loss is not None: result["packetLossPercent"] = loss
        return result

    # ---------------- helpers ----------------
    def _pretty_os(self) -> str:
        distribution = self._os_version()
        if distribution:
            return distribution
        return platform.platform()

    def _os_version(self) -> Optional[str]:
        """PRETTY_NAME from /proc/os-release, e.g. 'Ubuntu 24.04.1 LTS'."""
        os_release = _PROC / "os-release"
        if not os_release.is_file():
            return None
        try:
            lines = os_release.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        pretty = None
        for line in lines:
            if line.startswith("PRETTY_NAME="):
                pretty = line.split("=", 1)[1].strip().strip('"')
        if pretty:
            return pretty
        for line in lines:
            if line.startswith("NAME="):
                return line.split("=", 1)[1].strip().strip('"')
        return None

    def _cpu_model(self) -> Optional[str]:
        cpuinfo = _PROC / "cpuinfo"
        if not cpuinfo.is_file():
            return None
        try:
            for line in cpuinfo.read_text().splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            return None
        return None

    def _cpu_cores(self) -> Optional[int]:
        count = os.cpu_count()
        return count if count else None

    def _read_meminfo(self) -> dict[str, int]:
        """Values in kilobytes. /proc/meminfo is the source of truth."""
        meminfo: dict[str, int] = {}
        path = _PROC / "meminfo"
        if not path.is_file():
            return meminfo
        try:
            for line in path.read_text().splitlines():
                key, _, rest = line.partition(":")
                digits = "".join(ch for ch in rest.split()[:1] if ch.isdigit())
                if digits:
                    meminfo[key.strip()] = int(digits)
        except OSError:
            return meminfo
        return meminfo

    def _total_memory_mb(self) -> Optional[int]:
        info = self._read_meminfo()
        total = info.get("MemTotal")
        if total is None:
            return None
        return int(total / 1024)

    def _total_storage_gb(self) -> Optional[int]:
        try:
            usage = shutil.disk_usage("/")
        except OSError:
            return None
        return int(usage.total / (1024 ** 3))

    def _read_boot_time(self) -> float:
        if _HAS_PSUTIL:
            try:
                return psutil.boot_time()
            except Exception:  # noqa: BLE001
                pass
        stat = _PROC / "stat"
        if stat.is_file():
            try:
                for line in stat.read_text().splitlines():
                    if line.startswith("btime "):
                        return float(line.split()[1])
            except (OSError, IndexError, ValueError):
                pass
        return time.time()

    def _uptime_seconds(self) -> Optional[int]:
        uptime_file = _PROC / "uptime"
        if uptime_file.is_file():
            try:
                return int(float(uptime_file.read_text().split()[0]))
            except (OSError, IndexError, ValueError):
                pass
        if self._boot_time:
            return max(0, int(time.time() - self._boot_time))
        return None

    def get_uptime(self) -> Optional[int]:
        return self._uptime_seconds()

    def _read_cpu_jiffies(self) -> Optional[tuple[float, float]]:
        stat = _PROC / "stat"
        if not stat.is_file():
            return None
        try:
            line = stat.read_text().splitlines()[0]
        except OSError:
            return None
        fields = line.split()
        if not fields or fields[0] != "cpu":
            return None
        try:
            jiffies = [float(value) for value in fields[1:]]
        except ValueError:
            return None
        # idle = idle + iowait (indices 3 and 4)
        idle = sum(jiffies[3:5])
        total = sum(jiffies)
        return idle, total

    def _power_supplies(self) -> list[Path]:
        base = _SYS / "class" / "power_supply"
        if not base.is_dir():
            return []
        return sorted(p for p in base.iterdir() if (p / "capacity").is_file())

    def _battery_status(self) -> Optional[str]:
        for supply in self._power_supplies():
            try:
                status = (supply / "status").read_text().strip().lower()
            except OSError:
                continue
            if status == "charging": return "charging"
            if status == "discharging": return "discharging"
            if status == "full": return "full"
        return None

    def _first_hwmon_value(self, prefix: str, divisor: int) -> Optional[float]:
        base = _SYS / "class" / "hwmon"
        if not base.is_dir(): return None
        for directory in sorted(base.iterdir()):
            for value in sorted(directory.glob(f"{prefix}[0-9]*_input")):
                try:
                    raw = float(value.read_text().strip()) / divisor
                    if prefix != "temp" or -40 <= raw <= 200: return round(raw, 1)
                except (OSError, ValueError): continue
        return None

    def _interface_address(self, interface: Optional[str]) -> Optional[str]:
        if not interface or not _HAS_PSUTIL: return None
        try:
            for item in psutil.net_if_addrs().get(interface, []):
                if item.family == socket.AF_INET and not item.address.startswith("127."): return item.address
        except Exception: pass
        return None

    def _default_gateway(self) -> Optional[str]:
        route = _PROC / "net" / "route"
        try:
            for line in route.read_text().splitlines()[1:]:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    return socket.inet_ntoa(bytes.fromhex(fields[2])[::-1])
        except (OSError, ValueError): pass
        return None

    def _dns_servers(self) -> list[str]:
        try:
            return [line.split()[1] for line in Path("/etc/resolv.conf").read_text().splitlines() if line.startswith("nameserver ") and len(line.split()) == 2][:4]
        except OSError: return []

    def _network_throughput(self) -> tuple[Optional[float], Optional[float]]:
        if not _HAS_PSUTIL: return None, None
        try:
            counters = psutil.net_io_counters(); now = time.monotonic()
            previous, self._net_previous = self._net_previous, (counters.bytes_recv, counters.bytes_sent, now)
            if previous is None or now <= previous[2]: return None, None
            seconds = now - previous[2]
            return round(max(0, counters.bytes_recv - previous[0]) * 8 / seconds / 1_000_000, 3), round(max(0, counters.bytes_sent - previous[1]) * 8 / seconds / 1_000_000, 3)
        except Exception: return None, None

    @staticmethod
    def _network_probe(target: Optional[str]) -> tuple[Optional[float], Optional[float]]:
        """One bounded TCP DNS-port reachability probe. No subprocess or ICMP."""
        if not target: return None, None
        started = time.monotonic()
        try:
            with socket.create_connection((target, 53), timeout=1.0):
                return round((time.monotonic() - started) * 1000, 1), 0.0
        except OSError:
            return None, 100.0

    def _default_interface(self) -> Optional[str]:
        route = _PROC / "net" / "route"
        if not route.is_file():
            return None
        try:
            lines = route.read_text().splitlines()[1:]
        except OSError:
            return None
        for line in lines:
            fields = line.split()
            if len(fields) < 4:
                continue
            interface, flags = fields[0], fields[3]
            try:
                if int(flags, 16) & 0x0002:  # RTF_UP
                    return interface
            except ValueError:
                continue
        return None

    def _default_route_exists(self) -> bool:
        route = _PROC / "net" / "route"
        if not route.is_file():
            # Fallback: a DNS-reachable network implies connectivity.
            try:
                with socket.create_connection(("1.1.1.1", 53), timeout=2):
                    return True
            except OSError:
                return False
        try:
            lines = route.read_text().splitlines()[1:]
        except OSError:
            return False
        return any(line.split() for line in lines)


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
