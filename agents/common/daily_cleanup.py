"""
VIGILIA agent — Daily Auto Cleanup (Phase 5E).

The rule, enforced in code:

    boot → security system active → owner authentication
              │
              ├── owner NOT authenticated → skip, reason NOT_AUTHENTICATED
              └── owner authenticated
                    ↓
                already completed today?
                  ├── yes → skip, reason ALREADY_DONE
                  └── no → run the safe categories
                               ↓
                        record completion

    Maximum frequency: ONE TIME PER DAY.

The once-per-day state is **durable**: it lives in a JSON file the agent owns
(mode 0600), not in memory, so a reboot does not reset the daily limit and a
second boot the same day does not clean a second time.

Timezone handling: the day is a calendar date (`YYYY-MM-DD`) derived from the
*device's* local time, because only the device knows which timezone its owner
is in. The rule is therefore "one cleanup per device-day", which is what the
phase requires, and it cannot drift with the server's clock.

Safe categories only
--------------------
Every action id is filtered through `DAILY_SAFE_CLEAN_ACTIONS`, which is a
subset of the Phase 4 `CLEAN_ACTIONS` allowlist. The cleanup itself goes
through the *same* `Cleaner.execute()` the manual dispatch uses — there is no
second deletion path, no second allowlist and no way for this module to name
a path. Downloads, Documents, projects, source, `node_modules`, config,
credentials, SSH keys, `.env` and databases have no action id at all, so they
cannot be requested by anything in this module.

`CLEAN_TRASH` is opt-in (`trashEnabled`) and defaults off.

Not a scheduler
---------------
This is not a general scheduler. There is exactly one trigger — after the
owner authenticates — and it is evaluated once per authentication. No cron,
no at, no timers.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from agents.common.cleaner import CleanResult, human_bytes, is_clean_action
from agents.common.logger import Logger
from agents.common.owner_auth import OwnerAuthenticator
from agents.common.security import (
    DAILY_CLEANUP_SKIP_LABELS,
    DEFAULT_DAILY_CLEANUP_CONFIG,
    is_daily_safe_action,
)


@dataclass
class DailyCleanupOutcome:
    """What happened in one daily cleanup attempt. Never a path."""

    ran: bool
    state: str
    reason: str
    actions: int = 0
    bytes_freed: int = 0
    items_removed: int = 0
    errors: int = 0
    date: str = ""
    results: list = field(default_factory=list)

    @property
    def skipped(self) -> bool:
        return not self.ran


@dataclass
class DailyCleanupState:
    """The durable once-per-day state."""

    last_cleanup_date: Optional[str] = None
    last_cleanup_at: Optional[float] = None
    last_result: Optional[str] = None
    config: dict = field(default_factory=lambda: dict(DEFAULT_DAILY_CLEANUP_CONFIG))

    def to_dict(self) -> dict:
        return {
            "lastCleanupDate": self.last_cleanup_date,
            "lastCleanupAt": self.last_cleanup_at,
            "lastResult": self.last_result,
            "config": self.config,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DailyCleanupState":
        config = data.get("config")
        if not isinstance(config, dict):
            config = dict(DEFAULT_DAILY_CLEANUP_CONFIG)
        return cls(
            last_cleanup_date=data.get("lastCleanupDate"),
            last_cleanup_at=data.get("lastCleanupAt"),
            last_result=data.get("lastResult"),
            config=config,
        )


class DailyCleanupController:
    """Evaluates the eligibility rule and runs the safe categories."""

    def __init__(
        self,
        device_id: str,
        log: Logger,
        owner: OwnerAuthenticator,
        cleaner,
        *,
        state_file: Optional[Path] = None,
        now: Optional[callable] = None,
    ) -> None:
        self.device_id = device_id
        self.log = log
        self.owner = owner
        self.cleaner = cleaner
        self.state_file = state_file
        # `now` is injected so a test can move the calendar without waiting.
        self._now = now or (lambda: datetime.now())
        self.state = self._load_state()

    # ---------------- config ----------------
    def config(self) -> dict:
        return normalise_config(self.state.config)

    def set_config(self, config: dict) -> dict:
        self.state.config = normalise_config(config)
        self._persist_state()
        return self.state.config

    # ---------------- the durable day ----------------
    def today(self) -> str:
        """The device's own calendar day. `%04d-%02d-%02d`, no time zone games."""
        now = self._now()
        return f"{now.year:04d}-{now.month:02d}-{now.day:02d}"

    def _load_state(self) -> DailyCleanupState:
        if self.state_file is None or not self.state_file.is_file():
            return DailyCleanupState()
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DailyCleanupState()
        if not isinstance(data, dict):
            return DailyCleanupState()
        return DailyCleanupState.from_dict(data)

    def _persist_state(self) -> None:
        if self.state_file is None:
            return
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.touch(mode=0o600, exist_ok=True)
            self.state_file.chmod(0o600)
            self.state_file.write_text(json.dumps(self.state.to_dict()), encoding="utf-8")
            os.chmod(self.state_file, 0o600)
        except OSError as exc:
            self.log.warn("could not persist the daily cleanup state", error=str(exc))

    # ---------------- eligibility ----------------
    def eligibility(self) -> tuple[bool, str]:
        """The once-per-day gate, as a pure function of the durable state.

        Returns `(eligible, reason)`. `reason` is empty when eligible, and a
        `DAILY_CLEANUP_SKIP_REASONS` value otherwise — the same value the
        event and the UI report.
        """
        config = self.config()
        if not self.owner.is_owner:
            return False, "NOT_AUTHENTICATED"
        if not config.get("enabled"):
            return False, "DISABLED"
        if self.state.last_cleanup_date == self.today():
            return False, "ALREADY_DONE"
        categories = self._safe_categories()
        if not categories:
            return False, "NO_CATEGORIES"
        return True, ""

    def _safe_categories(self) -> list[str]:
        """The categories this run may touch: daily-safe, allowlisted, unique."""
        config = self.config()
        seen: list[str] = []
        for action_id in config.get("categories") or ():
            if not is_clean_action(action_id):
                continue
            if not is_daily_safe_action(action_id):
                continue
            if action_id == "CLEAN_TRASH" and not config.get("trashEnabled"):
                continue
            if action_id in seen:
                continue
            seen.append(action_id)
        # Trash is opt-in on top of the category list.
        if config.get("trashEnabled") and "CLEAN_TRASH" not in seen:
            seen.append("CLEAN_TRASH")
        return seen

    # ---------------- the run ----------------
    def run_after_owner_authentication(self) -> DailyCleanupOutcome:
        """The whole Phase 5E flow. Called once after the owner authenticates."""
        eligible, reason = self.eligibility()
        if not eligible:
            return self._skip(reason)

        categories = self._safe_categories()
        config = self.config()
        # `CLEAN_OLD_LOGS` needs the configured age; the cleaner takes it as
        # an execution option the agent itself supplies, never from a request.
        options = {
            "logRetentionDays": int(config.get("logRetentionDays") or 30),
        }

        self.owner.record_event(_started_event(self.device_id, categories))
        results: list[CleanResult] = []
        bytes_freed = 0
        items_removed = 0
        errors = 0

        for action_id in categories:
            try:
                result = self.cleaner.execute(action_id, options=options)
            except Exception as exc:  # noqa: BLE001 - one category must not stop the run
                self.log.error("daily cleanup action failed", action=action_id, error=str(exc))
                result = CleanResult(action_id=action_id, status="FAILED", errors=1,
                                     detail=f"agent error: {exc}")
            if not isinstance(result, CleanResult):
                result = CleanResult(action_id=action_id, status="FAILED", errors=1)
            results.append(result)
            bytes_freed += max(0, int(result.bytes_freed))
            items_removed += max(0, int(result.items_removed))
            errors += max(0, int(result.errors))

        today = self.today()
        ok = errors == 0 and all(r.status == "SUCCESS" for r in results)
        partial = not ok and any(r.status == "SUCCESS" for r in results)

        self.state.last_cleanup_date = today
        self.state.last_cleanup_at = time.time()
        self.state.last_result = "completed" if ok else ("partial" if partial else "failed")
        self.owner.set_daily_cleanup_date(today)
        self._persist_state()

        if ok or partial:
            event_type = "daily_cleanup.completed"
            description = (
                f"Daily cleanup completed: {len(results)} action(s), "
                f"{human_bytes(bytes_freed)} freed"
            )
        else:
            event_type = "daily_cleanup.failed"
            description = f"Daily cleanup failed: {errors} action(s) reported an error"

        self.owner.record_event(
            _result_event(self.device_id, event_type, description, bytes_freed, len(results))
        )

        return DailyCleanupOutcome(
            ran=True,
            state=self.owner.state,
            reason=description,
            actions=len(results),
            bytes_freed=bytes_freed,
            items_removed=items_removed,
            errors=errors,
            date=today,
            results=results,
        )

    def _skip(self, reason: str) -> DailyCleanupOutcome:
        label = DAILY_CLEANUP_SKIP_LABELS.get(reason, reason)
        self.owner.record_event(_skipped_event(self.device_id, reason))
        self.log.info("daily cleanup skipped", reason=reason, label=label)
        return DailyCleanupOutcome(ran=False, state=self.owner.state, reason=label)


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def _started_event(device_id: str, categories: Sequence[str]):
    from agents.common.security import SecurityEvent

    return SecurityEvent(
        device_id=device_id,
        event_type="daily_cleanup.started",
        description="Daily cleanup started: "
        + f"{len(categories)} safe categor{'y' if len(categories) == 1 else 'ies'}",
        since=time.time(),
        metadata={"actions": len(categories)},
    )


