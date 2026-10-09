"""
VIGILIA Windows agent — application control (Phase 3B).

The same three commands as the Linux agent, implemented with Win32 APIs
through ctypes. No PowerShell, no `cmd.exe`, no `tasklist`, no shell of any
kind:

  APPLICATION_OPEN    ShellExecuteExW with a fixed verb ("open") and a fixed
                      path resolved from the registry — never from the request
  APPLICATION_CLOSE   OpenProcess + TerminateProcess on pids matched by the
                      process name, then verify the handles are gone
  APPLICATION_RESTART CLOSE then OPEN

Why these APIs:

- `ShellExecuteExW` is the documented "launch an application" entry point. It
  resolves the application through the shell's own file association, so the
  agent does not have to second-guess where the binary lives on this user's
  machine, and the request cannot choose the executable.
- `TerminateProcess` is the Win32 equivalent of SIGKILL; Windows has no
  SIGTERM analogue for a GUI process. It is still a deliberate, per-pid call
  on processes matched by name, never a wildcard `taskkill /F /IM *`.
- `CreateToolhelp32Snapshot` + `Process32First/Next` enumerates processes
  without `tasklist` or a WMI pipe.

Importable on Linux: every native call is guarded by `_IS_WINDOWS`, and the
default path reports `failed` with an explicit reason off-Windows rather than
pretending. Nothing is faked — an injected `native` double is the only way a
Windows action "runs" on a non-Windows host.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional

from agents.common.applications import ApplicationCatalog, ApplicationDescriptor
from agents.common.logger import Logger
from agents.common.processes import ProcessInfo

_IS_WINDOWS = sys.platform == "win32"

#: Seconds to wait for a terminated process to disappear.
CLOSE_SETTLE_SECONDS = 3.0
CLOSE_POLL_INTERVAL = 0.2
CLOSE_PROCESS_CAP = 128


@dataclass
class ApplicationOutcome:
    """Result of one application command."""

    request_id: str
    command: str
    target: Optional[str]
    status: str = "succeeded"
    result: Optional[str] = None
    duration_ms: Optional[int] = None


class WindowsApplicationController:
    """Open / close / restart applications via Win32 APIs."""

    def __init__(
        self,
        log: Logger,
        catalog: ApplicationCatalog,
        enumerate_processes: Optional[Callable[[], list[ProcessInfo]]] = None,
        native: Optional[Callable[[str, str, str], tuple[bool, int]]] = None,
    ) -> None:
        self.log = log
        self._catalog = catalog
        self._enumerate = enumerate_processes or _enumerate_processes
        # `native(action, application_id, resolved_path) -> (ok, win32_error)`
        # is injected by tests; the default path requires a real Windows host.
        self._native = native
        self._native_injected = native is not None

    def supported_commands(self) -> tuple[str, ...]:
        return ("APPLICATION_OPEN", "APPLICATION_CLOSE", "APPLICATION_RESTART")

    def execute(self, request_id: str, command: str, target: Optional[str]) -> ApplicationOutcome:
        if command not in self.supported_commands():
            self.log.warn("unsupported application command", request_id=request_id, command=command)
            return ApplicationOutcome(
                request_id, command, target, status="failed", result="unsupported command"
            )

        app = self._catalog.find(target)
        if app is None:
            self.log.warn("application target is not allowed",
                          request_id=request_id, command=command, target=target)
            return ApplicationOutcome(
                request_id, command, target, status="failed",
                result=f"application {target!r} is not allowed on this device",
            )

        if not _IS_WINDOWS and not self._native_injected:
            # Honest, not faked: a Windows application action cannot run here.
            return ApplicationOutcome(
                request_id, command, app.application_id, status="failed",
                result="Windows application command executed on a non-Windows host",
            )

        started = time.monotonic()
        try:
            if command == "APPLICATION_OPEN":
                detail = self._open(app)
            elif command == "APPLICATION_CLOSE":
                detail = self._close(app)
            else:
                detail = self._restart(app)
        except Exception as exc:  # noqa: BLE001 - surface every failure to the caller
            return ApplicationOutcome(
                request_id, command, app.application_id, status="failed",
                result=f"application control error: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        status, message = detail
        elapsed = int((time.monotonic() - started) * 1000)
        return ApplicationOutcome(
            request_id, command, app.application_id, status=status, result=message,
            duration_ms=elapsed,
        )

    # ---------------- open ----------------
    def _open(self, app: ApplicationDescriptor) -> tuple[str, str]:
        path = _windows_target_path(app)
        if path is None:
            return "failed", f"{app.application_id} is not launchable from this agent"
        if self._is_running(app.application_id):
            return "succeeded", f"{app.application_id} is already running"
        ok, error = self._call_native("open", app.application_id, path)
        if not ok:
            return "failed", f"ShellExecuteExW failed with error {error} for {app.application_id}"
        return "succeeded", f"{app.application_id} launched"

    # ---------------- close ----------------
    def _close(self, app: ApplicationDescriptor) -> tuple[str, str]:
        pids = self._matching_pids(app.application_id)
        if not pids:
            return "succeeded", f"{app.application_id} is not running"

        terminated = 0
        for pid in pids:
            ok, _error = self._call_native("close", app.application_id, str(pid))
            if ok:
                terminated += 1

        if not self._wait_until_gone(app.application_id, pids):
            still = sorted(set(pids) & set(self._matching_pids(app.application_id)))
            return "failed", (
                f"{app.application_id}: {terminated} process(es) terminated, "
                f"{still} still running after {CLOSE_SETTLE_SECONDS:.0f}s"
            )
        return "succeeded", f"{app.application_id}: {terminated} process(es) closed"

    # ---------------- restart ----------------
    def _restart(self, app: ApplicationDescriptor) -> tuple[str, str]:
        close_status, close_message = self._close(app)
        if close_status != "succeeded":
            return "failed", f"restart aborted at close: {close_message}"
        time.sleep(0.4)
        open_status, open_message = self._open(app)
        if open_status != "succeeded":
            return "failed", f"restart closed but failed to reopen: {open_message}"
        return "succeeded", f"{app.application_id} restarted"

    # ---------------- helpers ----------------
    def _call_native(self, action: str, application_id: str, argument: str) -> tuple[bool, int]:
        if self._native is not None:
            return self._native(action, application_id, argument)
        return _call_native(action, application_id, argument)

    def _is_running(self, application_id: str) -> bool:
        return bool(self._matching_pids(application_id))

    def _matching_pids(self, application_id: str) -> list[int]:
        """Pids whose process name is this application (case-insensitive).

        A zombie is excluded: the process is gone, it only has not been reaped
        yet, and treating it as "running" would misreport a later OPEN or
        CLOSE.
        """
        wanted = _process_name_key(application_id)
        pids: list[int] = []
        for entry in self._enumerate():
            if len(pids) >= CLOSE_PROCESS_CAP:
                break
            if entry.status == "zombie":
                continue
            if _process_name_key(entry.name) == wanted:
                pids.append(entry.pid)
        return pids

    def _wait_until_gone(self, application_id: str, pids: list[int]) -> bool:
        deadline = time.monotonic() + CLOSE_SETTLE_SECONDS
        while time.monotonic() < deadline:
            remaining = set(self._matching_pids(application_id))
            if not (remaining & set(pids)):
                return True
            time.sleep(CLOSE_POLL_INTERVAL)
        return False


def _process_name_key(name: str) -> str:
    key = (name or "").strip().lower()
    if key.endswith(".exe"):
        key = key[: -len(".exe")]
    return key


def _windows_target_path(app: ApplicationDescriptor) -> Optional[str]:
    """Resolve a registered application to the path the shell will launch.

    Windows resolves these through the shell: `notepad` is `notepad.exe` on
    `PATH`, and the modern apps (`calc`, `mspaint`) open through their
    registered protocol. The id therefore stays the only thing the mobile
    client names; the path here is derived from the id, not received.
    """
    app_id = app.application_id.lower()
    shell_targets = {
        "notepad": "notepad.exe",
        "calc": "calc.exe",
        "mspaint": "mspaint.exe",
        "write": "write.exe",
        "explorer": "explorer.exe",
    }
    if app_id in shell_targets:
        return shell_targets[app_id]
    if app.launch_argv:
        return app.launch_argv[0]
    return None


def _enumerate_processes() -> list[ProcessInfo]:
    """Live process list via the Windows collector (native, no tasklist)."""
    from agents.windows.src.collectors import WindowsCollector
    from agents.common.logger import Logger as _Logger

    collector = WindowsCollector(_Logger(quiet=True))
    return collector.get_process_list(limit=CLOSE_PROCESS_CAP, include_command_line=False)


def _call_native(action: str, application_id: str, argument: str) -> tuple[bool, int]:
    """Invoke the Win32 entry point for an application action. Windows only."""
    from ctypes import windll

    if action == "open":
        from ctypes import wintypes

        # SHELLEXECUTEINFOW; cbSize must be the byte length of the struct.
        class SHELLEXECUTEINFOW(wintypes.Structure):  # type: ignore[misc]
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", wintypes.ULONG),
                ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR),
                ("lpFile", wintypes.LPCWSTR),
                ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR),
                ("nShow", wintypes.INT),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", wintypes.LPVOID),
                ("lpClass", wintypes.LPCWSTR),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIcon", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        SEE_MASK_NOCLOSEPROCESS = 0x00000040
        SW_SHOWNORMAL = 1
        info = SHELLEXECUTEINFOW()
        # ctypes can measure the struct itself; this is the documented way to
        # fill cbSize and avoids hard-coding a version-specific constant.
        from ctypes import sizeof as _sizeof

        info.cbSize = wintypes.DWORD(_sizeof(info))
        info.fMask = SEE_MASK_NOCLOSEPROCESS
        info.hwnd = None
        info.lpVerb = "open"
        info.lpFile = argument
        info.lpParameters = None
        info.lpDirectory = None
        info.nShow = SW_SHOWNORMAL
        shell32 = windll.shell32
        ok = bool(shell32.ShellExecuteExW(wintypes.byref(info)))
        return ok, windll.kernel32.GetLastError()

    if action == "close":
        kernel32 = windll.kernel32
        PROCESS_TERMINATE = 0x0001
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(argument))
        if not handle:
            return False, kernel32.GetLastError()
        try:
            ok = bool(kernel32.TerminateProcess(handle, 0))
            return ok, kernel32.GetLastError()
        finally:
            kernel32.CloseHandle(handle)

    return False, 0


__all__ = [
    "WindowsApplicationController",
    "ApplicationOutcome",
    "CLOSE_SETTLE_SECONDS",
    "CLOSE_PROCESS_CAP",
]
