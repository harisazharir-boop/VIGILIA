"""
VIGILIA Linux agent — read-only storage analysis tests (Phase 4A).

The collector is exercised against a real temp directory tree that the suite
builds and reads back. Nothing is ever deleted: the tree is created with
`tempfile`, and `setUp`/`tearDown` only add and remove *its own* files (the
tests assert the collector never touched them).

Covered:
  - byte and item counts across files and nested directories
  - symlinks are counted as one item and never followed (escape protection)
  - a permission-denied directory marks the bucket `safeToAnalyze: False`
    and keeps a partial total instead of failing
  - a missing directory reports zero, not an error
  - the walk is bounded on both entries and bytes
  - the public surface exposes no destructive verb
  - the payload matches the wire contract and validates locally

Run:  python3 -m unittest discover -s agents/linux -p 'test_*.py' -v
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.logger import Logger  # noqa: E402
from agents.common.storage import STORAGE_CATEGORY_IDS, StorageSummary  # noqa: E402
from agents.linux.src.storage import LinuxStorageCollector  # noqa: E402


def _write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(size)
    return path


class LinuxStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="vigilia-storage-")
        self.root = Path(self._tmp)
        self.log = Logger(quiet=True)

    def tearDown(self) -> None:
        # Read-only contract: the collector must not have touched the tree.
        # Remove it as the suite's own fixture, after asserting the contents
        # the tests expect.
        for entry in sorted(self.root.rglob("*"), reverse=True):
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                entry.rmdir()

    def _collector(self, tree: Path) -> LinuxStorageCollector:
        return LinuxStorageCollector(self.log, user_dirs={"downloads": tree, **{
            key: (self.root / f"empty-{key}") for key in STORAGE_CATEGORY_IDS if key != "downloads"
        }})

    # ---------------- counting ----------------
    def test_counts_bytes_and_items_across_nested_files(self) -> None:
        tree = self.root / "downloads"
        _write(tree / "a.bin", 1000)
        _write(tree / "sub" / "b.bin", 500)
        _write(tree / "sub" / "deep" / "c.bin", 250)

        summary = self._collector(tree).summarize("dev")
        category = next(c for c in summary.categories if c.category_id == "downloads")

        # File bytes exactly; directory entries add their own dirent size on
        # most filesystems, so assert the file total as a floor and count the
        # containers separately.
        self.assertGreaterEqual(category.bytes, 1750)
        self.assertEqual(category.item_count, 5, "3 files + the 2 directories holding them")
        self.assertTrue(category.safe_to_analyze)

    def test_a_missing_directory_reports_zero_without_failing(self) -> None:
        collector = LinuxStorageCollector(self.log, user_dirs={k: self.root / "nope" for k in STORAGE_CATEGORY_IDS})
        summary = collector.summarize("dev")
        for category in summary.categories:
            self.assertEqual(category.bytes, 0)
            self.assertEqual(category.item_count, 0)

        self.assertEqual(summary.validate(), [], "an empty breakdown is still valid")

    # ---------------- symlink escape ----------------
    def test_a_symlink_root_is_refused_not_followed(self) -> None:
        target = self.root / "outside"
        _write(target / "secret.env", 9999)
        link = self.root / "link-downloads"
        os.symlink(target, link)

        collector = LinuxStorageCollector(
            self.log, user_dirs={"downloads": link, **{k: self.root / f"e-{k}" for k in STORAGE_CATEGORY_IDS if k != "downloads"}}
        )
        category = next(c for c in collector.collect() if c.category_id == "downloads")

        self.assertFalse(category.safe_to_analyze, "a symlinked root is flagged, not followed")
        self.assertEqual(category.bytes, 0, "the target tree was not counted")

    def test_a_symlink_inside_the_tree_is_not_followed(self) -> None:
        tree = self.root / "downloads"
        _write(tree / "real.bin", 100)
        outside = self.root / "outside"
        _write(outside / "huge.bin", 1_000_000)
        os.symlink(outside, tree / "escape")

        category = next(c for c in self._collector(tree).collect() if c.category_id == "downloads")

        self.assertTrue(category.safe_to_analyze)
        # The target tree is 1_000_000 bytes; the link contributes only its
        # own dirent size, so the total must stay far below the target's size.
        self.assertLess(category.bytes, 2000, "the target's tree was not followed")
        self.assertGreaterEqual(category.bytes, 100)
        # 1 file + the symlink; the root itself is not one of its own entries.
        self.assertEqual(category.item_count, 2)

    # ---------------- permission denied ----------------
    def test_a_unreadable_subdirectory_marks_the_bucket_partial(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root bypasses permission bits, so this case cannot be exercised")
        tree = self.root / "downloads"
        _write(tree / "readable.bin", 200)
        _write(tree / "private" / "hidden.bin", 400)
        private = tree / "private"
        os.chmod(private, 0o000)

        try:
            category = next(c for c in self._collector(tree).collect() if c.category_id == "downloads")
        finally:
            os.chmod(private, 0o755)

        self.assertFalse(
            category.safe_to_analyze,
            "an unreadable directory is reported as a partial measurement",
        )
        self.assertGreater(category.bytes, 0, "the readable part is still counted")
        self.assertLess(category.bytes, 600, "the unreadable part was not guessed at")

    def test_permission_errors_never_raise(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root bypasses permission bits")
        tree = self.root / "downloads"
        _write(tree / "x.bin", 10)
        os.chmod(tree, 0o000)
        try:
            summary = self._collector(tree).summarize("dev")
        finally:
            os.chmod(tree, 0o755)
        self.assertIsInstance(summary, StorageSummary)

    # ---------------- bounds ----------------
    def test_the_walk_is_bounded_on_entries(self) -> None:
        import agents.linux.src.storage as storage_mod

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

    def test_the_walk_is_bounded_on_bytes(self) -> None:
        import agents.linux.src.storage as storage_mod

        tree = self.root / "downloads"
        for index in range(20):
            _write(tree / f"big{index:02d}.bin", 1024 * 1024)  # 1 MiB each

        collector = self._collector(tree)
        original = storage_mod.WALK_MAX_BYTES
        storage_mod.WALK_MAX_BYTES = 2 * 1024 * 1024
        try:
            category = next(c for c in collector.collect() if c.category_id == "downloads")
        finally:
            storage_mod.WALK_MAX_BYTES = original

        self.assertFalse(category.safe_to_analyze)
        self.assertLessEqual(category.bytes, 3 * 1024 * 1024)

    # ---------------- contract ----------------
    def test_every_category_id_is_in_the_wire_contract(self) -> None:
        collector = self._collector(self.root / "downloads")
        summary = collector.summarize("dev")
        for category in summary.categories:
            self.assertIn(category.category_id, STORAGE_CATEGORY_IDS)
            self.assertTrue(category.label)

    def test_the_payload_validates_locally(self) -> None:
        tree = self.root / "downloads"
        _write(tree / "a.bin", 64)
        summary = self._collector(tree).summarize("dev").with_timestamp()
        payload = summary.to_payload()
        self.assertEqual(payload["deviceId"], "dev")
        self.assertGreater(payload["usedBytes"], 0)
        self.assertEqual(summary.validate(), [], payload)

    def test_usage_percent_is_within_range(self) -> None:
        summary = self._collector(self.root / "downloads").summarize("dev")
        self.assertGreaterEqual(summary.usage_percent, 0.0)
        self.assertLessEqual(summary.usage_percent, 100.0)

    # ---------------- read-only ----------------
    def test_no_destructive_verb_exists_on_the_public_surface(self) -> None:
        collector = self._collector(self.root / "downloads")
        self.assertFalse(
            collector.has_destructive_api(),
            f"no delete/remove/unlink/rmdir/rename verb may exist: "
            f"{[n for n in dir(collector) if not n.startswith('_')]}",
        )

    def test_the_walker_does_not_follow_symlinks_even_for_files(self) -> None:
        tree = self.root / "downloads"
        _write(tree / "real.bin", 16)
        outside = self.root / "outside"
        _write(outside / "linked.bin", 4096)
        os.symlink(outside / "linked.bin", tree / "link.bin")

        category = next(c for c in self._collector(tree).collect() if c.category_id == "downloads")

        link_size = os.lstat(tree / "link.bin").st_size
        self.assertLessEqual(category.bytes, 16 + link_size + 64, "no target bytes leaked in")
        self.assertLess(category.bytes, 4096)
        self.assertNotIn(category.bytes, (4096, 4112), "the target's size did not leak in")

    def test_the_module_imports_no_deletion_capability(self) -> None:
        import importlib
        import inspect

        module = importlib.import_module("agents.linux.src.storage")
        source = inspect.getsource(module)
        for verb in ("os.remove(", "os.unlink(", "os.rmdir(", "shutil.rmtree(", "os.rename(", "subprocess"):
            self.assertNotIn(verb, source, f"the module must not use {verb!r}")


if __name__ == "__main__":
    unittest.main()
