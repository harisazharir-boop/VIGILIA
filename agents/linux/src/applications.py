"""
VIGILIA Linux agent — application control (Phase 3B).

Implements the three application commands. Like the Phase 2 power commands,
this module resolves an allowlisted id to a fixed action and never lets a
request reach `exec` or `kill`:

  APPLICATION_OPEN    resolve `target` in the registry -> run its fixed argv
                      detached, no shell
  APPLICATION_CLOSE   find the processes whose name matches the id and ask
                      each one to stop (SIGTERM), verifying they are gone
  APPLICATION_RESTART CLOSE then OPEN through the same two paths

Process control choices:

- **SIGTERM, not SIGKILL.** A kill verb is the same mechanism whether it is
  launched from a phone or from an attacker's shell; SIGTERM gives the
  application a chance to save its state and makes an accidental close
  recoverable. A process that ignores SIGTERM is reported as still running
  rather than being force-killed: the operator's request does not silently
  escalate to something destructive.
- **Match by process name only.** The target is matched against the process
  `name` the Phase 3A collector reports, so the mobile client closes exactly
  what it sees running. Pid is not accepted as an identifier — pids are
  recycled, and closing "pid 1234" ten seconds later may close an unrelated
  process.
- **No `killall`, no shell.** Process termination goes through the same
  psutil//proc enumeration as telemetry; the only write is the signal.

Off-Windows behaviour: nothing here is faked. A Windows id resolved through
the Linux registry is simply absent from it and fails with `not allowed`.
"""
from __future__ import annotations

import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from agents.common.applications import ApplicationCatalog, ApplicationDescriptor
from agents.common.logger import Logger
from agents.common.processes import ProcessInfo

#: Seconds to wait for a terminated process to actually disappear.
CLOSE_SETTLE_SECONDS = 3.0
#: Seconds between two polls of the process list while waiting.
CLOSE_POLL_INTERVAL = 0.2
#: Ceiling on the number of processes one command may touch. A application
#: with a thousand children is not a normal case, and an unbounded loop is
#: not a safe default for a remote-controlled verb.
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


class LinuxApplicationController:
    """Open / close / restart applications from the local registry."""

    def __init__(
        self,
        log: Logger,
        catalog: ApplicationCatalog,
        enumerate_processes: Optional[Callable[[], list[ProcessInfo]]] = None,
        launcher: Optional[Callable[[list[str]], None]] = None,
    ) -> None:
        self.log = log
        self._catalog = catalog
        # `enumerate_processes` and `launcher` are injected by tests so the
        # suite can exercise the logic without touching the real process
        # table or actually launching anything.
        self._enumerate = enumerate_processes or _enumerate_processes
        self._launch = launcher or _launch_detached

    def supported_commands(self) -> tuple[str, ...]:
        return ("APPLICATION_OPEN", "APPLICATION_CLOSE", "APPLICATION_RESTART")

    def execute(self, request_id: str, command: str, target: Optional[str]) -> ApplicationOutcome:
        """Run one application command. Never raises; failures become `failed`."""
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
        argv = app.launch_argv
        if not argv:
            return "failed", f"{app.application_id} is not launchable from this agent"
        if self._is_running(app.application_id):
            # Honest, not a no-op: the requested state already holds, and the
            # caller should not see a second instance being spawned.
            return "succeeded", f"{app.application_id} is already running"
        self._launch(argv)
        return "succeeded", f"{app.application_id} launched"

    # ---------------- close ----------------
    def _close(self, app: ApplicationDescriptor) -> tuple[str, str]:
        pids = self._matching_pids(app.application_id)
        if not pids:
            # Nothing to close. Report the honest state rather than faking a
            # success that would hide a stale dashboard row.
            return "succeeded", f"{app.application_id} is not running"

        signalled = self._terminate(pids, app.application_id)
        gone = self._wait_until_gone(app.application_id, pids)
        if not gone:
            still = sorted(set(pids) & set(self._matching_pids(app.application_id)))
            return "failed", (
                f"{app.application_id}: {signalled} process(es) signalled, "
                f"{still} still running after {CLOSE_SETTLE_SECONDS:.0f}s"
            )
        return "succeeded", f"{app.application_id}: {signalled} process(es) closed"

    # ---------------- restart ----------------
    def _restart(self, app: ApplicationDescriptor) -> tuple[str, str]:
        close_status, close_message = self._close(app)
        if close_status != "succeeded":
            return "failed", f"restart aborted at close: {close_message}"
        # A short pause so the freed resources (and a possible launcher lock)
        # settle before the same application starts again.
        time.sleep(0.4)
        open_status, open_message = self._open(app)
        if open_status != "succeeded":
            return "failed", f"restart closed but failed to reopen: {open_message}"
        return "succeeded", f"{app.application_id} restarted"

    # ---------------- process helpers ----------------
    def _is_running(self, application_id: str) -> bool:
        return bool(self._matching_pids(application_id))

    def _matching_pids(self, application_id: str) -> list[int]:
        """Pids whose process name is this application.

        Matches on the process `name` (the same field Phase 3A telemetry
        reports), lower-cased and stripped of a `.exe` suffix so a Windows
        name compares equal to its Linux spelling. A zombie is excluded: the
        process is gone, it only has not been reaped yet, and treating it as
        "running" would make a later OPEN skip its launch and a later CLOSE
        report a failure for something that is already dead.
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

    def _terminate(self, pids: list[int], application_id: str) -> int:
        """SIGTERM every matching pid we can signal."""
        signalled = 0
        for pid in pids:
            if _terminate_pid(pid):
                signalled += 1
        self.log.info("application close signalled",
                      application=application_id, pids=pids, signalled=signalled)
        return signalled

    def _wait_until_gone(self, application_id: str, pids: list[int]) -> bool:
        """Poll until none of `pids` remain, or the settle window expires."""
        deadline = time.monotonic() + CLOSE_SETTLE_SECONDS
        while time.monotonic() < deadline:
            remaining = set(self._matching_pids(application_id))
            if not (remaining & set(pids)):
                return True
            time.sleep(CLOSE_POLL_INTERVAL)
        return False


def _process_name_key(name: str) -> str:
    """Normalise a process or application name for comparison."""
    key = (name or "").strip().lower()
    if key.endswith(".exe"):
        key = key[: -len(".exe")]
    return key


def _enumerate_processes() -> list[ProcessInfo]:
    """Live process list via the Linux collector (psutil or /proc)."""
    from agents.linux.src.collectors import LinuxCollector
    from agents.common.logger import Logger as _Logger

    collector = LinuxCollector(_Logger(quiet=True))
    return collector.get_process_list(limit=CLOSE_PROCESS_CAP, include_command_line=False)


def _terminate_pid(pid: int) -> bool:
    """Send SIGTERM to one pid. `False` when the process is already gone."""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False


def _launch_detached(argv: list[str]) -> None:
    """Run a fixed argv detached from the agent, with no shell involved.

    The argv is the constant from the registry — it never contains anything
    derived from the request, so there is no injection surface even though
    `subprocess` is used. Detaching keeps the child alive after the agent's
    own session ends, which is what "open an application" has to mean.
    """
    import subprocess

    subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


__all__ = [
    "LinuxApplicationController",
    "ApplicationOutcome",
    "CLOSE_SETTLE_SECONDS",
    "CLOSE_PROCESS_CAP",
]
