"""
VIGILIA agent — Daily Auto Cleanup tests (Phase 5E).

The point of this module is the rule, so that is what is tested:

    * it never runs unless the owner authenticated
    * it runs at most once per calendar day
    * the once-per-day state is durable across a reboot
    * it is not a scheduler — there is no boot path and no timer
    * only the safe, allowlisted categories can ever run
    * a disabled config skips with a reason, and the reason is honest
    * it reuses the Phase 4 `Cleaner`, so there is no second deletion path

The cleaner here is a recording stand-in: it answers the same
`execute(action_id, options=...)` contract the real one does, so the *filter*
is what is under test — not whether a temp file was really deleted.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.cleaner import CleanResult  # noqa: E402
from agents.common.daily_cleanup import DailyCleanupController  # noqa: E402
from agents.common.logger import Logger  # noqa: E402
from agents.common.owner_auth import OwnerAuthenticator  # noqa: E402
from agents.common.security import (  # noqa: E402
    DAILY_SAFE_CLEAN_ACTIONS,
    SecurityCapabilities,
    SecurityEvent,
)
from agents.common.test_owner_auth import RecordingFace, Sink  # noqa: E402


class RecordingCleaner:
    """Answers the Phase 4 `Cleaner.execute` contract and records the calls.

    It cannot delete anything: there is no path handling here at all, so the
    test asserts the *allowlist* is the only way an action id reaches it.
    """

    def __init__(self, results: Optional[Dict[str, CleanResult]] = None) -> None:
        self.calls: List[tuple] = []
        self.options: List[dict] = []
        self._results = dict(results or {})

    def execute(self, action_id: str, options: Optional[dict] = None, **kwargs) -> CleanResult:
        self.calls.append((action_id,))
        self.options.append(dict(options or {}))
        if action_id in self._results:
            return self._results[action_id]
        return CleanResult(
            action_id=action_id,
            status="SUCCESS",
            bytes_freed=1024,
            items_removed=2,
            detail="recorded",
        )


def make_controller(
    tmp: Path,
    cleaner: RecordingCleaner,
    *,
    enabled: bool = True,
    categories=None,
    trash_enabled: bool = False,
    log_retention_days: int = 30,
    now=None,
    state_file: Optional[Path] = None,
    capabilities: Optional[SecurityCapabilities] = None,
    owner_state: str = "OWNER",
):
    """Build a controller wired to an owner already in `owner_state`."""
    log = Logger(quiet=True)
    face = RecordingFace()
    owner = OwnerAuthenticator(
        "dev",
        log,
        face,
        state_file=tmp / "owner-state.json",
        pin_file=tmp / "owner-pin.json",
        photo_dir=tmp / "photos",
        capture_photo=None,
        capabilities=capabilities or SecurityCapabilities(face_recognition=False, daily_cleanup=True),
    )
    owner.state = owner_state
    sink = Sink()
    owner.set_event_sink(sink)
    ctrl = DailyCleanupController(
        "dev",
        log,
        owner,
        cleaner,
        state_file=state_file or tmp / "daily-cleanup.json",
        now=now or (lambda: datetime(2026, 10, 5, 9, 0, 0)),
    )
    ctrl.set_config(
        {
            "enabled": enabled,
            "trigger": "AFTER_OWNER_LOGIN",
            "categories": list(categories if categories is not None else DAILY_SAFE_CLEAN_ACTIONS),
            "trashEnabled": trash_enabled,
            "logRetentionDays": log_retention_days,
        }
    )
    return ctrl, sink, owner


class EligibilityTests(unittest.TestCase):
    """The gate. Every rule is a separate reason the run may be refused."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-dc-"))
        self.cleaner = RecordingCleaner()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_owner_authenticated_is_eligible(self) -> None:
        ctrl, _, _ = make_controller(self.tmp, self.cleaner)
        eligible, reason = ctrl.eligibility()
        self.assertTrue(eligible)
        self.assertEqual(reason, "")

    def test_guest_unknown_and_locked_are_never_eligible(self) -> None:
        for state in ("UNKNOWN", "GUEST", "LOCKED"):
            ctrl, _, _ = make_controller(self.tmp, self.cleaner, owner_state=state)
            eligible, reason = ctrl.eligibility()
            self.assertFalse(eligible, f"{state} must not be eligible")
            self.assertEqual(reason, "NOT_AUTHENTICATED")

    def test_a_disabled_config_is_skipped_as_disabled(self) -> None:
        ctrl, _, _ = make_controller(self.tmp, self.cleaner, enabled=False)
        eligible, reason = ctrl.eligibility()
        self.assertFalse(eligible)
        self.assertEqual(reason, "DISABLED")

    def test_a_second_run_the_same_day_is_skipped_as_already_done(self) -> None:
        ctrl, _, _ = make_controller(self.tmp, self.cleaner)
        first = ctrl.run_after_owner_authentication()
        self.assertTrue(first.ran)
        eligible, reason = ctrl.eligibility()
        self.assertFalse(eligible)
        self.assertEqual(reason, "ALREADY_DONE")

    def test_a_new_calendar_day_is_eligible_again(self) -> None:
        clock = {"now": datetime(2026, 10, 5, 9, 0, 0)}
        ctrl, _, _ = make_controller(self.tmp, self.cleaner, now=lambda: clock["now"])
        self.assertTrue(ctrl.run_after_owner_authentication().ran)
        clock["now"] = datetime(2026, 10, 5, 23, 59, 0)
        self.assertEqual(ctrl.eligibility(), (False, "ALREADY_DONE"))
        clock["now"] = datetime(2026, 10, 6, 0, 1, 0)
        self.assertEqual(ctrl.eligibility(), (True, ""))
        self.assertTrue(ctrl.run_after_owner_authentication().ran)

    def test_no_safe_categories_is_skipped(self) -> None:
        ctrl, _, _ = make_controller(self.tmp, self.cleaner, categories=[])
        eligible, reason = ctrl.eligibility()
        self.assertFalse(eligible)
        self.assertEqual(reason, "NO_CATEGORIES")

    def test_unsafe_categories_are_dropped_before_the_gate(self) -> None:
        ctrl, _, _ = make_controller(
            self.tmp,
            self.cleaner,
            categories=["CLEAN_TEMP", "CLEAN_USER_CACHE", "DELETE_EVERYTHING", "/home/owner"],
        )
        # The manual-only and the invented ids are not daily-safe, so the
        # effective set is only what the allowlist permits.
        self.assertEqual(ctrl._safe_categories(), ["CLEAN_TEMP"])


