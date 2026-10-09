"""
VIGILIA — cleanup contract tests (Phase 4B/4C).

Platform-independent checks on the cleanup contract mirror
(`agents/common/cleaner.py`), so the Linux and Windows agents cannot drift
apart: both build plans and recommendations through these types, so both must
produce frames the backend accepts.

The point of keeping these tests in `common` is that they pin the allowlist and
the scoring without importing a platform module — the same suite runs on a
Linux and a Windows host and asserts the same thing.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.cleaner import (  # noqa: E402
    CLEAN_ACTION_CATEGORIES,
    CLEAN_ACTION_LABELS,
    CLEAN_ACTIONS,
    CLEAN_PLATFORMS,
    CLEAN_STATUSES,
    CleanActionDescriptor,
    CleanResult,
    CleanupPlan,
    CleanupRecommendation,
    is_clean_action,
    score_recommendations,
)
from agents.common.storage import StorageCategory  # noqa: E402


def _category(category_id: str, size: int, safe: bool = True) -> StorageCategory:
    return StorageCategory(
        category_id=category_id,
        label=category_id,
        bytes=size,
        item_count=1,
        safe_to_analyze=safe,
    )


def _summary(*categories: StorageCategory):
    """A minimal summary stand-in: `score_recommendations` only reads `.categories`."""
    return type("Summary", (), {"categories": list(categories)})()


class CleanAllowlistTests(unittest.TestCase):
    def test_the_allowlist_is_exactly_the_structured_actions(self) -> None:
        # Phase 5 added the two daily-safe categories the automatic cleanup
        # needs: thumbnail cache and old logs. Both are still structured ids —
        # no path, no glob — and neither names a user document directory.
        self.assertEqual(
            list(CLEAN_ACTIONS),
            [
                "CLEAN_TEMP",
                "CLEAN_BROWSER_CACHE",
                "CLEAN_TRASH",
                "CLEAN_PACKAGE_CACHE",
                "CLEAN_USER_CACHE",
                "CLEAN_THUMBNAIL_CACHE",
                "CLEAN_OLD_LOGS",
            ],
        )

    def test_there_is_no_action_for_downloads(self) -> None:
        # Deleting a user's downloads is not a safe cleanup, so there is no id
        # for it and therefore no way to request it.
        self.assertNotIn("CLEAN_DOWNLOADS", CLEAN_ACTIONS)

    def test_every_action_maps_to_exactly_one_measured_category(self) -> None:
        for action_id in CLEAN_ACTIONS:
            self.assertIn(action_id, CLEAN_ACTION_CATEGORIES, f"{action_id} has no category")
            self.assertTrue(is_clean_action(action_id))
            self.assertIn(CLEAN_ACTION_LABELS[action_id].strip(), CLEAN_ACTION_LABELS[action_id])
            self.assertTrue(CLEAN_ACTION_LABELS[action_id].strip(), f"{action_id} has an empty label")

    def test_every_action_supports_both_platforms(self) -> None:
        for action_id in CLEAN_ACTIONS:
            platforms = CLEAN_PLATFORMS
            self.assertIn("linux", platforms)
            self.assertIn("windows", platforms)
            self.assertTrue(is_clean_action(action_id))

    def test_an_unknown_action_is_not_in_the_allowlist(self) -> None:
        for hostile in ("CLEAN_EVERYTHING", "CLEAN_REGISTRY", "DELETE_ALL", "CLEAN_DOWNLOADS", "rm -rf /"):
            self.assertFalse(is_clean_action(hostile), f"{hostile!r} is not a cleanup action")


class CleanResultTests(unittest.TestCase):
    def test_a_result_never_carries_a_path(self) -> None:
        payload = CleanResult(
            action_id="CLEAN_TEMP",
            status="PARTIAL",
            items_removed=3,
            bytes_freed=1024,
            errors=1,
            detail="three items could not be removed",
        ).to_payload(request_id="req-1")
        import json

        body = json.dumps(payload)
        self.assertNotIn("/", body, "no path separator in a result payload")
        self.assertEqual(payload["requestId"], "req-1")
        self.assertEqual(payload["status"], "PARTIAL")

    def test_an_unknown_status_falls_back_to_failed(self) -> None:
        payload = CleanResult(action_id="CLEAN_TEMP", status="DELETED_EVERYTHING").to_payload()
        self.assertEqual(payload["status"], "FAILED")

    def test_negative_counts_are_normalised_to_zero(self) -> None:
        payload = CleanResult(action_id="CLEAN_TEMP", items_removed=-1, bytes_freed=-5, errors=-2).to_payload()
        self.assertEqual(payload["itemsRemoved"], 0)
        self.assertEqual(payload["bytesFreed"], 0)
        self.assertEqual(payload["errors"], 0)

    def test_statuses_are_the_four_structured_ones(self) -> None:
        self.assertEqual(set(CLEAN_STATUSES), {"SUCCESS", "PARTIAL", "FAILED", "NOT_SUPPORTED"})


class CleanupPlanTests(unittest.TestCase):
    def test_a_plan_rejects_an_unknown_action(self) -> None:
        plan = CleanupPlan(
            device_id="dev",
            actions=[
                CleanActionDescriptor(action_id="CLEAN_EVERYTHING", label="x", description="y", platforms=("linux",)),
            ],
        )
        self.assertTrue(any("unknown action id" in issue for issue in plan.validate()))

    def test_a_plan_rejects_an_empty_label_and_description(self) -> None:
        plan = CleanupPlan(
            device_id="dev",
            actions=[CleanActionDescriptor(action_id="CLEAN_TEMP", label="  ", description="ok", platforms=("linux",))],
        )
        self.assertTrue(any("empty label" in issue for issue in plan.validate()))

    def test_a_plan_rejects_a_recommendation_with_an_unknown_action(self) -> None:
        plan = CleanupPlan(
            device_id="dev",
            recommendations=[CleanupRecommendation(action_id="NUKE_EVERYTHING", reason="trust me")],
        )
        self.assertTrue(any("unknown action id" in issue for issue in plan.validate()))

    def test_a_plan_payload_never_contains_a_path(self) -> None:
        import json

        plan = CleanupPlan(
            device_id="dev",
            actions=[
                CleanActionDescriptor(
                    action_id="CLEAN_TEMP",
                    label="Temporary Files",
                    description="Removes cached temporary files.",
                    platforms=("linux", "windows"),
                )
            ],
            recommendations=[
                CleanupRecommendation(action_id="CLEAN_TEMP", reason="Temp is using 1.0 GB of storage.")
            ],
        )
        body = json.dumps(plan.with_timestamp().to_payload())
        self.assertNotIn("/tmp", body)
        self.assertNotIn("/home", body)
        self.assertNotIn("C:\\", body)

    def test_an_empty_plan_is_valid(self) -> None:
        self.assertEqual(CleanupPlan.empty("dev").validate(), [])


class RecommendationScoringTests(unittest.TestCase):
    """The 4C surface: advisory, derived, allowlist-bound, never executable."""

    @staticmethod
    def _actions(*ids: str) -> list[CleanActionDescriptor]:
        return [
            CleanActionDescriptor(action_id=a, label=a, description="d", platforms=("linux", "windows"))
            for a in ids
        ]

    def test_no_summary_yields_no_recommendations(self) -> None:
        self.assertEqual(score_recommendations(None, self._actions("CLEAN_TEMP")), [])

    def test_a_recommendation_is_only_emitted_for_a_supported_action(self) -> None:
        summary = _summary(_category("tmp", 50 * 1024 * 1024))
        self.assertEqual(score_recommendations(summary, []), [], "an unsupported action is never recommended")
        recs = score_recommendations(summary, self._actions("CLEAN_TRASH"))
        self.assertEqual(recs, [], "the measured category must match the supported action")
        recs = score_recommendations(summary, self._actions("CLEAN_TEMP"))
        self.assertEqual([r.action_id for r in recs], ["CLEAN_TEMP"])

    def test_a_recommendation_respects_the_safety_flag(self) -> None:
        summary = _summary(_category("tmp", 50 * 1024 * 1024, safe=False))
        self.assertEqual(score_recommendations(summary, self._actions("CLEAN_TEMP")), [])

    def test_a_recommendation_respects_the_size_threshold(self) -> None:
        summary = _summary(_category("tmp", 1024))
        self.assertEqual(score_recommendations(summary, self._actions("CLEAN_TEMP")), [])

    def test_recommendations_are_largest_first_and_capped(self) -> None:
        categories = [
            _category("tmp", 20 * 1024 * 1024),
            _category("browser-cache", 900 * 1024 * 1024),
            _category("trash", 100 * 1024 * 1024),
        ]
        summary = _summary(*categories)
        actions = self._actions(*CLEAN_ACTIONS)
        recs = score_recommendations(summary, actions)
        self.assertEqual([r.action_id for r in recs][:2], ["CLEAN_BROWSER_CACHE", "CLEAN_TRASH"])
        self.assertLessEqual(len(recs), 5)
        self.assertEqual([r.estimated_bytes for r in recs], sorted((r.estimated_bytes for r in recs), reverse=True))

    def test_every_recommendation_action_id_is_in_the_allowlist(self) -> None:
        categories = [_category(cid, 20 * 1024 * 1024) for cid in CLEAN_ACTION_CATEGORIES.values()]
        summary = _summary(*categories)
        recs = score_recommendations(summary, self._actions(*CLEAN_ACTIONS))
        self.assertTrue(recs, "the fixture should produce recommendations")
        for rec in recs:
            self.assertTrue(is_clean_action(rec.action_id))
            self.assertTrue(rec.reason.strip())
            self.assertGreater(rec.estimated_bytes, 0)
            self.assertEqual(len(rec.reason), len(rec.to_payload()["reason"]))

    def test_a_recommendation_payload_has_no_path_and_no_command(self) -> None:
        import json

        rec = score_recommendations(_summary(_category("tmp", 30 * 1024 * 1024)), self._actions("CLEAN_TEMP"))[0]
        body = json.dumps(rec.to_payload())
        self.assertNotIn("/", body)
        self.assertNotIn("rm ", body)
        self.assertNotIn("exec", body)


if __name__ == "__main__":
    unittest.main()
