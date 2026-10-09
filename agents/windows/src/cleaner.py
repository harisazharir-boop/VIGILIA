"""
VIGILIA Windows agent — safe storage cleanup (Phase 4B).

The Windows counterpart to `agents/linux/src/cleaner.py`: the same allowlist,
the same structured results and the same rules, applied to well-known Windows
locations.

Tooling rules this module honours, same as the rest of the Windows agent:
  - **no PowerShell, no `cmd /c`, no `tasklist`, no WMI pipe, no subprocess of
    any kind** — folders are resolved with the Win32 shell API
    (`SHGetKnownFolderPath`) through ctypes, with a pure-stdlib fallback, and
    deletion is plain `os.unlink` / `os.rmdir`;
  - **no arbitrary path** — `execute()` takes an `actionId` from the shared
    allowlist and `_roots_for()` resolves it to constants. Nothing the caller
    sends can reach the deletion call;
  - **symlinks and junctions are never followed** — a reparse-point root is
    refused, a reparse point inside the tree is unlinked as a link and its
    target is never touched;
  - **off-Windows, nothing is faked.** `is_supported()` is the gate: on a
    Linux host an action reports an honest `NOT_SUPPORTED` rather than
    pretending to clean Windows paths. Only an injected `user_dirs` test
    double makes the walk run on a non-Windows machine, which is exactly how
    the suite exercises the calculation.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from agents.common.cleaner import (
    CLEAN_ACTION_CATEGORIES,
    CLEAN_ACTION_DESCRIPTIONS,
    CLEAN_ACTION_LABELS,
    CLEAN_ACTION_PLATFORMS,
    CleanActionDescriptor,
    CleanResult,
    human_bytes,
    is_clean_action,
)
from agents.common.logger import Logger
from agents.common.storage import StorageCategory

_IS_WINDOWS = os.name == "nt"

# Known-folder GUIDs from shlobj.h (same ids the Phase 4A analyser uses).
_FOLDERID_PROFILE = "{5E6C858F-0E22-4760-9AFE-EA3317B67173}"
_FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"
_FOLDERID_LOCAL_APPDATA = "{F1B32785-6FBA-4FCF-9D55-2B8E4CB7E5F4}"
_FOLDERID_INTERNET_CACHE = "{352481E8-33BE-4251-BA85-6007CAED6CD4}"

#: Same caps as the Linux cleaner so one action cannot stall the agent.
CLEAN_MAX_ENTRIES = 200_000
CLEAN_MAX_BYTES = 8 * 1024 * 1024 * 1024


def _win32_known_folder(guid: str) -> Optional[Path]:
    """Resolve a known folder by GUID via SHGetKnownFolderPath (Vista+)."""
    try:
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None

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


def _user_dirs(profile: Path, local_appdata: Path, ie_cache: Optional[Path]) -> dict[str, list[Path]]:
    """The well-known folders a Windows cleanup action may touch.

    Every path is a constant: derived from the Win32 known-folder API or the
    process environment, never from a request.
    """
    browsers: list[Path] = []
    for sub in (
        "Microsoft\\Edge\\User Data\\Default\\Cache",
        "Google\\Chrome\\User Data\\Default\\Cache",
        "BraveSoftware\\Brave-Browser\\User Data\\Default\\Cache",
    ):
        browsers.append(local_appdata / sub)
    if ie_cache is None:
        browsers.append(local_appdata / "Microsoft\\Windows\\INetCache")
    else:
        browsers.append(ie_cache)

    temp = Path(os.environ.get("TEMP") or os.environ.get("TMP") or local_appdata / "Temp")

    return {
        "CLEAN_TEMP": [temp],
        "CLEAN_BROWSER_CACHE": browsers,
        "CLEAN_TRASH": [profile / "$Recycle.Bin"],
        "CLEAN_PACKAGE_CACHE": [local_appdata / "Packages"],
        "CLEAN_USER_CACHE": [local_appdata / "Temp", local_appdata / "Cache"],
        # The Explorer thumbnail cache. Regenerable, and a fixed location.
        "CLEAN_THUMBNAIL_CACHE": [local_appdata / "Microsoft\\Windows\\Explorer"],
    }
    # `CLEAN_OLD_LOGS` is deliberately absent on Windows: the Windows event
    # log is a binary store cleared through `wevtutil`, not a rotated file
    # tree, and there is no safe fixed root for "old logs" the way `/var/log`
    # is on Linux. The action id is in the shared allowlist so the contract
    # is one vocabulary, and `is_supported()` reports the honest platform
    # answer for it: NOT_SUPPORTED here, not a faked cleanup.


@dataclass
class _RemovalStats:
    items_removed: int = 0
    bytes_freed: int = 0
    errors: int = 0
    skipped: int = 0


class WindowsCleaner:
    """Structured, allowlisted cleanup for a Windows host."""

    def __init__(
        self,
        log: Logger,
        *,
        user_dirs: Optional[dict[str, list[Path]]] = None,
        remover: Optional[Callable[[Path], None]] = None,
    ) -> None:
        self.log = log
        # `user_dirs` is injected by tests so the suite can clean a temp tree
        # on a non-Windows host. The default path refuses to run off-host.
        self._user_dirs = user_dirs
        self._remover = remover or _remove_path

    # ---------------- surface ----------------
    def supported_actions(self) -> tuple[str, ...]:
        from agents.common.cleaner import CLEAN_ACTIONS

        return CLEAN_ACTIONS

    def is_supported(self) -> bool:
        """True only on a real Windows host. Off-host, nothing is faked."""
        return _IS_WINDOWS or self._user_dirs is not None

    def _resolve_dirs(self) -> dict[str, list[Path]]:
        if self._user_dirs is not None:
            return self._user_dirs

        profile = _win32_known_folder(_FOLDERID_PROFILE)
        local_appdata = _win32_known_folder(_FOLDERID_LOCAL_APPDATA)
        ie_cache = _win32_known_folder(_FOLDERID_INTERNET_CACHE)

        fallback = Path(os.environ.get("USERPROFILE") or Path.home())
        if profile is None:
            profile = fallback
        if local_appdata is None:
            local_appdata = fallback / "AppData" / "Local"
        return _user_dirs(profile, local_appdata, ie_cache)

    def _roots_for(self, action_id: str) -> list[Path]:
        """Fixed, approved roots for one action id; unknown id -> none."""
        if not is_clean_action(action_id):
            return []
        return list(self._resolve_dirs().get(action_id, []))

    # ---------------- descriptors ----------------
    def describe(self, storage_categories: List[StorageCategory]) -> List[CleanActionDescriptor]:
        measured = {c.category_id: c for c in (storage_categories or [])}
        descriptors: list[CleanActionDescriptor] = []

        for action_id in self.supported_actions():
            category_id = CLEAN_ACTION_CATEGORIES.get(action_id)
            category = measured.get(category_id) if category_id else None
            descriptors.append(
                CleanActionDescriptor(
                    action_id=action_id,
                    label=CLEAN_ACTION_LABELS.get(action_id, action_id),
                    description=CLEAN_ACTION_DESCRIPTIONS.get(action_id, ""),
                    estimated_bytes=category.bytes if category else 0,
                    item_count=category.item_count if category else 0,
                    platforms=tuple(CLEAN_ACTION_PLATFORMS.get(action_id, ())),
                    dry_run_supported=True,
                    estimate_reliable=bool(category and category.safe_to_analyze),
                )
            )
        return descriptors

    # ---------------- preview ----------------
    def preview(self, action_id: str) -> CleanResult:
        """Measure what an action would free. Touches nothing."""
        if not is_clean_action(action_id):
            self.log.warn("preview rejected an unknown action", action_id=action_id)
            return CleanResult(action_id=action_id or "UNKNOWN", status="FAILED", detail="unsupported action")

        roots = self._roots_for(action_id)
        stats = _RemovalStats()
        for root in roots:
            self._measure(root, stats)
        return CleanResult(
            action_id=action_id,
            status="SUCCESS",
            items_removed=stats.items_removed,
            bytes_freed=stats.bytes_freed,
            errors=0,
        )

    def _measure(self, root: Path, stats: _RemovalStats) -> None:
        """Count one folder. Read-only: `stat` only, no deletion call.

        The root directory itself is counted as neither bytes nor items:
        `execute()` keeps the well-known folder, so the preview prices
        exactly the set the execution removes — the contents only.
        """
        try:
            os.lstat(root)
        except OSError:
            return
        if os.path.islink(str(root)):
            return
        stack: list[Path] = [root]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as it:
                    entries = list(it)
            except (PermissionError, OSError):
                stats.skipped += 1
                continue
            for entry in entries:
                if stats.items_removed >= CLEAN_MAX_ENTRIES or stats.bytes_freed >= CLEAN_MAX_BYTES:
                    stats.skipped += 1
                    return
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    stats.items_removed += 1
                    continue
                stats.bytes_freed += info.st_size
                stats.items_removed += 1
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))

    # ---------------- execution ----------------
    def execute(self, action_id: str, options: Optional[dict] = None) -> CleanResult:
        """Run one cleanup action. Never raises; every failure becomes a result.

        `options` is agent-supplied (the daily cleanup controller passes the
        configured log retention); it is never request-supplied.
        """
        if not is_clean_action(action_id):
            self.log.warn("cleanup rejected an unknown action", action_id=action_id)
            return CleanResult(action_id=action_id or "UNKNOWN", status="FAILED", detail="unsupported action")

        # Honest off-host answer: a Windows cleanup cannot run on Linux, and
        # an injected test double is the only way to exercise the walk there.
        if not _IS_WINDOWS and self._user_dirs is None:
            self.log.warn("windows cleanup executed on a non-Windows host", action_id=action_id)
            return CleanResult(
                action_id=action_id,
                status="NOT_SUPPORTED",
                detail="Windows cleanup executed on a non-Windows host",
            )

        roots = self._roots_for(action_id)
        if not roots:
            # `CLEAN_OLD_LOGS` lands here on Windows: there is no safe fixed
            # root for it, so the honest answer is NOT_SUPPORTED rather than
            # a cleanup of something that is not a log.
            return CleanResult(action_id=action_id, status="NOT_SUPPORTED",
                               detail=f"{action_id} is not supported on this host")

        stats = _RemovalStats()
        for root in roots:
            self._clean_root(root, stats)

        status = "SUCCESS" if stats.errors == 0 else "PARTIAL"
        return CleanResult(
            action_id=action_id,
            status=status,
            items_removed=stats.items_removed,
            bytes_freed=stats.bytes_freed,
            errors=stats.errors,
            detail=self._summary(action_id, stats),
        )

    def _clean_root(self, root: Path, stats: _RemovalStats) -> None:
        """Delete the contents of one approved folder."""
        try:
            if not os.path.exists(root):
                return
        except OSError as exc:
            stats.errors += 1
            self.log.debug("cleanup root unreadable", root=str(root), error=str(exc))
            return

        if os.path.islink(str(root)):
            # A symlinked or junctioned root would let a folder claim to be
            # somewhere it is not; refuse it instead of following.
            stats.errors += 1
            self.log.warn("cleanup root is a reparse point, skipping", root=str(root))
            return

        stack: list[Path] = [root]
        while stack:
            directory = stack.pop()
            try:
                with os.scandir(directory) as it:
                    entries = list(it)
            except PermissionError:
                stats.errors += 1
                continue
            except OSError as exc:
                stats.errors += 1
                self.log.debug("cleanup folder unreadable", dir=str(directory), error=str(exc))
                continue

            for entry in entries:
                if stats.items_removed >= CLEAN_MAX_ENTRIES or stats.bytes_freed >= CLEAN_MAX_BYTES:
                    stats.skipped += 1
                    return
                self._remove_entry(Path(entry.path), entry, stats)

            # The well-known folder itself is kept; cleanup empties it.
            if directory != root:
                self._rmdir(directory, stats)

    def _remove_entry(self, path: Path, entry: os.DirEntry, stats: _RemovalStats) -> None:
        """Remove one entry. A reparse point is unlinked, never followed."""
        try:
            if entry.is_symlink():
                self._remover(path)
                stats.items_removed += 1
                return
            if entry.is_dir(follow_symlinks=False):
                try:
                    sub_entries = list(os.scandir(path))
                except (PermissionError, OSError):
                    stats.errors += 1
                    return
                for sub in sub_entries:
                    if stats.items_removed >= CLEAN_MAX_ENTRIES or stats.bytes_freed >= CLEAN_MAX_BYTES:
                        stats.skipped += 1
                        return
                    self._remove_entry(Path(sub.path), sub, stats)
                self._rmdir(path, stats)
                # An emptied directory is itself a removed item, so the count
                # matches what the preview priced for the same tree.
                stats.items_removed += 1
                return
            size = self._size_of(entry)
            self._remover(path)
            stats.items_removed += 1
            stats.bytes_freed += size
        except PermissionError:
            stats.errors += 1
            self.log.debug("cleanup entry not removable", path=str(path), error="permission denied")
        except OSError as exc:
            stats.errors += 1
            self.log.debug("cleanup entry not removable", path=str(path), error=str(exc))

    def _rmdir(self, directory: Path, stats: _RemovalStats) -> None:
        try:
            self._remover(directory)
        except (PermissionError, OSError):
            stats.errors += 1

    @staticmethod
    def _size_of(entry: os.DirEntry) -> int:
        try:
            return int(entry.stat(follow_symlinks=False).st_size)
        except OSError:
            return 0

    @staticmethod
    def _summary(action_id: str, stats: _RemovalStats) -> str:
        freed = human_bytes(stats.bytes_freed)
        if stats.errors == 0:
            return f"{action_id}: removed {stats.items_removed} item(s), freed {freed}"
        return (
            f"{action_id}: removed {stats.items_removed} item(s) (partial), "
            f"freed {freed}, {stats.errors} could not be removed"
        )


__all__ = ["WindowsCleaner", "CLEAN_MAX_ENTRIES", "CLEAN_MAX_BYTES"]


def _remove_path(path: Path) -> None:
    """Remove one file, symlink or empty directory (Windows default primitive).

    No shell, no PowerShell, no `cmd /c`: plain `os` filesystem calls. The
    caller walks the tree itself and accounts for every entry removed.
    """
    if path.is_symlink() or path.is_file():
        os.unlink(path)
        return
    if path.is_dir():
        os.rmdir(path)
        return
    os.unlink(path)
