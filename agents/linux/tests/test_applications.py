"""
VIGILIA Linux agent — application control tests (Phase 3B).

The controller is exercised with an injected process list and an injected
launcher, so the suite proves the control logic without launching or killing
anything real:

  - an unknown target is refused, never launched
  - OPEN launches the fixed argv for a registered id only
  - CLOSE matches processes by name and reports honestly when they survive
  - RESTART is close-then-open through the same two paths
  - no argv or pid set ever comes from the request

Run:  python3 -m unittest discover -s agents/linux -p 'test_*.py' -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.applications import ApplicationCatalog, ApplicationDescriptor  # noqa: E402
from agents.common.logger import Logger  # noqa: E402
from agents.common.processes import ProcessInfo  # noqa: E402
from agents.linux.src.applications import (  # noqa: E402
    ApplicationOutcome,
    CLOSE_SETTLE_SECONDS,
    LinuxApplicationController,
)


def _proc(pid: int, name: str, status: str = "running") -> ProcessInfo:
    return ProcessInfo(pid=pid, name=name, cpu=0.0, status=status)


def _catalog(*entries: str) -> ApplicationCatalog:
    return ApplicationCatalog(
        device_id="dev",
        applications=[
            ApplicationDescriptor(application_id=name, label=name, launch_argv=[name]) for name in entries
        ],
    )


class FakeController(LinuxApplicationController):
    """A controller with injectable enumeration and launching."""

    def __init__(self, catalog: ApplicationCatalog, processes: list[ProcessInfo]):
        super().__init__(
            Logger(quiet=True),
            catalog,
            enumerate_processes=lambda: list(self._processes),
            launcher=self._record_launch,
        )
        self._processes = list(processes)
        self.launches: list[list[str]] = []
        self.signals: list[int] = []

    def _record_launch(self, argv: list[str]) -> None:
        self.launches.append(list(argv))

    def _terminate(self, pids: list[int], application_id: str) -> int:
        # A real SIGTERM makes the process disappear; the fake removes it from
        # the injected list so `_wait_until_gone` can observe the state change.
        signalled = 0
        for pid in pids:
            self._processes = [p for p in self._processes if p.pid != pid]
            self.signals.append(pid)
            signalled += 1
        self.log.info("application close signalled", application=application_id, pids=pids, signalled=signalled)
        return signalled


class LinuxApplicationControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log = Logger(quiet=True)

    def test_supported_commands_are_exactly_the_three(self) -> None:
        controller = FakeController(_catalog("firefox"), [])
        self.assertEqual(
            set(controller.supported_commands()),
            {"APPLICATION_OPEN", "APPLICATION_CLOSE", "APPLICATION_RESTART"},
        )

    def test_unsupported_command_fails(self) -> None:
        controller = FakeController(_catalog("firefox"), [])
        outcome = controller.execute("r1", "APPLICATION_DELETE", "firefox")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("unsupported", outcome.result or "")

    def test_unknown_target_is_refused(self) -> None:
        controller = FakeController(_catalog("firefox"), [])
        for target in ("/bin/sh", "sh;rm -rf /", "vim", None, ""):
            outcome = controller.execute("r1", "APPLICATION_OPEN", target)
            self.assertEqual(outcome.status, "failed", f"{target!r} must be refused")
            self.assertIn("not allowed", outcome.result or "")
        self.assertEqual(controller.launches, [], "nothing was launched")

    def test_open_launches_the_fixed_argv(self) -> None:
        controller = FakeController(_catalog("firefox"), [])
        outcome = controller.execute("r1", "APPLICATION_OPEN", "firefox")
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(controller.launches, [["firefox"]])
        self.assertIn("launched", outcome.result or "")

    def test_open_is_case_insensitive(self) -> None:
        controller = FakeController(_catalog("firefox"), [])
        outcome = controller.execute("r1", "APPLICATION_OPEN", "Firefox")
        self.assertEqual(outcome.status, "succeeded")

    def test_open_skips_when_already_running(self) -> None:
        controller = FakeController(_catalog("firefox"), [_proc(101, "firefox")])
        outcome = controller.execute("r1", "APPLICATION_OPEN", "firefox")
        self.assertEqual(outcome.status, "succeeded")
        self.assertIn("already running", outcome.result or "")
        self.assertEqual(controller.launches, [], "no second instance is spawned")

    def test_open_reports_when_not_launchable(self) -> None:
        catalog = ApplicationCatalog(
            device_id="dev",
            applications=[ApplicationDescriptor(application_id="widget", label="Widget", launch_argv=None)],
        )
        controller = FakeController(catalog, [])
        outcome = controller.execute("r1", "APPLICATION_OPEN", "widget")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("not launchable", outcome.result or "")
        self.assertEqual(controller.launches, [])

    def test_close_reports_when_nothing_is_running(self) -> None:
        controller = FakeController(_catalog("firefox"), [])
        outcome = controller.execute("r1", "APPLICATION_CLOSE", "firefox")
        self.assertEqual(outcome.status, "succeeded")
        self.assertIn("not running", outcome.result or "")

    def test_close_matches_by_process_name_and_terminates(self) -> None:
        controller = FakeController(
            _catalog("firefox"),
            [_proc(11, "firefox"), _proc(12, "firefox"), _proc(13, "code")],
        )
        outcome = controller.execute("r1", "APPLICATION_CLOSE", "firefox")

        self.assertEqual(outcome.status, "succeeded")
        self.assertIn("2 process(es) closed", outcome.result or "")
        self.assertEqual(controller.signals, [11, 12], "only matching pids were signalled")
        self.assertEqual(controller.launches, [])

    def test_close_matches_a_windows_style_name(self) -> None:
        controller = FakeController(_catalog("firefox"), [_proc(21, "firefox.exe")])
        outcome = controller.execute("r1", "APPLICATION_CLOSE", "firefox")
        self.assertEqual(outcome.status, "succeeded")

    def test_close_fails_when_the_process_survives(self) -> None:
        # A controller whose processes ignore SIGTERM: the injected list is
        # static, so the pid reappears on every poll past the settle window.
        controller = LinuxApplicationController(
            Logger(quiet=True),
            _catalog("firefox"),
            enumerate_processes=lambda: [_proc(31, "firefox")],
            launcher=lambda argv: None,
        )
        outcome = controller.execute("r1", "APPLICATION_CLOSE", "firefox")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("still running", outcome.result or "")
        self.assertIn(f"{CLOSE_SETTLE_SECONDS:.0f}s", outcome.result or "")

    def test_close_only_touches_the_targeted_application(self) -> None:
        controller = FakeController(
            _catalog("firefox", "code"),
            [_proc(41, "firefox"), _proc(42, "code")],
        )
        outcome = controller.execute("r1", "APPLICATION_CLOSE", "firefox")
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(controller.signals, [41])

    def test_a_zombie_is_treated_as_gone(self) -> None:
        # A zombie is dead but not yet reaped; matching it would make OPEN skip
        # its launch and CLOSE report a failure for something already gone.
        controller = FakeController(
            _catalog("firefox"),
            [_proc(71, "firefox", status="zombie")],
        )
        close = controller.execute("r1", "APPLICATION_CLOSE", "firefox")
        self.assertEqual(close.status, "succeeded")
        self.assertIn("not running", close.result or "")
        self.assertEqual(controller.signals, [])

        controller._processes = [_proc(72, "firefox", status="zombie")]
        opened = controller.execute("r2", "APPLICATION_OPEN", "firefox")
        self.assertEqual(opened.status, "succeeded")
        self.assertIn("launched", opened.result or "")

    def test_restart_closes_then_opens(self) -> None:
        controller = FakeController(_catalog("firefox"), [_proc(51, "firefox")])
        outcome = controller.execute("r1", "APPLICATION_RESTART", "firefox")

        self.assertEqual(outcome.status, "succeeded")
        self.assertIn("restarted", outcome.result or "")
        self.assertEqual(controller.signals, [51])
        self.assertEqual(controller.launches, [["firefox"]])

    def test_restart_aborts_when_close_fails(self) -> None:
        # The close step must fail before any relaunch happens. A controller
        # whose injected process survives SIGTERM is close-only: the fake
        # list is static, so the pid reappears on every poll.
        launched: list[list[str]] = []
        controller = LinuxApplicationController(
            Logger(quiet=True),
            _catalog("firefox"),
            enumerate_processes=lambda: [_proc(61, "firefox")],
            launcher=lambda argv: launched.append(list(argv)),
        )
        outcome = controller.execute("r1", "APPLICATION_RESTART", "firefox")

        self.assertEqual(outcome.status, "failed")
        self.assertIn("restart aborted at close", outcome.result or "")
        self.assertEqual(launched, [], "a failed close must not relaunch")

    def test_restart_fails_when_the_application_cannot_be_opened(self) -> None:
        catalog = ApplicationCatalog(
            device_id="dev",
            applications=[ApplicationDescriptor(application_id="widget", label="Widget", launch_argv=None)],
        )
        controller = FakeController(catalog, [])
        outcome = controller.execute("r1", "APPLICATION_RESTART", "widget")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("not launchable", outcome.result or "")

    def test_duration_is_reported(self) -> None:
        controller = FakeController(_catalog("firefox"), [])
        outcome = controller.execute("r1", "APPLICATION_OPEN", "firefox")
        self.assertEqual(outcome.status, "succeeded")
        self.assertIsInstance(outcome.duration_ms, int)
        self.assertGreaterEqual(outcome.duration_ms or 0, 0)

    def test_outcome_carries_the_normalised_target(self) -> None:
        controller = FakeController(_catalog("firefox"), [])
        outcome = controller.execute("r1", "APPLICATION_OPEN", "Firefox")
        self.assertEqual(outcome.target, "firefox")


if __name__ == "__main__":
    unittest.main()
