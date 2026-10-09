"""
VIGILIA Linux agent — command execution.

Executes the fixed allowlist of remote commands. Design constraints:

  - No shell is ever spawned. Every command resolves to a fixed argv list.
  - The argv lists are constants here, not derived from the request. A
    crafted frame cannot smuggle arguments, because there is no parsing step
    between the allowlist check and exec.
  - A command the platform cannot perform returns `failed` with a reason
    instead of raising.
  - Power actions (SLEEP/HIBERNATE/RESTART/SHUTDOWN) need elevated
    privileges; on a systemd host the agent runs under a polkit-enabled
    service or as root. A missing capability surfaces as `failed`, never as a
    silent no-op.

All commands are run via `subprocess.run` with a timeout, so a hung helper
cannot stall the telemetry loop.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from agents.common.logger import Logger
from agents.common.telemetry import (
    COMMAND_ALLOWLIST,
    is_allowed_command,
    is_application_command,
    is_clean_command,
)

#: Seconds before a helper invocation is abandoned.
COMMAND_TIMEOUT_SECONDS = 15

#: Fixed argv per command. `require_session` marks commands that must run
#: against an active login session (LOCK); power commands are system-wide.
_COMMANDS: dict[str, tuple[list[str], bool]] = {
    # Prefer the modern D-Bus login manager.
    "LOCK": (["loginctl", "lock-session"], True),
    "SLEEP": (["systemctl", "suspend"], False),
    "HIBERNATE": (["systemctl", "hibernate"], False),
    "RESTART": (["systemctl", "reboot"], False),
    "SHUTDOWN": (["systemctl", "poweroff"], False),
}

#: Fallback helper per command when the primary binary is missing.
_FALLBACKS: dict[str, list[str]] = {
    "LOCK": ["xdg-screensaver", "lock"],
    "SLEEP": ["systemctl", "suspend"],
    "HIBERNATE": ["systemctl", "hibernate"],
    "RESTART": ["shutdown", "-r", "now"],
    "SHUTDOWN": ["shutdown", "-P", "now"],
}


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


class CommandExecutor:
    """Runs the fixed allowlist of commands on this host."""

    def __init__(self, log: Logger, runner=None,
                 application_controller: Optional[object] = None,
                  cleaner: Optional[object] = None) -> None:
        self.log = log
        # `runner` is injected by tests to avoid touching the real system.
        self._runner = runner or _run_argv
        # Application commands (Phase 3B) are served by a separate controller
        # that shares the same allowlist discipline. Injected by tests.
        self._applications = application_controller
        # Cleanup commands (Phase 4B) are served by the structured cleaner,
        # which resolves an action id to its own fixed roots — never a path
        # from a request. Injected by tests. A separate object on purpose, so
        # the power-command path cannot reach the deletion code.
        self._cleaner = cleaner

    def supported_commands(self) -> tuple[str, ...]:
        return COMMAND_ALLOWLIST

    def _active_sessions(self) -> list[str]:
        """Seat session ids reported by loginctl, oldest first.

        Only sessions bound to a seat are lockable, so a row is skipped when
        the seat column is absent (plain output collapses an empty seat into
        fewer columns, and a tty-only session is not a graphical login).
        """
        try:
            completed = subprocess.run(
                ["loginctl", "list-sessions", "--no-legend", "--plain"],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.log.warn("could not list login sessions", error=str(exc))
            return []

        sessions: list[str] = []
        for line in completed.stdout.splitlines():
            fields = line.split()
            # Plain columns: SESSION UID USER SEAT TTY — five fields means the
            # session has a seat; four means the seat column was empty.
            if len(fields) >= 5 and fields[3]:
                sessions.append(fields[0])
        return sessions

    def _build_argv(self, command: str) -> Optional[tuple[list[str], bool]]:
        entry = _COMMANDS.get(command)
        if entry is None:
            return None
        argv, require_session = entry
        if shutil.which(argv[0]) is None:
            fallback = _FALLBACKS.get(command)
            if fallback is None or shutil.which(fallback[0]) is None:
                return None
            argv = fallback
        return argv, require_session

    def execute(self, request_id: str, command: str,
                target: Optional[str] = None) -> ExecutionOutcome:
        """Execute one command. Never raises; failures become `failed`.

        `target` is the `applicationId` for `APPLICATION_*` commands. It is
        resolved by the application controller against the local registry,
        never used to build argv here.
        """
        if not is_allowed_command(command):
            if is_clean_command(command):
                return self._execute_cleanup(request_id, command)
            self.log.warn("rejected unknown command", request_id=request_id, command=command)
            return ExecutionOutcome(request_id, command, status="failed", result="unsupported command")

        if is_application_command(command):
            return self._execute_application(request_id, command, target)

        resolved = self._build_argv(command)
        if resolved is None:
            self.log.warn("command not available on this host", command=command)
            return ExecutionOutcome(
                request_id, command, status="failed",
                result=f"{command} is not available on this host",
            )

        argv, require_session = resolved
        targets: list[list[str]] = []
        if require_session:
            sessions = self._active_sessions()
            if not sessions:
                # The helper exists but nobody is logged in at a seat.
                return ExecutionOutcome(
                    request_id, command, status="failed",
                    result="no active login session to lock",
                )
            for session in sessions:
                targets.append(argv + [session])
        else:
            targets.append(argv)

        failures: list[str] = []
        started = time.monotonic()
        for target_argv in targets:
            outcome = self._runner(target_argv)
            if outcome.returncode != 0:
                detail = (outcome.stderr or outcome.stdout or "").strip()
                failures.append(f"{target_argv[0]}: {detail or 'exit ' + str(outcome.returncode)}")

        elapsed = int((time.monotonic() - started) * 1000)
        if failures:
            return ExecutionOutcome(
                request_id, command, status="failed",
                result="; ".join(failures)[:1000],
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

        The command *is* the action id; there is no target and no path to
        consider. The cleaner resolves it to fixed roots on this host and
        returns counts only.
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
        """A short, path-free status line for the audit trail."""
        from agents.common.cleaner import human_bytes

        detail = f"{outcome.action_id} {outcome.status} · items={outcome.items_removed} · freed={human_bytes(outcome.bytes_freed)}"
        if outcome.errors:
            detail += f" · errors={outcome.errors}"
        if outcome.detail:
            detail += f" · {outcome.detail}"
        return detail[:1000] or None

    def execute_payload(self, dispatch: object) -> ExecutionOutcome:
        """Run a `CommandDispatch` payload and return a reply-shaped outcome."""
        from agents.common.telemetry import CommandDispatch

        if not isinstance(dispatch, CommandDispatch):
            return ExecutionOutcome("", "UNKNOWN", status="failed", result="malformed dispatch")
        return self.execute(dispatch.request_id, dispatch.command, dispatch.target)


def _run_argv(argv: list[str]) -> subprocess.CompletedProcess:
    """Run a fixed argv with no shell involvement."""
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )


__all__ = ["CommandExecutor", "ExecutionOutcome", "COMMAND_TIMEOUT_SECONDS"]
