"""
VIGILIA — storage analysis contract tests (Phase 4A).

Platform-independent checks on the wire contract mirror
(`agents/common/storage.py`), so the Linux and Windows collectors cannot drift
apart: both build payloads through these classes, so both must produce frames
the backend accepts.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.storage import (  # noqa: E402
    STORAGE_CATEGORY_CAP,
    STORAGE_CATEGORY_IDS,
    StorageCategory,
    StorageSummary,
)


class StorageContractTests(unittest.TestCase):
    def test_category_ids_are_fixed_and_complete(self) -> None:
        # `thumbnail-cache` and `old-logs` were added in Phase 5: the daily
        # cleanup measures what it may delete, and those are the two
        # categories the automatic run targets.
        self.assertEqual(
            set(STORAGE_CATEGORY_IDS),
            {
                "downloads",
                "user-cache",
                "tmp",
                "trash",
                "package-cache",
                "browser-cache",
                "thumbnail-cache",
                "old-logs",
                "other",
            },
        )

    def test_an_unknown_category_id_is_rejected(self) -> None:
        summary = StorageSummary(
            device_id="dev",
            categories=[StorageCategory(category_id="etc-passwd", label="secrets")],
        )
        self.assertIn("unknown category id", " ".join(summary.validate()))

    def test_negative_byte_and_item_counts_are_rejected(self) -> None:
        summary = StorageSummary(
            device_id="dev",
            categories=[
                StorageCategory(category_id="downloads", label="Downloads", bytes=-1, item_count=-1)
            ],
        )
        issues = " ".join(summary.validate())
        self.assertIn("negative bytes", issues)
        self.assertIn("negative item count", issues)

    def test_more_categories_than_the_cap_is_rejected(self) -> None:
        categories = [
            StorageCategory(category_id="other", label=f"c-{i}") for i in range(STORAGE_CATEGORY_CAP + 1)
        ]
        summary = StorageSummary(device_id="dev", categories=categories)
        self.assertIn("too many categories", " ".join(summary.validate()))

    def test_usage_percent_out_of_range_is_rejected(self) -> None:
        self.assertIn("out of range", " ".join(StorageSummary(device_id="d", usage_percent=150.0).validate()))
        self.assertIn("out of range", " ".join(StorageSummary(device_id="d", usage_percent=-1.0).validate()))

    def test_used_exceeding_total_is_rejected(self) -> None:
        summary = StorageSummary(device_id="d", total_bytes=100, used_bytes=200)
        self.assertIn("usedBytes exceeds totalBytes", " ".join(summary.validate()))

    def test_an_empty_label_is_rejected(self) -> None:
        summary = StorageSummary(
            device_id="d", categories=[StorageCategory(category_id="downloads", label="  ")]
        )
        self.assertIn("empty label", " ".join(summary.validate()))

    def test_a_valid_breakdown_has_no_issues(self) -> None:
        summary = StorageSummary(
            device_id="dev",
            total_bytes=1_000_000,
            used_bytes=400_000,
            free_bytes=600_000,
            usage_percent=40.0,
            categories=[
                StorageCategory(category_id="downloads", label="Downloads", bytes=1000, item_count=1),
                StorageCategory(category_id="user-cache", label="User cache", bytes=500, item_count=2),
            ],
        )
        self.assertEqual(summary.validate(), [])

    def test_the_payload_shape_matches_the_wire_contract(self) -> None:
        summary = StorageSummary(
            device_id="dev",
            total_bytes=1_000_000,
            used_bytes=400_000,
            free_bytes=600_000,
            usage_percent=40.0,
            categories=[
                StorageCategory(
                    category_id="downloads", label="Downloads", bytes=1000, item_count=1, safe_to_analyze=False
                )
            ],
        ).with_timestamp()
        payload = summary.to_payload()
        self.assertEqual(payload["deviceId"], "dev")
        self.assertEqual(payload["totalBytes"], 1_000_000)
        self.assertEqual(payload["usedBytes"], 400_000)
        self.assertEqual(payload["freeBytes"], 600_000)
        self.assertEqual(payload["usagePercent"], 40.0)
        self.assertEqual(payload["categories"][0]["categoryId"], "downloads")
        self.assertEqual(payload["categories"][0]["safeToAnalyze"], False)
        self.assertTrue(payload["timestamp"].endswith("Z"))

    def test_the_payload_is_clamped_never_wraps(self) -> None:
        summary = StorageSummary(
            device_id="dev",
            usage_percent=10_000.0,
            categories=[StorageCategory(category_id="downloads", label="d", bytes=-5, item_count=-5)],
        )
        payload = summary.to_payload()
        self.assertEqual(payload["usagePercent"], 100.0, "clamped, not wrapped")
        self.assertEqual(payload["categories"][0]["bytes"], 0)
        self.assertEqual(payload["categories"][0]["itemCount"], 0)

    def test_with_timestamp_is_non_mutating(self) -> None:
        summary = StorageSummary(device_id="dev")
        stamped = summary.with_timestamp()
        self.assertNotEqual(summary.timestamp, stamped.timestamp)
        self.assertNotEqual(id(summary), id(stamped))


if __name__ == "__main__":
    unittest.main()