class TriggerTests(unittest.TestCase):
    """5E: exactly one trigger — after the owner authenticates — and no other."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-dc-trig-"))
        self.cleaner = RecordingCleaner()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_run_after_authentication_executes_the_safe_categories(self) -> None:
        ctrl, sink, owner = make_controller(self.tmp, self.cleaner)
        outcome = ctrl.run_after_owner_authentication()
        self.assertTrue(outcome.ran)
        self.assertEqual(outcome.state, "OWNER")
        # `CLEAN_TRASH` is opt-in, so the default run is the other five.
        expected = [a for a in ctrl._safe_categories()]
        self.assertEqual(
            sorted(call[0] for call in self.cleaner.calls),
            sorted(expected),
        )
        self.assertIn("daily_cleanup.started", sink.types())
        self.assertIn("daily_cleanup.completed", sink.types())

    def test_the_owner_state_is_recorded_on_the_outcome(self) -> None:
        ctrl, sink, _ = make_controller(self.tmp, self.cleaner, owner_state="GUEST")
        outcome = ctrl.run_after_owner_authentication()
        self.assertFalse(outcome.ran)
        self.assertEqual(outcome.state, "GUEST")
        self.assertIn("daily_cleanup.skipped", sink.types())

    def test_a_skipped_run_touches_nothing(self) -> None:
        ctrl, _, _ = make_controller(self.tmp, self.cleaner, owner_state="UNKNOWN")
        outcome = ctrl.run_after_owner_authentication()
        self.assertFalse(outcome.ran)
        self.assertEqual(self.cleaner.calls, [])
        self.assertEqual(outcome.actions, 0)
        self.assertEqual(outcome.bytes_freed, 0)

    def test_a_run_records_the_date_in_the_owner_state(self) -> None:
        ctrl, _, owner = make_controller(self.tmp, self.cleaner)
        outcome = ctrl.run_after_owner_authentication()
        self.assertEqual(outcome.date, "2026-10-05")
        self.assertEqual(owner.last_daily_cleanup_date, "2026-10-05")

    def test_a_failed_category_still_marks_the_day_done(self) -> None:
        cleaner = RecordingCleaner(
            results={
                "CLEAN_TEMP": CleanResult(action_id="CLEAN_TEMP", status="FAILED", errors=1, detail="nope"),
            }
        )
        ctrl, sink, _ = make_controller(self.tmp, cleaner)
        outcome = ctrl.run_after_owner_authentication()
        self.assertTrue(outcome.ran)
        self.assertGreater(outcome.errors, 0)
        self.assertEqual(ctrl.state.last_cleanup_date, "2026-10-05")
        # Some categories still succeeded, so the run is a *partial* and is
        # reported as completed — but the day is still consumed, so the next
        # attempt the same date is refused.
        self.assertIn("daily_cleanup.completed", sink.types())
        self.assertEqual(ctrl.eligibility(), (False, "ALREADY_DONE"))

    def test_a_run_where_every_category_fails_reports_a_failure(self) -> None:
        cleaner = RecordingCleaner(
            results={
                action_id: CleanResult(action_id=action_id, status="FAILED", errors=1, detail="nope")
                for action_id in DAILY_SAFE_CLEAN_ACTIONS
            }
        )
        ctrl, sink, _ = make_controller(self.tmp, cleaner)
        outcome = ctrl.run_after_owner_authentication()
        self.assertTrue(outcome.ran)
        self.assertEqual(ctrl.state.last_result, "failed")
        self.assertIn("daily_cleanup.failed", sink.types())

    def test_log_retention_is_passed_to_the_cleaner_as_an_option(self) -> None:
        ctrl, _, _ = make_controller(self.tmp, self.cleaner, log_retention_days=14)
        ctrl.run_after_owner_authentication()
        for options in self.cleaner.options:
            self.assertEqual(options.get("logRetentionDays"), 14)

    def test_an_action_that_raises_does_not_stop_the_run(self) -> None:
        exploding = Exploding()
        ctrl, sink, _ = make_controller(self.tmp, exploding)
        outcome = ctrl.run_after_owner_authentication()
        self.assertTrue(outcome.ran)
        self.assertGreater(outcome.errors, 0)
        # Every category was attempted; one failure did not abort the rest.
        self.assertEqual(len(exploding.calls), len(ctrl._safe_categories()))


class Exploding(RecordingCleaner):
    """Raises on one action. The failure must not abort the remaining ones."""

    def execute(self, action_id, options=None, **kwargs):
        if action_id == "CLEAN_OLD_LOGS":
            # Count the attempt the way the parent does, then fail.
            self.calls.append((action_id,))
            self.options.append(dict(options or {}))
            raise RuntimeError("boom")
        return super().execute(action_id, options, **kwargs)


class DurabilityTests(unittest.TestCase):
    """5E: the once-per-day rule survives a reboot."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-dc-dur-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_second_run_the_same_day_after_a_restart_is_skipped(self) -> None:
        state_file = self.tmp / "daily-cleanup.json"
        clock = {"now": datetime(2026, 10, 5, 9, 0, 0)}
        cleaner = RecordingCleaner()

        ctrl, _, _ = make_controller(
            self.tmp, cleaner, state_file=state_file, now=lambda: clock["now"]
        )
        first = ctrl.run_after_owner_authentication()
        self.assertTrue(first.ran)
        runs_before = len(cleaner.calls)

        # A fresh controller, the same state file — this is a new process
        # reading the durable state after a reboot. It must not run again,
        # and it must *not* need to be told the day already happened.
        ctrl2, sink2, _ = make_controller(
            self.tmp, cleaner, state_file=state_file, now=lambda: clock["now"]
        )
        eligible, reason = ctrl2.eligibility()
        self.assertFalse(eligible)
        self.assertEqual(reason, "ALREADY_DONE")
        second = ctrl2.run_after_owner_authentication()
        self.assertFalse(second.ran)
        self.assertEqual(len(cleaner.calls), runs_before, "a reboot did not stop a second run")
        self.assertIn("daily_cleanup.skipped", sink2.types())

    def test_the_state_file_is_written_and_reloaded(self) -> None:
        state_file = self.tmp / "daily-cleanup.json"
        ctrl, _, _ = make_controller(self.tmp, RecordingCleaner(), state_file=state_file)
        ctrl.run_after_owner_authentication()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(data["lastCleanupDate"], "2026-10-05")
        self.assertEqual(data["lastResult"], "completed")

    def test_a_config_written_by_one_process_is_read_by_the_next(self) -> None:
        # The owner enables a narrow set from the phone. The *file* is the
        # contract between the two processes, so the next controller must see
        # exactly that set — nothing invented, nothing dropped.
        state_file = self.tmp / "daily-cleanup.json"
        ctrl, _, _ = make_controller(
            self.tmp,
            RecordingCleaner(),
            state_file=state_file,
            categories=["CLEAN_TEMP"],
            trash_enabled=True,
        )
        ctrl.run_after_owner_authentication()

        log = Logger(quiet=True)
        owner = OwnerAuthenticator(
            "dev",
            log,
            RecordingFace(),
            state_file=self.tmp / "owner-state.json",
            capabilities=SecurityCapabilities(face_recognition=False, daily_cleanup=True),
        )
        owner.state = "OWNER"
        ctrl2 = DailyCleanupController(
            "dev", log, owner, RecordingCleaner(),
            state_file=state_file,
            now=lambda: datetime(2026, 10, 6, 9, 0, 0),
        )
        config = ctrl2.config()
        self.assertEqual(config["categories"], ["CLEAN_TEMP"])
        self.assertTrue(config["trashEnabled"])
        self.assertEqual(config["trigger"], "AFTER_OWNER_LOGIN")
        # Trash is opt-in on top of the stored list, so it is armed here.
        self.assertEqual(ctrl2._safe_categories(), ["CLEAN_TEMP", "CLEAN_TRASH"])

    def test_a_corrupt_state_file_fails_safe_to_the_default(self) -> None:
        state_file = self.tmp / "daily-cleanup.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text("{not json", encoding="utf-8")
        ctrl, _, _ = make_controller(self.tmp, RecordingCleaner(), state_file=state_file)
        self.assertEqual(ctrl.state.last_cleanup_date, None)
        self.assertTrue(ctrl.eligibility()[0])


class CategorySafetyTests(unittest.TestCase):
    """5E: what the run may touch, and what it may never touch."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-dc-safe-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_daily_safe_set_is_a_subset_of_the_clean_allowlist(self) -> None:
        from agents.common.cleaner import CLEAN_ACTIONS

        for action in DAILY_SAFE_CLEAN_ACTIONS:
            self.assertIn(action, CLEAN_ACTIONS)

    def test_there_is_no_action_for_downloads_documents_or_source(self) -> None:
        from agents.common.cleaner import CLEAN_ACTIONS

        for forbidden in (
            "CLEAN_DOWNLOADS",
            "CLEAN_DOCUMENTS",
            "CLEAN_PROJECTS",
            "CLEAN_NODE_MODULES",
            "CLEAN_ENV",
            "CLEAN_DATABASES",
            "CLEAN_SSH_KEYS",
        ):
            self.assertNotIn(forbidden, CLEAN_ACTIONS)
            self.assertNotIn(forbidden, DAILY_SAFE_CLEAN_ACTIONS)

    def test_a_config_cannot_arm_an_action_the_run_may_not_run(self) -> None:
        ctrl, _, _ = make_controller(
            self.tmp,
            RecordingCleaner(),
            categories=["CLEAN_USER_CACHE", "CLEAN_DOWNLOADS", "/etc"],
        )
        self.assertEqual(ctrl._safe_categories(), [])

    def test_trash_runs_only_when_it_is_opted_in(self) -> None:
        ctrl, _, _ = make_controller(
            self.tmp, RecordingCleaner(), categories=["CLEAN_TEMP"], trash_enabled=False
        )
        self.assertNotIn("CLEAN_TRASH", ctrl._safe_categories())
        ctrl.set_config({**ctrl.config(), "trashEnabled": True})
        self.assertIn("CLEAN_TRASH", ctrl._safe_categories())

    def test_no_category_is_duplicated(self) -> None:
        ctrl, _, _ = make_controller(
            self.tmp,
            RecordingCleaner(),
            categories=["CLEAN_TEMP", "CLEAN_TEMP", "CLEAN_TRASH", "CLEAN_TRASH"],
            trash_enabled=True,
        )
        cats = ctrl._safe_categories()
        self.assertEqual(len(cats), len(set(cats)))

    def test_the_event_metadata_carries_no_path(self) -> None:
        ctrl, sink, _ = make_controller(self.tmp, RecordingCleaner())
        ctrl.run_after_owner_authentication()
        for event in sink.events:
            payload = event.to_payload()
            blob = json.dumps(payload)
            self.assertNotIn("/home", blob)
            self.assertNotIn("..", blob)
            self.assertNotIn("rm ", blob)
            for key in (event.metadata or {}):
                self.assertIn(
                    key,
                    ("failedAttempts", "bytesFreed", "actions", "reason", "actionId"),
                    f"an unexpected metadata key: {key}",
                )
            issues = event.validate()
            self.assertEqual(issues, [], f"{event.event_type} did not validate: {issues}")


class DisabledConfigTests(unittest.TestCase):
    """5F: the default is off, and the phone's switch is honoured."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vigilia-dc-off-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_controller_that_never_received_a_config_is_disabled(self) -> None:
        log = Logger(quiet=True)
        owner = OwnerAuthenticator(
            "dev",
            log,
            RecordingFace(),
            state_file=self.tmp / "owner-state.json",
            capabilities=SecurityCapabilities(face_recognition=False, daily_cleanup=True),
        )
        owner.state = "OWNER"
        ctrl = DailyCleanupController(
            "dev", log, owner, RecordingCleaner(),
            state_file=self.tmp / "daily-cleanup.json",
            now=lambda: datetime(2026, 10, 5, 9, 0, 0),
        )
        eligible, reason = ctrl.eligibility()
        self.assertFalse(eligible)
        self.assertEqual(reason, "DISABLED")

    def test_a_disabled_config_does_not_run_on_authentication(self) -> None:
        ctrl, sink, owner = make_controller(self.tmp, RecordingCleaner(), enabled=False)
        outcome = ctrl.run_after_owner_authentication()
        self.assertFalse(outcome.ran)
        self.assertEqual(owner.state, "OWNER")

    def test_a_skipped_run_reports_the_honest_label(self) -> None:
        ctrl, _, _ = make_controller(self.tmp, RecordingCleaner(), enabled=False)
        outcome = ctrl.run_after_owner_authentication()
        self.assertEqual(outcome.reason, "Daily cleanup is disabled")


if __name__ == "__main__":
    unittest.main()
