"""
VIGILIA Windows agent — read-only storage analysis (Phase 4A).

The Windows counterpart to `agents/linux/src/storage.py`: a census of a fixed
list of safe, well-known folders — Downloads, the user's LocalAppData caches,
the temp directory, the Recycle Bin, the package cache and the browser cache.

Tooling rules this module honours, same as the rest of the Windows agent:
  - **no PowerShell, no `cmd /c`, no `tasklist`, no WMI pipe** — folders are
    resolved with the Win32 shell API (`SHGetKnownFolderPath`) through ctypes,
    with a pure-stdlib fallback when the entry point is missing;
  - **read-only** — no delete, no `unlink`, no rename, no write anywhere in
    the module, and no shell of any kind;
  - **off-Windows, nothing is faked.** `is_supported()` is the gate: on a
    Linux host the collector reports an honest empty breakdown rather than
    inventing Windows paths. Only an injected `user_dirs` test double can make
    the walk run on a non-Windows machine, which is exactly how the suite
    exercises the calculation.

Symlinks and junctions are never followed: `os.scandir` +
`entry.stat(follow_symlinks=False)` treats a reparse point as one item, so a
hostile tree cannot redirect the walk outside the named folder. Permission
errors mark the bucket `safeToAnalyze: False` and keep a partial total.
"""
from __future__ import annotations

import ctypes
import os
import shutil
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from agents.common.logger import Logger
from agents.common.storage import StorageCategory, StorageSummary

#: Same caps as the Linux collector — a pathological folder must not stall
#: the agent, and the wire field is a 64-bit integer.
WALK_MAX_ENTRIES = 200_000
WALK_MAX_BYTES = 8 * 1024 * 1024 * 1024
CATEGORY_MAX_BYTES = 2**63 - 1

_IS_WINDOWS = os.name == "nt"

# Known-folder GUIDs from shlobj.h, so no path is hardcoded per version.
_FOLDERID_PROFILE = "{5E6C858F-0E22-4760-9AFE-EA3317B67173}"
_FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"
_FOLDERID_LOCAL_APPDATA = "{F1B32785-6FBA-4FCF-9D55-2B8E4CB7E5F4}"
_FOLDERID_INTERNET_CACHE = "{352481E8-33BE-4251-BA85-6007CAED6CD4}"

_LABELS = {
    "downloads": "Downloads",
    "user-cache": "AppData cache",
    "tmp": "Temporary files",
    "trash": "Recycle Bin",
    "package-cache": "Package cache",
    "browser-cache": "Browser cache",
}


def _win32_known_folder(guid: str) -> Optional[Path]:
    """Resolve a known folder by GUID via SHGetKnownFolderPath (Vista+)."""
    try:
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None

    # CoTaskMemAlloc'd by the API; freed by the caller.
    ppath = wintypes.LPWSTR()
    try:
        ok = ole32.SHGetKnownFolderPath(ctypes.create_unicode_buffer(guid), 0, None, ctypes.byref(ppath))
    except (AttributeError, OSError, ValueError):
        return None
    if not ok or not ppath.value:
        return None
    try:
        return Path(ppath.value)
    finally:
        try:
            ctypes.windll.ole32.CoTaskMemFree(ppath)  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass


@dataclass
class _WalkResult:
    bytes_total: int = 0
    items: int = 0
    complete: bool = True


