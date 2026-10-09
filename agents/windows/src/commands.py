"""
VIGILIA Windows agent — command execution.

Executes the fixed allowlist of remote commands using Win32 APIs through
ctypes. No shell, no subprocess, no PowerShell:

  LOCK      -> user32.LockWorkStation()
  SLEEP     -> powrprof.SetSuspendState(sleep := FALSE, hibernate := FALSE, disableWake := FALSE)
  HIBERNATE -> powrprof.SetSuspendState(TRUE, FALSE, FALSE)
  RESTART   -> advapi32.InitiateSystemShutdownExW(..., rebootAfterShutdown := TRUE, ...)
  SHUTDOWN  -> advapi32.InitiateSystemShutdownExW(..., rebootAfterShutdown := FALSE, ...)

`InitiateSystemShutdownExW` requires the SE_SHUTDOWN_NAME privilege, which
the agent holds when running as a service or an elevated process. A missing
privilege or a failed call returns `failed` with the Win32 error code — never
a silent no-op.

This module is importable on Linux (the dev/test host) so the surface can be
unit-tested; every native call is guarded by `_IS_WINDOWS` and replaced by a
skipped outcome off-Windows. Nothing is faked: on Linux the command reports
`failed`, because a Windows command genuinely cannot run there.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Callable, Optional

from agents.common.logger import Logger
from agents.common.telemetry import (
    COMMAND_ALLOWLIST,
    is_allowed_command,
    is_application_command,
    is_clean_command,
)

_IS_WINDOWS = sys.platform == "win32"
COMMAND_TIMEOUT_SECONDS = 15


@dataclass
class ExecutionOutcome:
    """Result of executing one command."""
    request_id: str
    command: str
    status: str = "succeeded"
    result: Optional[str] = None
    duration_ms: Optional[int] = None
    #: Application target for `APPLICATION_*` commands, else `None`.
    target: Optional[str] = None


class WindowsCommandExecutor:
    """Runs the fixed allowlist of commands via Win32 APIs."""

    def __init__(self, log: Logger, native: Optional[Callable[[str], tuple[bool, int]]] = None,
                 application_controller: Optional[object] = None,
                  cleaner: Optional[object] = None) -> None:
        self.log = log
        # `native(command) -> (ok, win32_error)` is injected by tests.
        self._native = native if native is not None else _call_native
        # An injected entry point is a test double the suite controls, so it
        # runs off-Windows too. The default path still requires a real host.
        self._native_injected = native is not None
        # Application commands (Phase 3B) are served by a separate controller
        # with the same allowlist discipline. Injected by tests.
        self._applications = application_controller
        # Cleanup commands (Phase 4B) are served by the structured cleaner.
        # Injected by tests; a separate object so the native power-command
        # path cannot reach the deletion code.
        self._cleaner = cleaner

    def supported_commands(self) -> tuple[str, ...]:
        return COMMAND_ALLOWLIST

    def execute(self, request_id: str, command: str,
                target: Optional[str] = None) -> ExecutionOutcome:
        if not is_allowed_command(command):
            if is_clean_command(command):
                return self._execute_cleanup(request_id, command)
            self.log.warn("rejected unknown command", request_id=request_id, command=command)
            return ExecutionOutcome(request_id, command, status="failed", result="unsupported command")

        if is_application_command(command):
            return self._execute_application(request_id, command, target)

        if not _IS_WINDOWS and not self._native_injected:
            # Honest, not faked: the Windows action cannot run off-Windows.
            return ExecutionOutcome(
                request_id, command, status="failed",
                result="Windows command executed on a non-Windows host",
            )

        import time

        started = time.monotonic()
        try:
            ok, error_code = self._native(command)
        except Exception as exc:  # noqa: BLE001 - surface every failure to the caller
            return ExecutionOutcome(
                request_id, command, status="failed",
                result=f"native call raised: {exc}",
            )
        elapsed = int((time.monotonic() - started) * 1000)

        if not ok:
            return ExecutionOutcome(
                request_id, command, status="failed",
                result=f"Win32 call failed with error {error_code}",
                duration_ms=elapsed,
            )
        return ExecutionOutcome(request_id, command, status="succeeded", duration_ms=elapsed)

    def _execute_application(self, request_id: str, command: str,
                             target: Optional[str]) -> ExecutionOutcome:
        """Delegate an `APPLICATION_*` command to the application controller."""
        controller = self._applications
        if controller is None:
            return ExecutionOutcome(
                request_id, command, status="failed",
                result="application control is not available on this host",
            )
        outcome = controller.execute(request_id, command, target)
        return ExecutionOutcome(
            request_id, command, status=outcome.status,
            result=(outcome.result or "")[:1000] or None,
            duration_ms=outcome.duration_ms,
            target=outcome.target,
        )


    def _execute_cleanup(self, request_id: str, command: str) -> ExecutionOutcome:
        """Delegate a `CLEAN_*` action to the structured cleaner.

        No PowerShell, no `cmd /c`, no cleanmgr: the cleaner deletes through
        plain filesystem APIs and reports counts only. Off-Windows it answers
        an honest `NOT_SUPPORTED` rather than faking a cleanup.
        """
        cleaner = self._cleaner
        if cleaner is None:
            return ExecutionOutcome(
                request_id, command, status="failed",
                result="storage cleanup is not available on this host",
            )
        outcome = cleaner.execute(command)
        return ExecutionOutcome(
            request_id, command,
            status="succeeded" if outcome.status in ("SUCCESS", "PARTIAL") else "failed",
            result=self._cleanup_detail(outcome),
            duration_ms=None,
            target=None,
        )


    @staticmethod
    def _cleanup_detail(outcome) -> Optional[str]:
        from agents.common.cleaner import human_bytes

        detail = f"{outcome.action_id} {outcome.status} · items={outcome.items_removed} · freed={human_bytes(outcome.bytes_freed)}"
        if outcome.errors:
            detail += f" · errors={outcome.errors}"
        if outcome.detail:
            detail += f" · {outcome.detail}"
        return detail[:1000] or None

def _call_native(command: str) -> tuple[bool, int]:
    """Invoke the Win32 entry point for `command`. Windows only."""
    from ctypes import windll

    if command == "LOCK":
        user32 = windll.user32
        return bool(user32.LockWorkStation()), windll.kernel32.GetLastError()

    if command in ("SLEEP", "HIBERNATE"):
        powrprof = windll.powrprof
        hibernate = 1 if command == "HIBERNATE" else 0
        # SetSuspendState(BOOL bHibernate, BOOL bForce, BOOL bDisableWakeEvent)
        ok = bool(powrprof.SetSuspendState(hibernate, 0, 0))
        return ok, windll.kernel32.GetLastError()

    if command in ("RESTART", "SHUTDOWN"):
        advapi32 = windll.advapi32
        reboot = 1 if command == "RESTART" else 0
        # BOOL InitiateSystemShutdownExW(
        #   LPWSTR lpMachineName, LPWSTR lpMessage, DWORD dwTimeout,
        #   BOOL bForceAppsClosed, BOOL bRebootAfterShutdown,
        #   DWORD dwReason)
        ok = bool(advapi32.InitiateSystemShutdownExW(
            None, "VIGILIA remote command", 0, 0, reboot, 0x00040000,
        ))
        return ok, windll.kernel32.GetLastError()

    return False, 0


__all__ = ["WindowsCommandExecutor", "ExecutionOutcome", "COMMAND_TIMEOUT_SECONDS"]
