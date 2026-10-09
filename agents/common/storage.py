"""
VIGILIA agent — storage analysis contract (Phase 4A).

The Python mirror of `shared/src/types.ts`
(StorageSummary / StorageCategory) and `shared/src/schemas.ts`
(storageSummarySchema / storageCategorySchema). The TypeScript contract is
authoritative; the two must stay in sync.

This is read-only *analysis*. It reports how much space a fixed list of safe,
well-known locations occupy. It carries no file name, no path and no file
content, and nothing in it is deletable: there is no delete, unlink, rmdir or
cleanup verb anywhere in this phase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

#: Category ids the wire contract accepts. The agent must use these exact
#: values; `other` is the escape hatch for a location not in the list.
STORAGE_CATEGORY_IDS = (
    "downloads",
    "user-cache",
    "tmp",
    "trash",
    "package-cache",
    "browser-cache",
    "thumbnail-cache",
    "old-logs",
    "other",
)

#: Ceiling the schema accepts, mirrored from the TS contract so a collector on
#: a hostile host cannot emit a frame the backend rejects.
STORAGE_CATEGORY_CAP = 32
STORAGE_CATEGORY_LABEL_MAX_LENGTH = 48


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class StorageCategory:
    """One bucket of disk usage as the agent measured it."""

    category_id: str
    label: str
    bytes: int = 0
    item_count: int = 0
    safe_to_analyze: bool = True

    def to_payload(self) -> dict:
        return {
            "categoryId": self.category_id,
            "label": self.label[:STORAGE_CATEGORY_LABEL_MAX_LENGTH],
            "bytes": max(0, int(self.bytes)),
            "itemCount": max(0, int(self.item_count)),
            "safeToAnalyze": bool(self.safe_to_analyze),
        }


@dataclass
class StorageSummary:
    """The whole breakdown plus the filesystem totals it is measured against."""

    device_id: str
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    usage_percent: float = 0.0
    categories: list[StorageCategory] = field(default_factory=list)
    timestamp: str = ""

    @classmethod
    def empty(cls, device_id: str) -> "StorageSummary":
        return cls(device_id=device_id, timestamp=_now())

    def with_timestamp(self) -> "StorageSummary":
        return StorageSummary(
            device_id=self.device_id,
            total_bytes=self.total_bytes,
            used_bytes=self.used_bytes,
            free_bytes=self.free_bytes,
            usage_percent=self.usage_percent,
            categories=list(self.categories),
            timestamp=_now(),
        )

    def to_payload(self) -> dict:
        return {
            "deviceId": self.device_id,
            "totalBytes": max(0, int(self.total_bytes)),
            "usedBytes": max(0, int(self.used_bytes)),
            "freeBytes": max(0, int(self.free_bytes)),
            "usagePercent": round(max(0.0, min(100.0, float(self.usage_percent))), 2),
            "categories": [c.to_payload() for c in self.categories[:STORAGE_CATEGORY_CAP]],
            "timestamp": self.timestamp or _now(),
        }

    def validate(self) -> list[str]:
        """Local pre-send validation; mirrors the backend schema."""
        issues: list[str] = []
        if len(self.categories) > STORAGE_CATEGORY_CAP:
            issues.append(f"too many categories ({len(self.categories)} > {STORAGE_CATEGORY_CAP})")
        seen: set[str] = set()
        for category in self.categories:
            if category.category_id not in STORAGE_CATEGORY_IDS:
                issues.append(f"unknown category id: {category.category_id}")
                continue
            if category.category_id in seen:
                issues.append(f"duplicate category id: {category.category_id}")
            seen.add(category.category_id)
            if not category.label or not category.label.strip():
                issues.append(f"category {category.category_id} has an empty label")
            if category.bytes < 0:
                issues.append(f"category {category.category_id} has negative bytes")
            if category.item_count < 0:
                issues.append(f"category {category.category_id} has a negative item count")
        if self.total_bytes < 0 or self.used_bytes < 0 or self.free_bytes < 0:
            issues.append("storage totals cannot be negative")
        if self.total_bytes and self.used_bytes > self.total_bytes:
            issues.append("usedBytes exceeds totalBytes")
        if not (0.0 <= self.usage_percent <= 100.0):
            issues.append(f"usagePercent out of range: {self.usage_percent}")
        return issues


__all__ = [
    "STORAGE_CATEGORY_CAP",
    "STORAGE_CATEGORY_IDS",
    "STORAGE_CATEGORY_LABEL_MAX_LENGTH",
    "StorageCategory",
    "StorageSummary",
]