def _result_event(device_id: str, event_type: str, description: str, bytes_freed: int, actions: int):
    from agents.common.security import SecurityEvent

    return SecurityEvent(
        device_id=device_id,
        event_type=event_type,
        description=description,
        since=time.time(),
        metadata={"bytesFreed": bytes_freed, "actions": actions},
    )


def _skipped_event(device_id: str, reason: str):
    from agents.common.security import SecurityEvent

    return SecurityEvent(
        device_id=device_id,
        event_type="daily_cleanup.skipped",
        description=f"Daily cleanup skipped: {DAILY_CLEANUP_SKIP_LABELS.get(reason, reason)}",
        since=time.time(),
        metadata={"reason": reason},
    )


# ---------------------------------------------------------------------------
# config normalisation — the conservative default is enforced here too
# ---------------------------------------------------------------------------

def normalise_config(raw: Optional[dict]) -> dict:
    """Validate a stored config against the allowlist.

    A `categories` key that is *present* but empty is the owner's explicit
    choice — "no safe categories selected" — and is kept, so the eligibility
    gate reports `NO_CATEGORIES` and the phone's screen explains the skip
    instead of silently arming the default set. Only a config that never had
    the key (or was never written) falls back to the default.
    """
    raw = raw if isinstance(raw, dict) else {}
    has_categories = "categories" in raw and raw["categories"] is not None
    categories: list[str] = []
    for action_id in raw.get("categories") or ():
        if is_daily_safe_action(action_id) and action_id not in categories:
            categories.append(str(action_id))

    if not has_categories:
        categories = list(DEFAULT_DAILY_CLEANUP_CONFIG["categories"])

    trigger = raw.get("trigger")
    if trigger not in ("AFTER_OWNER_LOGIN",):
        trigger = "AFTER_OWNER_LOGIN"

    retention = raw.get("logRetentionDays")
    try:
        retention = int(retention)
    except (TypeError, ValueError):
        retention = 30
    retention = max(1, min(365, retention))

    return {
        "enabled": bool(raw.get("enabled")),
        "trigger": trigger,
        "categories": categories,
        "trashEnabled": bool(raw.get("trashEnabled")),
        "logRetentionDays": retention,
    }


__all__ = [
    "DailyCleanupController",
    "DailyCleanupOutcome",
    "DailyCleanupState",
    "normalise_config",
]
