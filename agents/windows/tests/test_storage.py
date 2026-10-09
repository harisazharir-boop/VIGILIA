"""
VIGILIA Windows agent — read-only storage analysis tests (Phase 4A).

The collector's calculation is exercised on a Linux host through an injected
`user_dirs` test double — the only way to make the folder walk run off
Windows, because `is_supported()` is the gate that refuses to fabricate
Windows paths. That refusal is itself one of the behaviours under test.

Covered:
  - off-Windows the collector reports an honest empty breakdown, never fakes
  - byte and item counts with the injected tree
  - symlinks (reparse points) are counted, never followed
  - an unreadable folder marks the bucket `safeToAnalyze: False` with a
    partial total, and never raises
  - the walk is bounded
  - no destructive verb exists on the public surface
  - the module uses no shell, no PowerShell, no subprocess
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.logger import Logger  # noqa: E402
from agents.common.storage import STORAGE_CATEGORY_IDS, StorageSummary  # noqa: E402
from agents.windows.src.storage import WindowsStorageCollector  # noqa: E402


def _write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(size)
    return path


class WindowsStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="vigilia-win-storage-")
        self.root = Path(self._tmp)
        self.log = Logger(quiet=True)

    def tearDown(self) -> None:
        for entry in sorted(self.root.rglob("*"), reverse=True):
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                entry.rmdir()

    def _collector(self, tree: Path) -> WindowsStorageCollector:
        return WindowsStorageCollector(
            self.log,
            user_dirs={
                "downloads": tree,
                **{key: self.root / f"empty-{key}" for key in STORAGE_CATEGORY_IDS if key != "downloads"},
            },
        )

    # ---------------- off-Windows honesty ----------------
    def test_off_windows_reports_empty_and_supports_nothing(self) -> None:
        collector = WindowsStorageCollector(self.log)
        if collector.is_supported():
            self.skipTest("this host is Windows; the off-host case cannot be exercised here")
        summary = collector.summarize("dev")
        self.assertEqual(summary.categories, [], "off-Windows nothing is fabricated")
        self.assertEqual(summary.total_bytes, 0)
        self.assertEqual(summary.validate(), [], "the empty report is still a valid frame")

    # ---------------- counting (injected tree) ----------------
    def test_counts_bytes_and_items_across_nested_files(self) -> None:
        tree = self.root / "downloads"
        _write(tree / "report.pdf", 2000)
        _write(tree / "work" / "sheet.xlsx", 800)
        _write(tree / "work" / "2026" / "archive.zip", 1200)

        category = next(
            c for c in self._collector(tree).collect() if c.category_id == "downloads"
        )

        self.assertGreaterEqual(category.bytes, 4000)
        self.assertEqual(category.item_count, 5, "3 files + the 2 folders holding them")
        self.assertTrue(category.safe_to_analyze)

    def test_a_missing_folder_reports_zero_without_failing(self) -> None:
        collector = WindowsStorageCollector(
            self.log, user_dirs={k: self.root / "absent" for k in STORAGE_CATEGORY_IDS}
        )
        summary = collector.summarize("dev")
        for category in summary.categories:
            self.assertEqual(category.bytes, 0)
            self.assertEqual(category.item_count, 0)
        self.assertEqual(summary.validate(), [])

    # ---------------- reparse point / symlink ----------------
    def test_a_symlinked_root_is_refused_not_followed(self) -> None:
        target = self.root / "outside"
        _write(target / "secret.txt", 5000)
        link = self.root / "linked-downloads"
        os.symlink(target, link)

        collector = WindowsStorageCollector(
            self.log,
            user_dirs={
                "downloads": link,
                **{k: self.root / f"e-{k}" for k in STORAGE_CATEGORY_IDS if k != "downloads"},
            },
        )
        category = next(c for c in collector.collect() if c.category_id == "downloads")

        self.assertFalse(category.safe_to_analyze, "a reparse-point root is flagged, not followed")
        self.assertEqual(category.bytes, 0, "the target tree was not counted")

    def test_a_symlink_inside_the_tree_is_not_followed(self) -> None:
        tree = self.root / "downloads"
        _write(tree / "local.bin", 300)
        outside = self.root / "outside"
        _write(outside / "enormous.bin", 5_000_000)
        os.symlink(outside, tree / "junction")

        category = next(c for c in self._collector(tree).collect() if c.category_id == "downloads")

        self.assertLess(category.bytes, 2000, "the target tree was not followed")
        self.assertGreaterEqual(category.bytes, 300)
        self.assertEqual(category.item_count, 2, "1 file + 1 link; the root is not its own entry")

    # ---------------- permission denied ----------------
    def test_an_unreadable_folder_marks_the_bucket_partial(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root bypasses permission bits, so this case cannot be exercised")
        tree = self.root / "downloads"
        _write(tree / "ok.bin", 400)
        _write(tree / "locked" / "hidden.bin", 600)
        locked = tree / "locked"
        os.chmod(locked, 0o000)

        try:
            category = next(c for c in self._collector(tree).collect() if c.category_id == "downloads")
        finally:
            os.chmod(locked, 0o755)

        self.assertFalse(category.safe_to_analyze, "an unreadable folder is a partial measurement")
        self.assertGreater(category.bytes, 0, "the readable part is still counted")
        self.assertLess(category.bytes, 1000, "the unreadable part was not guessed at")

    def test_permission_errors_never_raise(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root bypasses permission bits")
        tree = self.root / "downloads"
        _write(tree / "x.bin", 8)
        os.chmod(tree, 0o000)
        try:
            summary = self._collector(tree).summarize("dev")
        finally:
            os.chmod(tree, 0o755)
        self.assertIsInstance(summary, StorageSummary)

    # ---------------- bounds ----------------
    def test_the_walk_is_bounded_on_entries(self) -> None:
        import agents.windows.src.storage as storage_mod

        tree = self.root / "downloads"
        for index in range(50):
            _write(tree / f"f{index:04d}.bin", 1)
        collector = self._collector(tree)

        original = storage_mod.WALK_MAX_ENTRIES
        storage_mod.WALK_MAX_ENTRIES = 10
        try:
            category = next(c for c in collector.collect() if c.category_id == "downloads")
        finally:
            storage_mod.WALK_MAX_ENTRIES = original

        self.assertFalse(category.safe_to_analyze, "a capped walk is flagged as partial")
        self.assertLessEqual(category.item_count, 12)

    # ---------------- contract ----------------
    def test_every_category_id_is_in_the_wire_contract(self) -> None:
        categories = self._collector(self.root / "downloads").collect()
        for category in categories:
            self.assertIn(category.category_id, STORAGE_CATEGORY_IDS)
            self.assertTrue(category.label)

    def test_the_payload_validates_locally(self) -> None:
        tree = self.root / "downloads"
        _write(tree / "a.bin", 128)
        categories = self._collector(tree).collect()
        summary = StorageSummary(
            device_id="dev", total_bytes=1_000_000, used_bytes=500_000,
            free_bytes=500_000, usage_percent=50.0, categories=categories,
        ).with_timestamp()
        payload = summary.to_payload()
        self.assertEqual(payload["deviceId"], "dev")
        self.assertEqual(payload["usedBytes"], 500_000)
        self.assertEqual(summary.validate(), [], payload)

    # ---------------- read-only ----------------
    def test_no_destructive_verb_exists_on_the_public_surface(self) -> None:
        collector = self._collector(self.root / "downloads")
        self.assertFalse(
            collector.has_destructive_api(),
            "no delete/remove/unlink/rmdir/rename verb may exist",
        )

    def test_the_module_uses_no_shell(self) -> None:
        import importlib
        import inspect

        module = importlib.import_module("agents.windows.src.storage")
        source = inspect.getsource(module)
        # The docstring *mentions* the forbidden tools to forbid them, so
        # check for the calls rather than the words: no child process is ever
        # spawned and no delete verb is ever invoked.
        for verb in (
            "subprocess.",
            "CreateProcess",
            "ShellExecute",
            "Popen(",
            "os.system(",
            "os.remove(",
            "os.unlink(",
            "os.rmdir(",
            "shutil.rmtree(",
            "os.rename(",
        ):
            self.assertNotIn(verb, source, f"the module must not use {verb!r}")


if __name__ == "__main__":
    unittest.main()
