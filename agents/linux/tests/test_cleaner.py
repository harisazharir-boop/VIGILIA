"""
VIGILIA Linux agent — safe storage cleanup tests (Phase 4B).

The cleaner is exercised against a real temp tree the suite builds. Every
test asserts the safety property rather than the happy path only:

  - the allowlist is exactly the five structured action ids
  - an unknown action id is refused
  - a preview never touches the filesystem
  - cleanup removes only the approved root's contents, and keeps the root
  - a symlink inside the tree is unlinked, its target never touched
  - a symlinked root is refused
  - a permission error yields PARTIAL, never an exception
  - the module contains no shell and no subprocess
  - there is no API that accepts a path

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

from agents.common.cleaner import CLEAN_ACTIONS  # noqa: E402
from agents.common.logger import Logger  # noqa: E402
from agents.linux.src.cleaner import LinuxCleaner  # noqa: E402


def _write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(size)
    return path


def _snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


class LinuxCleanerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="vigilia-cleaner-")
        self.root = Path(self._tmp)
        self.log = Logger(quiet=True)
        self.temp_root = self.root / "tmp"
        self.temp_root.mkdir()
        self.outside = self.root / "keep"
        self.outside.mkdir()
        self.cleaner = LinuxCleaner(self.log, user_dirs={"CLEAN_TEMP": [self.temp_root]})

    def tearDown(self) -> None:
        for entry in sorted(self.root.rglob("*"), reverse=True):
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            elif entry.is_dir():
                entry.rmdir()

    # ---------------- allowlist ----------------
    def test_the_allowlist_is_exactly_the_structured_actions(self) -> None:
        # Phase 5 added the two daily-safe categories the automatic cleanup
        # runs: thumbnail cache and old logs. Both are fixed roots on this
        # platform — still no path is ever taken from a request.
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
        self.assertNotIn("CLEAN_DOWNLOADS", CLEAN_ACTIONS)

    def test_an_unknown_action_is_refused(self) -> None:
        result = self.cleaner.execute("CLEAN_EVERYTHING")
        self.assertEqual(result.status, "FAILED")
        self.assertIn("unsupported", (result.detail or ""))

    def test_an_unknown_action_preview_is_refused(self) -> None:
        result = self.cleaner.preview("DELETE_ALL")
        self.assertEqual(result.status, "FAILED")

    # ---------------- no path input surface ----------------
    def test_the_cleaner_exposes_no_path_accepting_api(self) -> None:
        public = [name for name in dir(self.cleaner) if not name.startswith("_")]
        for name in public:
            self.assertFalse(
                any(word in name for word in ("path", "file", "delete", "remove", "unlink")),
                f"the public surface has no path-taking verb: {name}",
            )

    # ---------------- dry run ----------------
    def test_a_preview_does_not_modify_the_filesystem(self) -> None:
        _write(self.temp_root / "a.bin", 4096)
        _write(self.temp_root / "sub" / "b.bin", 2048)
        before = _snapshot(self.root)

        result = self.cleaner.preview("CLEAN_TEMP")

        self.assertEqual(result.status, "SUCCESS")
        # The preview counts exactly the entries the execution removes: both
        # files, plus the `sub` directory once it would be empty. It never
        # counts the root itself, which `execute()` keeps.
        self.assertEqual(result.items_removed, 3)
        # Directory bytes are filesystem-dependent, so only the file bytes are
        # asserted exactly; directories are priced, but their sizes are not.
        self.assertGreaterEqual(result.bytes_freed, 4096 + 2048)
        self.assertEqual(_snapshot(self.root), before, "preview touched nothing")

    # ---------------- execution ----------------
    def test_cleanup_removes_only_the_approved_root_contents(self) -> None:
        _write(self.temp_root / "a.bin", 4096)
        _write(self.temp_root / "nested" / "deep" / "b.bin", 1024)
        _write(self.outside / "keep.bin", 999_999)

        result = self.cleaner.execute("CLEAN_TEMP")

        self.assertEqual(result.status, "SUCCESS")
        # Two files plus the two nested directories that get emptied and
        # removed. The root directory itself is kept, so it is not counted.
        self.assertEqual(result.items_removed, 4)
        self.assertGreaterEqual(result.bytes_freed, 4096 + 1024)
        self.assertTrue((self.outside / "keep.bin").exists(), "a file outside the root survived")
        self.assertTrue(self.temp_root.exists(), "the well-known root directory is kept")
        self.assertEqual(list(self.temp_root.iterdir()), [], "the root is empty")

    def test_cleanup_of_an_empty_root_is_success_with_zero_items(self) -> None:
        result = self.cleaner.execute("CLEAN_TEMP")
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.items_removed, 0)

    def test_a_missing_root_is_success_not_failure(self) -> None:
        cleaner = LinuxCleaner(self.log, user_dirs={"CLEAN_TEMP": [self.root / "does-not-exist"]})
        result = cleaner.execute("CLEAN_TEMP")
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.items_removed, 0)

    # ---------------- symlink safety ----------------
    def test_a_symlink_inside_the_tree_is_unlinked_and_its_target_is_kept(self) -> None:
        target_dir = self.outside / "precious"
        target_dir.mkdir()
        precious = _write(target_dir / "important.bin", 64_000)
        os.symlink(target_dir, self.temp_root / "escape")

        result = self.cleaner.execute("CLEAN_TEMP")

        self.assertEqual(result.status, "SUCCESS")
        self.assertTrue(precious.exists(), "the symlink target was never deleted")
        self.assertFalse((self.temp_root / "escape").exists(), "the symlink itself was removed")

    def test_a_symlinked_root_is_refused(self) -> None:
        real = self.root / "real"
        real.mkdir()
        _write(real / "a.bin", 128)
        link = self.root / "link"
        os.symlink(real, link)
        cleaner = LinuxCleaner(self.log, user_dirs={"CLEAN_TEMP": [link]})

        result = cleaner.execute("CLEAN_TEMP")

        self.assertEqual(result.status, "PARTIAL")
        self.assertTrue((real / "a.bin").exists(), "a symlinked root was not followed")
        self.assertEqual(result.errors, 1)

    def test_a_symlinked_root_preview_reports_nothing(self) -> None:
        real = self.root / "real"
        real.mkdir()
        _write(real / "a.bin", 128)
        link = self.root / "link"
        os.symlink(real, link)
        cleaner = LinuxCleaner(self.log, user_dirs={"CLEAN_TEMP": [link]})

        result = cleaner.preview("CLEAN_TEMP")
        self.assertEqual(result.items_removed, 0, "a symlinked root is not walked even for a preview")

    # ---------------- partial ----------------
    def test_an_unreadable_subdirectory_yields_partial_not_failure(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root bypasses permission bits, so the test cannot fail")
        _write(self.temp_root / "a.bin", 512)
        denied = self.temp_root / "denied"
        _write(denied / "b.bin", 256)
        # A mode of 0 denies even the owner, so the directory cannot be read
        # or written by this process. `S_IRWXU` alone would still grant the
        # owner write access and the cleanup would legitimately empty it.
        os.chmod(denied, 0)

        result = self.cleaner.execute("CLEAN_TEMP")

        os.chmod(denied, stat.S_IRWXU)
        self.assertEqual(result.status, "PARTIAL")
        self.assertEqual(result.errors, 1)
        self.assertTrue((self.temp_root / "a.bin").exists() is False, "the readable file was removed")
        self.assertTrue((denied / "b.bin").exists(), "the unreadable file was not deleted")

    def test_a_removed_file_is_counted_and_bytes_are_reported(self) -> None:
        _write(self.temp_root / "one.bin", 1_000_000)
        _write(self.temp_root / "two.bin", 2_000_000)
        result = self.cleaner.execute("CLEAN_TEMP")
        self.assertEqual(result.items_removed, 2)
        self.assertEqual(result.bytes_freed, 3_000_000)

    # ---------------- bounds ----------------
    def test_the_walk_is_bounded_on_entries(self) -> None:
        for index in range(300):
            _write(self.temp_root / f"f{index:04d}.bin", 1)
        cleaner = LinuxCleaner(
            self.log,
            user_dirs={"CLEAN_TEMP": [self.temp_root]},
            remover=lambda path: None,  # count only, do not delete
        )
        result = cleaner.execute("CLEAN_TEMP")
        # The cap applies to what the walk *removes*, and the module-level cap
        # is far above this tree. The bound is exercised by asserting the
        # count instead of a deletion: every entry the walk saw was counted,
        # and nothing was skipped.
        self.assertEqual(result.items_removed, 300)
        self.assertEqual(result.skipped if hasattr(result, "skipped") else 0, 0)

    def test_the_walk_stops_at_the_entries_cap(self) -> None:
        # The real bound: inject a cap the tree exceeds, so the walk must stop
        # before it has seen everything. The default remover really deletes,
        # so the entries the walk never reached survive on disk.
        import agents.linux.src.cleaner as cleaner_module

        for index in range(300):
            _write(self.temp_root / f"g{index:04d}.bin", 1)
        cleaner = LinuxCleaner(
            self.log,
            user_dirs={"CLEAN_TEMP": [self.temp_root]},
        )
        original = cleaner_module.CLEAN_MAX_ENTRIES
        cleaner_module.CLEAN_MAX_ENTRIES = 40
        try:
            result = cleaner.execute("CLEAN_TEMP")
        finally:
            cleaner_module.CLEAN_MAX_ENTRIES = original
        self.assertEqual(result.items_removed, 40, "the walk stops at the cap")
        remaining = len(list(self.temp_root.iterdir()))
        self.assertLess(remaining, 300, "the walk did not reach the whole tree")
        self.assertGreater(remaining, 0, "unreached entries survived")

    # ---------------- no shell ----------------
    def test_the_module_uses_no_shell_and_no_subprocess(self) -> None:
        import importlib
        import inspect

        module = importlib.import_module("agents.linux.src.cleaner")
        source = inspect.getsource(module)
        for forbidden in ("subprocess", "os.system", "shlex", "shell=True", "popen", "/bin/sh", "bash -c"):
            self.assertNotIn(forbidden, source, f"the cleaner must not use {forbidden!r}")

    def test_deletion_uses_only_os_filesystem_calls(self) -> None:
        import importlib
        import inspect

        module = importlib.import_module("agents.linux.src.cleaner")
        source = inspect.getsource(module)
        # `shutil.rmtree` is deliberately absent: the walk drives the
        # accounting so every removal is bounded and counted.
        self.assertNotIn("shutil.rmtree", source)
        self.assertNotIn("os.remove(", source)
        self.assertIn("os.unlink(", source, "deletion goes through os.unlink")
        self.assertIn("os.rmdir(", source, "directories are removed with os.rmdir")

    # ---------------- descriptors ----------------
    def test_describe_prices_actions_from_the_storage_report(self) -> None:
        from agents.common.storage import StorageCategory

        categories = [
            StorageCategory(category_id="tmp", label="Temporary files", bytes=5_000, item_count=9, safe_to_analyze=True),
            StorageCategory(category_id="downloads", label="Downloads", bytes=99_000, item_count=2, safe_to_analyze=True),
        ]
        descriptors = self.cleaner.describe(categories)
        by_id = {d.action_id: d for d in descriptors}
        self.assertEqual(by_id["CLEAN_TEMP"].estimated_bytes, 5_000)
        self.assertEqual(by_id["CLEAN_TEMP"].item_count, 9)
        self.assertTrue(by_id["CLEAN_TEMP"].estimate_reliable)
        self.assertEqual(by_id["CLEAN_TRASH"].estimated_bytes, 0, "an unmeasured category prices at zero")
        self.assertFalse(by_id["CLEAN_TRASH"].estimate_reliable)

    def test_describe_never_emits_a_path(self) -> None:
        import json

        descriptors = self.cleaner.describe([])
        payload = json.dumps([d.to_payload() for d in descriptors])
        self.assertNotIn("/tmp", payload)
        self.assertNotIn("/home", payload)
        self.assertNotIn("/var", payload)

    def test_describe_supports_every_action_in_the_allowlist(self) -> None:
        descriptors = self.cleaner.describe([])
        ids = {d.action_id for d in descriptors}
        self.assertEqual(ids, set(CLEAN_ACTIONS))

    # ---------------- command surface ----------------
    def test_the_executor_runs_a_cleanup_and_reports_succeeded(self) -> None:
        from agents.linux.src.commands import CommandExecutor

        _write(self.temp_root / "a.bin", 4_096)
        executor = CommandExecutor(self.log, cleaner=self.cleaner)

        outcome = executor.execute("req-1", "CLEAN_TEMP")

        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.command, "CLEAN_TEMP")
        self.assertIsNone(outcome.target)
        self.assertIn("CLEAN_TEMP SUCCESS", outcome.result or "")
        self.assertFalse((self.temp_root / "a.bin").exists())

    def test_the_executor_reports_failed_for_an_unknown_cleanup(self) -> None:
        from agents.linux.src.commands import CommandExecutor

        executor = CommandExecutor(self.log, cleaner=self.cleaner)
        outcome = executor.execute("req-2", "CLEAN_REGISTRY")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("unsupported", outcome.result or "")

    def test_an_executor_without_a_cleaner_reports_failed_honestly(self) -> None:
        from agents.linux.src.commands import CommandExecutor

        executor = CommandExecutor(self.log)
        outcome = executor.execute("req-3", "CLEAN_TEMP")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("not available", outcome.result or "")

    def test_the_power_command_path_is_unaffected_by_the_cleaner(self) -> None:
        from agents.common.telemetry import COMMAND_ALLOWLIST
        from agents.linux.src.commands import CommandExecutor

        executor = CommandExecutor(self.log, cleaner=self.cleaner)
        # The command executor still serves the Phase 2 allowlist; cleanup is
        # served alongside it, not instead of it.
        self.assertEqual(set(executor.supported_commands()), set(COMMAND_ALLOWLIST))


if __name__ == "__main__":
    unittest.main()
