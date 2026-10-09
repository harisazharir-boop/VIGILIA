"""
VIGILIA — command contract tests (Python mirror of shared/src/schemas.ts).

Run:  python3 -m unittest discover -s agents/common -p 'test_*.py' -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.telemetry import (  # noqa: E402
    APPLICATION_COMMANDS,
    COMMAND_ALLOWLIST,
    CommandDispatch,
    CommandResult,
    is_allowed_command,
    requires_target,
)

#: The Phase 2 power commands, unchanged by Phase 3B.
OS_COMMANDS = frozenset({"LOCK", "SLEEP", "HIBERNATE", "RESTART", "SHUTDOWN"})
#: The Phase 3B application commands, added on top of the Phase 2 surface.
APP_COMMANDS = frozenset({"APPLICATION_OPEN", "APPLICATION_CLOSE", "APPLICATION_RESTART"})


class AllowlistTests(unittest.TestCase):
    def test_allowlist_is_the_documented_set(self) -> None:
        # Phase 3B extends the allowlist; the surface is asserted as the union
        # so the test documents the growth without pinning Phase 2 forever.
        self.assertEqual(set(COMMAND_ALLOWLIST), OS_COMMANDS | APP_COMMANDS)

    def test_os_and_application_commands_are_disjoint(self) -> None:
        # RESTART restarts the operating system; APPLICATION_RESTART restarts
        # one application. They must stay separate verbs.
        self.assertEqual(OS_COMMANDS & APP_COMMANDS, frozenset())
        self.assertIn("RESTART", OS_COMMANDS)
        self.assertIn("APPLICATION_RESTART", APP_COMMANDS)

    def test_shell_meta_is_not_a_command(self) -> None:
        for attempt in ("rm -rf /", "shutdown; ls", "LOCK extra", "", "lock", "REBOOT", "POWEROFF", None, 5):
            self.assertFalse(is_allowed_command(attempt), f"must reject {attempt!r}")

    def test_every_supported_command_is_accepted(self) -> None:
        for command in COMMAND_ALLOWLIST:
            self.assertTrue(is_allowed_command(command))


class DispatchTests(unittest.TestCase):
    def test_parses_a_dispatch_frame(self) -> None:
        dispatch = CommandDispatch.from_payload({
            "requestId": "abc-123",
            "command": "LOCK",
            "deadline": 1790618540,
            "note": "lost it",
        })
        self.assertEqual(dispatch.request_id, "abc-123")
        self.assertEqual(dispatch.command, "LOCK")
        self.assertEqual(dispatch.note, "lost it")

    def test_missing_fields_do_not_raise(self) -> None:
        dispatch = CommandDispatch.from_payload({})
        self.assertEqual(dispatch.request_id, "")
        self.assertEqual(dispatch.command, "")
        self.assertFalse(dispatch.expired)

    def test_expired_uses_epoch_seconds(self) -> None:
        past = CommandDispatch(request_id="1", command="LOCK", deadline=1)
        self.assertTrue(past.expired)

        future_deadline = 4_000_000_000
        future = CommandDispatch(request_id="1", command="LOCK", deadline=future_deadline)
        self.assertFalse(future.expired)


class TargetTests(unittest.TestCase):
    def test_application_commands_require_a_target(self) -> None:
        for command in COMMAND_ALLOWLIST:
            self.assertEqual(
                requires_target(command),
                command in APP_COMMANDS,
                f"requires_target({command!r}) must match the application set",
            )

    def test_a_missing_target_is_an_invalid_frame(self) -> None:
        for command in APP_COMMANDS:
            dispatch = CommandDispatch(request_id="1", command=command)
            self.assertFalse(dispatch.is_valid, f"{command} needs an application target")

    def test_a_target_makes_the_frame_valid(self) -> None:
        dispatch = CommandDispatch(request_id="1", command="APPLICATION_OPEN", target="firefox")
        self.assertTrue(dispatch.is_valid)

    def test_a_target_is_not_required_for_power_commands(self) -> None:
        for command in OS_COMMANDS:
            dispatch = CommandDispatch(request_id="1", command=command)
            self.assertTrue(dispatch.is_valid, f"{command} takes no target")

    def test_an_unknown_command_is_invalid_with_or_without_a_target(self) -> None:
        self.assertFalse(CommandDispatch(request_id="1", command="rm -rf /").is_valid)
        self.assertFalse(CommandDispatch(request_id="1", command="rm -rf /", target="sh").is_valid)


class ResultTests(unittest.TestCase):
    def test_payload_shape_matches_the_backend_contract(self) -> None:
        result = CommandResult(request_id="abc", status="succeeded", duration_ms=120)
        self.assertEqual(result.to_payload(), {
            "requestId": "abc",
            "status": "succeeded",
            "durationMs": 120,
        })

    def test_result_is_truncated(self) -> None:
        result = CommandResult(request_id="abc", status="failed", result="x" * 5000)
        self.assertEqual(len(result.to_payload()["result"]), 1000)

    def test_optional_fields_are_omitted(self) -> None:
        self.assertEqual(CommandResult(request_id="abc").to_payload(), {
            "requestId": "abc",
            "status": "succeeded",
        })


if __name__ == "__main__":
    unittest.main()