class WindowsStorageCollector:
    """Read-only storage analysis for a Windows host."""

    def __init__(self, log: Logger, *, user_dirs: Optional[dict[str, Path]] = None) -> None:
        self.log = log
        self._user_dirs = user_dirs

    def is_supported(self) -> bool:
        """True only on a real Windows host. Off-host, nothing is faked."""
        return _IS_WINDOWS

    def _resolve_dirs(self) -> dict[str, Path]:
        if self._user_dirs is not None:
            return self._user_dirs

        profile = _win32_known_folder(_FOLDERID_PROFILE)
        downloads = _win32_known_folder(_FOLDERID_DOWNLOADS)
        local_appdata = _win32_known_folder(_FOLDERID_LOCAL_APPDATA)
        ie_cache = _win32_known_folder(_FOLDERID_INTERNET_CACHE)

        fallback = Path(os.environ.get("USERPROFILE") or Path.home())
        if profile is None:
            profile = fallback
        if downloads is None:
            downloads = profile / "Downloads"
        if local_appdata is None:
            local_appdata = fallback / "AppData" / "Local"

        browsers: list[Path] = []
        for sub in ("Microsoft\\Edge\\User Data\\Default\\Cache",
                    "Google\\Chrome\\User Data\\Default\\Cache",
                    "BraveSoftware\\Brave-Browser\\User Data\\Default\\Cache"):
            browsers.append(local_appdata / sub)
        if ie_cache is None:
            browsers.append(local_appdata / "Microsoft\\Windows\\INetCache")
        else:
            browsers.append(ie_cache)

        return {
            "downloads": downloads,
            "user-cache": local_appdata / "Temp",
            "tmp": Path(os.environ.get("TEMP") or os.environ.get("TMP") or fallback / "AppData" / "Local" / "Temp"),
            # The Recycle Bin is a per-volume hidden folder; the walker counts
            # it if it exists and reports an empty bucket if it does not.
            "trash": fallback / "$Recycle.Bin",
            "package-cache": local_appdata / "Packages",
            "browser-cache": browsers[0],
        }

    # ---------------- filesystem ----------------
    def _volume_totals(self) -> Tuple[int, int, int, float]:
        """(total, used, free, usagePercent) of the system volume."""
        root = os.environ.get("SystemDrive") or "C:\\"
        if not str(root).endswith("\\"):
            root = str(root) + "\\"
        try:
            usage = shutil.disk_usage(root)
        except OSError as exc:
            self.log.debug("storage volume totals failed", error=str(exc))
            return (0, 0, 0, 0.0)
        if usage.total <= 0:
            return (0, 0, 0, 0.0)
        return (usage.total, usage.used, usage.free, round(usage.used / usage.total * 100.0, 2))

    def _walk(self, root: Path) -> _WalkResult:
        """Count one folder tree. Never follows a reparse point. Never raises."""
        result = _WalkResult()
        try:
            os.lstat(root)
        except OSError as exc:
            self.log.debug("storage root unreadable", root=str(root), error=str(exc))
            result.complete = False
            return result
        # A symlinked or junctioned root would let a tree claim to be
        # somewhere it is not; refuse it instead of following.
        if os.path.islink(str(root)):
            self.log.debug("storage root is a reparse point, skipping", root=str(root))
            result.complete = False
            return result

        stack: list[Path] = [root]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as it:
                    entries = list(it)
            except PermissionError:
                result.complete = False
                continue
            except OSError as exc:
                self.log.debug("storage dir unreadable", dir=str(directory), error=str(exc))
                result.complete = False
                continue

            for entry in entries:
                if result.items >= WALK_MAX_ENTRIES or result.bytes_total >= WALK_MAX_BYTES:
                    result.complete = False
                    return result
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    # A reparse point whose target is gone; count, do not follow.
                    result.items += 1
                    continue
                result.bytes_total += min(info.st_size, CATEGORY_MAX_BYTES)
                result.items += 1
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
        return result

    # ---------------- categories ----------------
    def collect(self) -> List[StorageCategory]:
        dirs = self._resolve_dirs()
        categories: list[StorageCategory] = []
        for category_id, root in dirs.items():
            category = StorageCategory(
                category_id=category_id, label=_LABELS.get(category_id, category_id)
            )
            walked = self._walk(root)
            category.bytes = walked.bytes_total
            category.item_count = walked.items
            category.safe_to_analyze = walked.complete
            categories.append(category)
        return categories

    def summarize(self, device_id: str) -> StorageSummary:
        if not self.is_supported():
            # Not a Windows host: report nothing rather than fabricating
            # Windows paths the filesystem does not have.
            return StorageSummary.empty(device_id)
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
        """No destructive verb exists on the public surface (see Linux twin)."""
        forbidden = ("delete", "remove", "unlink", "rmdir", "rename", "truncate", "purge", "clean")
        return any(
            word in name
            for name in dir(self)
            if not name.startswith("_")
            for word in forbidden
        )


__all__ = ["WindowsStorageCollector"]
