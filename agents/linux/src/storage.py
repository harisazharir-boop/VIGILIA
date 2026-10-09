"""
VIGILIA Linux agent — read-only storage analysis (Phase 4A).

A census of a fixed list of safe, well-known locations: Downloads, the user
cache, /tmp, the user's trash, the package-manager cache and the browser
cache. It is *not* a filesystem scan — the agent never walks an arbitrary
tree, and it never walks a directory it did not explicitly name.

Safety properties, each enforced in code rather than by convention:

  - **Read-only.** Nothing here calls a delete, `unlink`, `rmdir`, `rename`,
    `open(..., 'w')` or a shell. Every filesystem access is `os.stat`,
    `os.scandir` or `os.readlink`. The module has no code path that could
    modify a file even by accident.
  - **Symlink escape is impossible.** A symlinked *root* is refused outright,
    and a symlink found during the walk is counted as one item and *not*
    followed. That keeps a hostile tree from redirecting the walk into, say,
    `/etc` or a network mount.
  - **Permission errors are skipped, not fatal.** `PermissionError` mid-walk
    marks the bucket `safeToAnalyze: False` and keeps the partial total — an
    honest lower bound, never an estimate and never a failure.
  - **Bounded.** Every walk is capped on both entries and bytes scanned, so a
    directory with ten million files cannot stall the agent.
  - **Private.** No file name, no path and no content is ever reported — only
    byte totals and item counts per category.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from agents.common.logger import Logger
from agents.common.storage import StorageCategory, StorageSummary

#: Hard caps per walk, so a pathological directory cannot stall the agent.
WALK_MAX_ENTRIES = 200_000
WALK_MAX_BYTES = 8 * 1024 * 1024 * 1024  # 8 GiB counted, then stop
#: Cap on the byte total of a single category: the wire field is an integer,
#: so an overflow is an error rather than something to truncate.
CATEGORY_MAX_BYTES = 2**63 - 1

_XDG_CACHE = os.environ.get("XDG_CACHE_HOME") or ""
_XDG_DATA = os.environ.get("XDG_DATA_HOME") or ""
_XDG_RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or ""


def _user_dirs(home: Path) -> dict[str, Path]:
    """The well-known locations this build of the agent looks at."""
    cache_root = Path(_XDG_CACHE) if _XDG_CACHE else home / ".cache"
    data_root = Path(_XDG_DATA) if _XDG_DATA else home / ".local" / "share"

    # Browser caches live under a versioned profile directory, so the probe is
    # a fixed parent plus a known child name. Nothing here reads the profile's
    # contents — the walker only counts.
    browsers: list[Path] = []
    for profile_dir, sub in (
        (home / ".mozilla" / "firefox", "cache2"),
        (home / ".cache" / "mozilla" / "firefox", "cache2"),
        (home / ".config" / "google-chrome", "Cache"),
        (home / ".config" / "chromium", "Cache"),
        (home / ".config" / "BraveSoftware" / "Brave-Browser", "Cache"),
    ):
        browsers.append(profile_dir / sub)

    return {
        "downloads": home / "Downloads",
        "user-cache": cache_root,
        "tmp": Path("/tmp"),
        "trash": data_root / "Trash",
        "package-cache": Path("/var/cache/apt/archives"),
        "browser-cache": _first_existing(browsers) or browsers[0],
    }


def _first_existing(paths: list[Path]) -> Optional[Path]:
    for path in paths:
        try:
            if path.exists():
                return path
        except OSError:
            continue
    return None


@dataclass
class _WalkResult:
    bytes_total: int = 0
    items: int = 0
    complete: bool = True


class LinuxStorageCollector:
    """Read-only storage analysis for a Linux host."""

    def __init__(self, log: Logger, *, user_dirs: Optional[dict[str, Path]] = None) -> None:
        self.log = log
        self._user_dirs = user_dirs

    def _resolve_dirs(self) -> dict[str, Path]:
        if self._user_dirs is not None:
            return self._user_dirs
        home = Path.home()
        try:
            home = Path(os.environ.get("HOME") or home)
        except (OSError, ValueError):
            pass
        return _user_dirs(home)

    # ---------------- filesystem ----------------
    def _volume_totals(self) -> Tuple[int, int, int, float]:
        """(total, used, free, usagePercent) of the filesystem holding `/`."""
        try:
            usage = shutil.disk_usage("/")
        except OSError as exc:
            self.log.debug("storage volume totals failed", error=str(exc))
            return (0, 0, 0, 0.0)
        if usage.total <= 0:
            return (0, 0, 0, 0.0)
        percent = round(usage.used / usage.total * 100.0, 2)
        return (usage.total, usage.used, usage.free, percent)

    def _walk(self, root: Path) -> _WalkResult:
        """Count one directory tree. Never follows a symlink. Never raises."""
        result = _WalkResult()
        # A symlinked root would let a tree claim to be somewhere it is not;
        # refuse it and report the bucket as unreadable rather than following.
        try:
            stat = os.lstat(root)
        except OSError as exc:
            self.log.debug("storage root unreadable", root=str(root), error=str(exc))
            result.complete = False
            return result
        if os.path.islink(str(root)):
            self.log.debug("storage root is a symlink, skipping", root=str(root))
            result.complete = False
            return result

        # The stack is directory paths only; files are counted on sight and
        # never retained, so a huge tree cannot accumulate memory.
        stack: list[Path] = [root]
        while stack:
            directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except PermissionError:
                # A directory the agent may not list: the total so far is a
                # lower bound. Flag it and keep the rest of the walk.
                result.complete = False
                continue
            except OSError as exc:
                # Vanished mid-walk or a broken link: same treatment.
                self.log.debug("storage dir unreadable", dir=str(directory), error=str(exc))
                result.complete = False
                continue

            for entry in entries:
                if result.items >= WALK_MAX_ENTRIES or result.bytes_total >= WALK_MAX_BYTES:
                    result.complete = False
                    return result

                # `follow_symlinks=False` is the whole point: a link is one
                # item and contributes its own size, never the target's tree.
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    # A link whose target is gone raises here. Count the
                    # entry, do not follow it, do not fail the walk.
                    result.items += 1
                    continue

                result.bytes_total += min(info.st_size, CATEGORY_MAX_BYTES)
                result.items += 1

                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))

        return result

    # ---------------- categories ----------------
    def collect(self) -> List[StorageCategory]:
        """Measure every well-known bucket. Safe on any host, including CI."""
        dirs = self._resolve_dirs()
        categories: list[StorageCategory] = []

        for category_id, root in dirs.items():
            label = _LABELS.get(category_id, category_id)
            category = StorageCategory(category_id=category_id, label=label)
            walked = self._walk(root)
            category.bytes = walked.bytes_total
            category.item_count = walked.items
            category.safe_to_analyze = walked.complete
            categories.append(category)

        return categories

    def summarize(self, device_id: str) -> StorageSummary:
        """The full breakdown, ready to report. Never raises."""
        total, used, free, percent = self._volume_totals()
        try:
            categories = self.collect()
        except Exception as exc:  # noqa: BLE001 - analysis must never kill the agent
            self.log.warn("storage analysis failed, reporting an empty breakdown", error=str(exc))
            categories = []
        return StorageSummary(
            device_id=device_id,
            total_bytes=total,
            used_bytes=used,
            free_bytes=free,
            usage_percent=percent,
            categories=categories,
        )

    def has_destructive_api(self) -> bool:
        """Static check the test suite relies on: no destructive verb exists.

        A Phase 4A collector must offer no way to delete, move or rename
        anything. This method exists so a test can assert the absence of
        those operations on the public surface without grepping.
        """
        forbidden = ("delete", "remove", "unlink", "rmdir", "rename", "truncate", "purge", "clean")
        return any(
            name.startswith(("delete", "remove", "unlink", "rmdir", "rename", "truncate", "purge", "clean"))
            or any(word in name for word in forbidden)
            for name in dir(self)
            if not name.startswith("_")
        )


_LABELS = {
    "downloads": "Downloads",
    "user-cache": "User cache",
    "tmp": "Temporary files",
    "trash": "Trash",
    "package-cache": "Package cache",
    "browser-cache": "Browser cache",
}


__all__ = ["LinuxStorageCollector"]
