"""
VIGILIA agent — storage cleanup contract (Phase 4B).

The Python mirror of `shared/src/types.ts` (CLEAN_ACTIONS, CleanActionDescriptor,
CleanResultPayload, CleanupRecommendation) and `shared/src/schemas.ts`
(cleanActionSchema, cleanResultSchema, cleanupRecommendationSchema). The
TypeScript contract is authoritative; the two must stay in sync.

What this contract is, and is not
---------------------------------
A cleanup *action id* names a category of pre-approved, well-known locations
on the device: ``CLEAN_TEMP`` means "the temp directory the analyser already
measures", and nothing else. It is not a path, not a glob, not a file, and it
is not parameterised — the mobile client cannot send anything that would let
it choose what gets deleted. The agent resolves the id to its own fixed roots
on the host it runs on, exactly the way ``APPLICATION_OPEN`` resolves to a
fixed argv.

``downloads`` has no action on purpose. Deleting a user's downloads is not a
safe cleanup, so there is no id for it and therefore no way to request it.

This module holds the contract only. The destructive part lives in
``agents/<platform>/src/cleaner.py`` and is exercised there; the point of
keeping the contract separate is that the allowlist, the labels and the
validation are shared between Linux and Windows, and a test can pin them
without importing a platform module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

#: The cleanup allowlist. This is the entire cleanup surface: an id not in
#: this tuple is rejected by the backend before any frame reaches the agent,
#: and rejected again by the agent before anything touches the filesystem.
#:
#: Phase 5 adds two safe categories: `CLEAN_THUMBNAIL_CACHE` (the file
#: manager's thumbnail cache, regenerable) and `CLEAN_OLD_LOGS` (rotated logs
#: older than an explicit age — never a whole log tree, never an active log).
CLEAN_ACTIONS = (
    "CLEAN_TEMP",
    "CLEAN_BROWSER_CACHE",
    "CLEAN_TRASH",
    "CLEAN_PACKAGE_CACHE",
    "CLEAN_USER_CACHE",
    "CLEAN_THUMBNAIL_CACHE",
    "CLEAN_OLD_LOGS",
)

#: Platforms a cleanup action may be supported on. Used for the honest
#: `NOT_SUPPORTED` answer: an action the host cannot perform is reported as
#: such rather than faked.
CLEAN_PLATFORMS = ("linux", "windows")

#: Ceiling on a recommendation reason, mirrored from the TS contract.
CLEAN_REASON_MAX_LENGTH = 280

#: Result statuses an agent may report for one cleanup action.
CLEAN_STATUSES = ("SUCCESS", "PARTIAL", "FAILED", "NOT_SUPPORTED")

#: Which storage category (Phase 4A) each action cleans. The mapping is fixed
#: so an action id can only ever target a category the analyser measures.
CLEAN_ACTION_CATEGORIES: dict[str, str] = {
    "CLEAN_TEMP": "tmp",
    "CLEAN_BROWSER_CACHE": "browser-cache",
    "CLEAN_TRASH": "trash",
    "CLEAN_PACKAGE_CACHE": "package-cache",
    "CLEAN_USER_CACHE": "user-cache",
    "CLEAN_THUMBNAIL_CACHE": "thumbnail-cache",
    "CLEAN_OLD_LOGS": "old-logs",
}

#: Which platforms support each action. Used to build the descriptor list the
#: mobile client picks from, and to answer `NOT_SUPPORTED` honestly.
CLEAN_ACTION_PLATFORMS: dict[str, tuple[str, ...]] = {
    "CLEAN_TEMP": ("linux", "windows"),
    "CLEAN_BROWSER_CACHE": ("linux", "windows"),
    "CLEAN_TRASH": ("linux", "windows"),
    "CLEAN_PACKAGE_CACHE": ("linux", "windows"),
    "CLEAN_USER_CACHE": ("linux", "windows"),
    "CLEAN_THUMBNAIL_CACHE": ("linux", "windows"),
    "CLEAN_OLD_LOGS": ("linux",),
}

#: Dashboard labels, one per action id. Kept in the shared contract so both
#: platforms describe the same action the same way.
CLEAN_ACTION_LABELS: dict[str, str] = {
    "CLEAN_TEMP": "Temporary Files",
    "CLEAN_BROWSER_CACHE": "Browser Cache",
    "CLEAN_TRASH": "Trash",
    "CLEAN_PACKAGE_CACHE": "Package Cache",
    "CLEAN_USER_CACHE": "User Cache",
    "CLEAN_THUMBNAIL_CACHE": "Thumbnail Cache",
    "CLEAN_OLD_LOGS": "Old Logs",
}

#: Plain-language description of what each action removes. This is what the
#: mobile confirmation sheet shows, so the operator knows what they are
#: agreeing to before anything is deleted.
CLEAN_ACTION_DESCRIPTIONS: dict[str, str] = {
    "CLEAN_TEMP": "Removes cached temporary files from the system temp directory.",
    "CLEAN_BROWSER_CACHE": "Removes cached web data from the predefined safe browser cache location.",
    "CLEAN_TRASH": "Empties the user's trash, deleting files already marked as deleted.",
    "CLEAN_PACKAGE_CACHE": "Removes cached downloaded packages from the package-manager cache.",
    "CLEAN_USER_CACHE": "Removes the user's application cache directory.",
    "CLEAN_THUMBNAIL_CACHE": "Removes the file manager thumbnail cache, which regenerates on demand.",
    "CLEAN_OLD_LOGS": "Removes rotated log files older than the configured age. Active logs are untouched.",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def human_bytes(size: int) -> str:
    """A human-readable byte count for audit lines. Never a path."""
    value = float(max(0, int(size)))
    for unit in ("B", "KB", "MB", "GB"):
        # Divide as we go and stop at the first unit the value fits. TB is
        # the floor: anything bigger still reports in terabytes.
        if value < 1024.0:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def is_clean_action(action: object) -> bool:
    """True only for a known cleanup action id."""
    return isinstance(action, str) and action in CLEAN_ACTIONS


@dataclass
class CleanActionDescriptor:
    """One structured cleanup action the mobile client may preview and confirm."""

    action_id: str
    label: str
    description: str
    estimated_bytes: int = 0
    item_count: int = 0
    platforms: tuple[str, ...] = ("linux", "windows")
    dry_run_supported: bool = True
    estimate_reliable: bool = True

    def to_payload(self) -> dict:
        return {
            "actionId": self.action_id,
            "label": self.label[:48],
            "description": self.description[:280],
            "estimatedBytes": max(0, int(self.estimated_bytes)),
            "itemCount": max(0, int(self.item_count)),
            "platforms": [p for p in self.platforms if p in CLEAN_PLATFORMS],
            "dryRunSupported": bool(self.dry_run_supported),
            "estimateReliable": bool(self.estimate_reliable),
        }

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not is_clean_action(self.action_id):
            issues.append(f"unknown action id: {self.action_id}")
        if not self.label.strip():
            issues.append(f"{self.action_id} has an empty label")
        if not self.description.strip():
            issues.append(f"{self.action_id} has an empty description")
        if self.estimated_bytes < 0:
            issues.append(f"{self.action_id} has a negative estimate")
        if self.item_count < 0:
            issues.append(f"{self.action_id} has a negative item count")
        if not any(p in CLEAN_PLATFORMS for p in self.platforms):
            issues.append(f"{self.action_id} supports no known platform")
        return issues


@dataclass
class CleanResult:
    """Result of one executed cleanup action.

    The wire equivalent of `CleanResultPayload`. Never carries a path, a file
    name or file contents — only counts and a short status line.
    """

    action_id: str
    status: str = "SUCCESS"
    items_removed: int = 0
    bytes_freed: int = 0
    errors: int = 0
    detail: str | None = None

    def to_payload(self, request_id: str = "") -> dict:
        payload: dict = {
            "actionId": self.action_id,
            "status": self.status if self.status in CLEAN_STATUSES else "FAILED",
            "itemsRemoved": max(0, int(self.items_removed)),
            "bytesFreed": max(0, int(self.bytes_freed)),
            "errors": max(0, int(self.errors)),
        }
        if self.detail is not None:
            payload["detail"] = self.detail[:1000]
        if request_id:
            payload["requestId"] = request_id
        return payload

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not is_clean_action(self.action_id):
            issues.append(f"unknown action id: {self.action_id}")
        if self.status not in CLEAN_STATUSES:
            issues.append(f"unknown status: {self.status}")
        for name in ("items_removed", "bytes_freed", "errors"):
            if getattr(self, name) < 0:
                issues.append(f"{self.action_id}: {name} is negative")
        return issues


@dataclass
class CleanupRecommendation:
    """One AI cleanup recommendation (Phase 4C). Advisory only.

    The AI layer may name an action id from the allowlist and give a reason.
    It may not name a path, a shell command, a glob or a file — there is no
    field here that could hold one, and the backend re-validates the action id
    before the recommendation reaches a client.
    """

    action_id: str
    reason: str
    estimated_bytes: int = 0

    def to_payload(self) -> dict:
        return {
            "actionId": self.action_id,
            "reason": self.reason.strip()[:CLEAN_REASON_MAX_LENGTH],
            "estimatedBytes": max(0, int(self.estimated_bytes)),
        }

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not is_clean_action(self.action_id):
            issues.append(f"unknown action id: {self.action_id}")
        reason = self.reason.strip()
        if not reason:
            issues.append(f"{self.action_id}: reason is empty")
        if len(reason) > CLEAN_REASON_MAX_LENGTH:
            issues.append(f"{self.action_id}: reason exceeds {CLEAN_REASON_MAX_LENGTH} chars")
        if self.estimated_bytes < 0:
            issues.append(f"{self.action_id}: negative estimatedBytes")
        return issues


@dataclass
class CleanupPlan:
    """The actions a device offers plus the AI recommendations for them."""

    device_id: str
    actions: list[CleanActionDescriptor] = field(default_factory=list)
    recommendations: list[CleanupRecommendation] = field(default_factory=list)
    timestamp: str = ""

    @classmethod
    def empty(cls, device_id: str) -> "CleanupPlan":
        return cls(device_id=device_id, timestamp=_now())

    def with_timestamp(self) -> "CleanupPlan":
        return CleanupPlan(
            device_id=self.device_id,
            actions=list(self.actions),
            recommendations=list(self.recommendations),
            timestamp=_now(),
        )

    def to_payload(self) -> dict:
        return {
            "deviceId": self.device_id,
            "actions": [a.to_payload() for a in self.actions],
            "recommendations": [r.to_payload() for r in self.recommendations],
            "timestamp": self.timestamp or _now(),
        }

    def validate(self) -> list[str]:
        issues: list[str] = []
        seen: set[str] = set()
        for action in self.actions:
            issues.extend(a for a in action.validate() if a not in issues)
            if action.action_id in seen:
                issues.append(f"duplicate action id: {action.action_id}")
            seen.add(action.action_id)
        for rec in self.recommendations:
            issues.extend(r for r in rec.validate() if r not in issues)
        return issues


#: A category smaller than this is not worth a recommendation. Mirrors the
#: backend's `MIN_RECOMMENDABLE_BYTES` so the agent and the server score the
#: same breakdown the same way.
MIN_RECOMMENDABLE_BYTES = 10 * 1024 * 1024

#: Ceiling on the recommendation list, so a plan cannot flood the UI. Mirrors
#: the backend's `MAX_RECOMMENDATIONS`.
MAX_RECOMMENDATIONS = 5


def score_recommendations(summary, actions) -> list["CleanupRecommendation"]:
    """Derive advisory recommendations from an agent's own measurement.

    Local scoring, not an external model call: the recommendation set is
    computed from the breakdown this agent just took, so it is bound by the
    same `safeToAnalyze` honesty flag and the same allowlist the execution
    path enforces. Both agents call this, so Linux and Windows recommend the
    same way for the same breakdown.

    The caller's own ``actions`` list is the support gate: only an action the
    device actually offers may be recommended, and every ``actionId`` here is
    re-validated by :meth:`CleanupPlan.validate` before it is sent.
    """
    measured = {c.category_id: c for c in (getattr(summary, "categories", None) or [])}
    supported = {a.action_id for a in (actions or [])}
    out: list[CleanupRecommendation] = []
    for action_id in CLEAN_ACTIONS:
        if action_id not in supported:
            continue
        category_id = CLEAN_ACTION_CATEGORIES.get(action_id)
        category = measured.get(category_id) if category_id else None
        if not category or not getattr(category, "safe_to_analyze", False):
            continue
        if getattr(category, "bytes", 0) < MIN_RECOMMENDABLE_BYTES:
            continue
        out.append(
            CleanupRecommendation(
                action_id=action_id,
                reason=f"{CLEAN_ACTION_LABELS.get(action_id, action_id)} is using "
                f"{human_bytes(category.bytes)} of storage.",
                estimated_bytes=int(category.bytes),
            )
        )
    # Largest first: the operator wants the biggest win at the top.
    out.sort(key=lambda r: r.estimated_bytes, reverse=True)
    return out[:MAX_RECOMMENDATIONS]


__all__ = [
    "CLEAN_ACTIONS",
    "CLEAN_PLATFORMS",
    "CLEAN_STATUSES",
    "CLEAN_ACTION_CATEGORIES",
    "CLEAN_ACTION_PLATFORMS",
    "CLEAN_ACTION_LABELS",
    "CLEAN_ACTION_DESCRIPTIONS",
    "CLEAN_REASON_MAX_LENGTH",
    "MIN_RECOMMENDABLE_BYTES",
    "MAX_RECOMMENDATIONS",
    "CleanActionDescriptor",
    "CleanResult",
    "CleanupRecommendation",
    "CleanupPlan",
    "is_clean_action",
    "human_bytes",
    "score_recommendations",
]
