"""
VIGILIA Windows agent — application control tests (Phase 3B).

Same shape as the Linux suite, against the Win32 controller. Native entry
points are injected, so the surface is exercised on a non-Windows host
without ever calling a real Win32 function.

Contract rules proven here:
  - off-Windows the default path reports `failed`, never a fake success
  - an injected native double is the only way the action "runs" off-Windows
  - an unknown target is refused before any native call
  - CLOSE matches pids by name and verifies they are gone

Run:  python3 -m unittest discover -s agents/windows -p 'test_*.py' -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.applications import ApplicationCatalog, ApplicationDescriptor  # noqa: E402
from agents.common.logger import Logger  # noqa: E402
from agents.common.processes import ProcessInfo  # noqa: E402
from agents.windows.src.applications import (  # noqa: E402
    WindowsApplicationController,
)


def _proc(pid: int, name: str, status: str = "running") -> ProcessInfo:
    return ProcessInfo(pid=pid, name=name, cpu=0.0, status=status)


def _catalog(*entries: str) -> ApplicationCatalog:
    return ApplicationCatalog(
        device_id="dev",
        applications=[
            ApplicationDescriptor(application_id=name, label=name, launch_argv=None) for name in entries
        ],
    )


class WindowsApplicationControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log = Logger(quiet=True)
        self.calls: list[tuple[str, str, str]] = []
        # (action, application_id, argument): open → the resolved path,
        # close → the pid as a string.
        self.results: dict[str, tuple[bool, int]] = {}
        self.processes: list[ProcessInfo] = []

    def _native(self, action: str, application_id: str, argument: str) -> tuple[bool, int]:
        self.calls.append((action, application_id, argument))
        if action == "close":
            # TerminateProcess makes the process disappear; the fake drops it
            # so the controller's verification loop can observe the change.
            pid = int(argument)
            self.processes = [p for p in self.processes if p.pid != pid]
        return self.results.get(action, (True, 0))

    def _controller(self, catalog: ApplicationCatalog) -> WindowsApplicationController:
        return WindowsApplicationController(
            self.log,
            catalog,
            enumerate_processes=lambda: list(self.processes),
            native=self._native,
        )

    # ---------------- off-Windows honesty ----------------
    def test_off_windows_default_path_reports_failure(self) -> None:
        controller = WindowsApplicationController(
            self.log, _catalog("notepad"), enumerate_processes=lambda: []
        )
        outcome = controller.execute("r1", "APPLICATION_OPEN", "notepad")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("non-Windows host", outcome.result or "")
        self.assertEqual(self.calls, [], "no native call was made")

    # ---------------- allowlist ----------------
    def test_supported_commands(self) -> None:
        controller = self._controller(_catalog("notepad"))
        self.assertEqual(
            set(controller.supported_commands()),
            {"APPLICATION_OPEN", "APPLICATION_CLOSE", "APPLICATION_RESTART"},
        )

    def test_unsupported_command_fails(self) -> None:
        controller = self._controller(_catalog("notepad"))
        outcome = controller.execute("r1", "APPLICATION_DELETE", "notepad")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("unsupported", outcome.result or "")

    def test_unknown_target_is_refused(self) -> None:
        controller = self._controller(_catalog("notepad"))
        for target in ("C:\\Windows\\System32\\cmd.exe", "sh", "powershell", None, ""):
            outcome = controller.execute("r1", "APPLICATION_OPEN", target)
            self.assertEqual(outcome.status, "failed", f"{target!r} must be refused")
            self.assertIn("not allowed", outcome.result or "")
        self.assertEqual(self.calls, [], "no native call was made")

    # ---------------- open ----------------
    def test_open_calls_the_shell_with_a_resolved_path(self) -> None:
        controller = self._controller(_catalog("notepad"))
        outcome = controller.execute("r1", "APPLICATION_OPEN", "notepad")

        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(self.calls, [("open", "notepad", "notepad.exe")],
                         "the request never chooses the executable")

    def test_open_is_case_insensitive(self) -> None:
        controller = self._controller(_catalog("notepad"))
        outcome = controller.execute("r1", "APPLICATION_OPEN", "Notepad")
        self.assertEqual(outcome.status, "succeeded")

    def test_open_skips_when_already_running(self) -> None:
        controller = self._controller(_catalog("notepad"))
        self.processes = [_proc(7, "notepad.exe")]
        outcome = controller.execute("r1", "APPLICATION_OPEN", "notepad")
        self.assertEqual(outcome.status, "succeeded")
        self.assertIn("already running", outcome.result or "")
        self.assertEqual(self.calls, [], "no second instance is launched")

    def test_open_reports_a_win32_failure(self) -> None:
        self.results = {"open": (False, 5)}
        controller = self._controller(_catalog("notepad"))
        outcome = controller.execute("r1", "APPLICATION_OPEN", "notepad")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("error 5", outcome.result or "")

    # ---------------- close ----------------
    def test_close_terminates_matching_pids_and_verifies(self) -> None:
        controller = self._controller(_catalog("notepad"))
        self.processes = [_proc(11, "notepad.exe"), _proc(12, "calc.exe")]
        outcome = controller.execute("r1", "APPLICATION_CLOSE", "notepad")

        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(
            self.calls,
            [("close", "notepad", "11")],
            "only the matching pid is terminated",
        )

    def test_close_reports_when_nothing_is_running(self) -> None:
        controller = self._controller(_catalog("notepad"))
        outcome = controller.execute("r1", "APPLICATION_CLOSE", "notepad")
        self.assertEqual(outcome.status, "succeeded")
        self.assertIn("not running", outcome.result or "")
        self.assertEqual(self.calls, [])

    def test_a_zombie_is_treated_as_gone(self) -> None:
        # A zombie is dead but not yet reaped; matching it would make OPEN skip
        # its launch and CLOSE report a failure for something already gone.
        controller = self._controller(_catalog("notepad"))
        self.processes = [_proc(71, "notepad.exe", status="zombie")]

        close = controller.execute("r1", "APPLICATION_CLOSE", "notepad")
        self.assertEqual(close.status, "succeeded")
        self.assertIn("not running", close.result or "")
        self.assertEqual(self.calls, [], "no native call was made")

        opened = controller.execute("r2", "APPLICATION_OPEN", "notepad")
        self.assertEqual(opened.status, "succeeded")
        self.assertIn("launched", opened.result or "")

    def test_close_fails_when_the_process_survives(self) -> None:        # TerminateProcess "succeeds" but the pid is still enumerated (a
        # protected process, or a child respawning it). The controller must
        # catch that instead of reporting success.
        controller = WindowsApplicationController(
            self.log,
            _catalog("notepad"),
            enumerate_processes=lambda: [_proc(21, "notepad.exe")],
            native=lambda action, app_id, arg: (True, 0),
        )
        outcome = controller.execute("r1", "APPLICATION_CLOSE", "notepad")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("still running", outcome.result or "")

    # ---------------- restart ----------------
    def test_restart_closes_then_opens(self) -> None:
        controller = self._controller(_catalog("notepad"))
        self.processes = [_proc(31, "notepad.exe")]
        outcome = controller.execute("r1", "APPLICATION_RESTART", "notepad")

        self.assertEqual(outcome.status, "succeeded")
        self.assertIn("restarted", outcome.result or "")
        self.assertEqual([call[0] for call in self.calls], ["close", "open"])

    def test_restart_aborts_when_close_fails(self) -> None:
        # Same as the Linux suite: a process that survives termination makes
        # the close step fail, and the restart must not relaunch.
        launched: list[str] = []
        controller = WindowsApplicationController(
            self.log,
            _catalog("notepad"),
            enumerate_processes=lambda: [_proc(41, "notepad.exe")],
            native=lambda action, app_id, arg: (
                (launched.append(arg) or (True, 0)) if action == "open" else (False, 5)
            ),
        )
        outcome = controller.execute("r1", "APPLICATION_RESTART", "notepad")

        self.assertEqual(outcome.status, "failed")
        self.assertIn("restart aborted at close", outcome.result or "")
        self.assertEqual(launched, [], "a failed close must not relaunch")

    # ---------------- outcome shape ----------------
    def test_outcome_carries_the_normalised_target(self) -> None:
        controller = self._controller(_catalog("notepad"))
        outcome = controller.execute("r1", "APPLICATION_OPEN", "Notepad")
        self.assertEqual(outcome.target, "notepad")
        self.assertIsInstance(outcome.duration_ms, int)


if __name__ == "__main__":
    unittest.main()
