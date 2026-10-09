"""
VIGILIA Linux agent — command execution tests.

A fake runner records the argv the executor would have run and asserts on it,
so the suite proves two things without touching the host:
  - the argv is fixed per command (nothing from the request reaches exec)
  - failures and timeouts surface as `failed` rather than raising

Run:  python3 -m unittest discover -s agents/linux -p 'test_*.py' -v
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.logger import Logger  # noqa: E402
from agents.common.telemetry import CommandDispatch  # noqa: E402
from agents.linux.src.commands import CommandExecutor  # noqa: E402


class FakeRunner:
    """Records argv and returns canned results."""

    def __init__(self, results: list[subprocess.CompletedProcess] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.results = results if results is not None else []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        if self.results:
            return self.results[len(self.calls) - 1]
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    @property
    def argv(self) -> list[str]:
        return self.calls[0] if self.calls else []


class CommandExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.log = Logger(quiet=True)
        self.runner = FakeRunner()
        self.executor = CommandExecutor(self.log, runner=self.runner)

    def test_supports_the_full_allowlist(self) -> None:
        # Phase 3B extends the allowlist with the three application commands.
        # The set is asserted as a superset, not an exact match, so this test
        # documents the growth instead of pinning Phase 2's list forever.
        supported = set(self.executor.supported_commands())
        for command in ("LOCK", "SLEEP", "HIBERNATE", "RESTART", "SHUTDOWN"):
            self.assertIn(command, supported)
        for command in ("APPLICATION_OPEN", "APPLICATION_CLOSE", "APPLICATION_RESTART"):
            self.assertIn(command, supported)

    def test_rejects_anything_not_in_the_allowlist(self) -> None:
        for command in ("rm -rf /", "REBOOT", "shutdown -h now", "", "lock"):
            outcome = self.executor.execute("req-1", command)
            self.assertEqual(outcome.status, "failed", f"{command!r} must be refused")
            self.assertIn("unsupported", outcome.result or "")
        self.assertEqual(self.runner.calls, [], "a refused command never execs")

    def test_lock_targets_an_active_session(self) -> None:
        self.executor._active_sessions = lambda: ["3", "5"]  # type: ignore[method-assign]
        outcome = self.executor.execute("req-1", "LOCK")

        self.assertEqual(outcome.status, "succeeded")
        # The session id is an argument, never parsed out of the request.
        self.assertEqual(self.runner.calls, [["loginctl", "lock-session", "3"], ["loginctl", "lock-session", "5"]])

    def test_lock_without_a_session_fails_loudly(self) -> None:
        self.executor._active_sessions = lambda: []  # type: ignore[method-assign]
        outcome = self.executor.execute("req-1", "LOCK")

        self.assertEqual(outcome.status, "failed")
        self.assertIn("no active login session", outcome.result)
        self.assertEqual(self.runner.calls, [], "no session means no exec")

    def test_power_commands_run_once_system_wide(self) -> None:
        outcome = self.executor.execute("req-1", "SLEEP")

        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(self.runner.argv, ["systemctl", "suspend"])

    def test_hibernate_and_restart_and_shutdown(self) -> None:
        for command, expected in (
            ("HIBERNATE", ["systemctl", "hibernate"]),
            ("RESTART", ["systemctl", "reboot"]),
            ("SHUTDOWN", ["systemctl", "poweroff"]),
        ):
            self.runner.calls = []
            outcome = self.executor.execute("req-1", command)
            self.assertEqual(outcome.status, "succeeded")
            self.assertEqual(self.runner.argv, expected)

    def test_a_failing_helper_reports_failed(self) -> None:
        self.runner = FakeRunner([
            subprocess.CompletedProcess(["loginctl"], 1, stdout="", stderr="Operation not permitted"),
        ])
        self.executor = CommandExecutor(self.log, runner=self.runner)
        self.executor._active_sessions = lambda: ["3"]  # type: ignore[method-assign]

        outcome = self.executor.execute("req-1", "LOCK")

        self.assertEqual(outcome.status, "failed")
        self.assertIn("Operation not permitted", outcome.result or "")

    def test_an_unavailable_command_fails_instead_of_no_op(self) -> None:
        executor = CommandExecutor(self.log, runner=self.runner)
        # Every helper resolves to a missing binary.
        executor._build_argv = lambda command: None  # type: ignore[method-assign]

        outcome = executor.execute("req-1", "SLEEP")

        self.assertEqual(outcome.status, "failed")
        self.assertIn("not available", outcome.result or "")
        self.assertEqual(self.runner.calls, [])

    def test_duration_is_reported(self) -> None:
        outcome = self.executor.execute("req-1", "SHUTDOWN")
        self.assertEqual(outcome.status, "succeeded")
        self.assertIsInstance(outcome.duration_ms, int)
        self.assertGreaterEqual(outcome.duration_ms, 0)

    def test_execute_payload_rejects_a_malformed_dispatch(self) -> None:
        outcome = self.executor.execute_payload({"requestId": "x", "command": "LOCK"})
        self.assertEqual(outcome.status, "failed")
        self.assertIn("malformed", outcome.result or "")

    def test_execute_payload_runs_a_real_dispatch(self) -> None:
        dispatch = CommandDispatch(request_id="r-9", command="SLEEP")
        outcome = self.executor.execute_payload(dispatch)
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.request_id, "r-9")
        self.assertEqual(self.runner.argv, ["systemctl", "suspend"])

    def test_session_list_parses_loginctl_output(self) -> None:
        executor = CommandExecutor(self.log, runner=self.runner)
        sample = " 3 1000 alice seat0 tty2\n 4 1000 alice      tty3\nLEGEND\n"
        completed = subprocess.CompletedProcess(["loginctl"], 0, stdout=sample, stderr="")
        executor._active_sessions.__func__  # sanity: the method exists
        # Patch the subprocess call the session list uses.
        import agents.linux.src.commands as mod

        original = subprocess.run
        try:
            subprocess.run = lambda *a, **k: completed  # type: ignore[assignment]
            sessions = executor._active_sessions()
        finally:
            subprocess.run = original  # type: ignore[assignment]

        self.assertEqual(sessions, ["3"])
        # Only sessions bound to a seat are lockable; tty3 has none.


if __name__ == "__main__":
    unittest.main()
