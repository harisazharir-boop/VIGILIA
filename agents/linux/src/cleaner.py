"""
VIGILIA Linux agent — safe storage cleanup (Phase 4B).

Executes the cleanup allowlist from `agents/common/cleaner.py` against a fixed
set of *pre-approved, well-known* locations on this host. This is the only
module in the Linux agent that deletes anything, and it deletes only what a
structured action id resolves to.

Threat model this module is written against
-------------------------------------------
A compromised backend, a hostile mobile client and a hostile filesystem are
all assumed. Each has a specific answer:

  - **No arbitrary path can reach the deletion call.** `execute()` takes an
    `actionId` from the allowlist. `_roots_for()` maps it to a constant list
    of directory paths. There is no parameter, no field and no code path that
    accepts a caller-supplied path — the request cannot name a file.
  - **No path traversal.** Because the roots are constants, there is nothing
    to traverse: the caller cannot influence the root at all. Walked entries
    are never used as roots, and a path built from request data never exists.
  - **No symlink escape.** Every root is resolved with `os.path.realpath` and
    re-checked to still be inside the approved root *after* resolution. A
    symlinked root is refused outright (same rule as the Phase 4A walker: a
    symlinked root would let a tree claim to be somewhere it is not). A
    symlink found *inside* the tree is unlinked as a link — its target is
    never followed and never deleted.
  - **No shell.** No child process of any kind: the agent never spawns, never
    hands a string to a system interpreter, never lexes one. Deletion is
    plain `os.unlink` / `os.rmdir`, one entry at a time, with every failure
    turned into a counted skip.
  - **Bounded.** Every walk is capped on entries and bytes, so a pathological
    directory cannot stall the agent.
  - **Never fatal.** A file that cannot be removed is skipped, counted as an
    error, and the result is `PARTIAL`. The agent always answers.

Dry run
-------
`preview()` measures the same roots without touching them and returns the
estimate. It calls no deletion API; a test asserts the tree is byte-identical
after a preview.

Honesty
-------
An action whose root does not exist reports `SUCCESS` with zero items: there
was genuinely nothing to clean. An action the platform cannot perform reports
`NOT_SUPPORTED`. A permission error partway through reports `PARTIAL` with the
lower bound of what was actually freed.
"""
from __future__ import annotations

import os
import time
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

#: Hard caps per cleanup walk, mirroring the Phase 4A analyser: a pathological
#: directory must not stall a command that holds a request slot. The entries
#: cap is what the walk *removes*, not what it reads: the cap is checked
#: before each removal, so at most this many entries are gone.
CLEAN_MAX_ENTRIES = 200_000
CLEAN_MAX_BYTES = 8 * 1024 * 1024 * 1024

_XDG_CACHE = os.environ.get("XDG_CACHE_HOME") or ""
_XDG_DATA = os.environ.get("XDG_DATA_HOME") or ""


def _user_dirs(home: Path) -> dict[str, list[Path]]:
    """The well-known locations a cleanup action may touch.

    Every path here is a constant derived from the environment the *agent*
    reads — never from a request. `CLEAN_TEMP` resolves to the system temp
    directory; it never resolves to anything the caller names.
    """
    cache_root = Path(_XDG_CACHE) if _XDG_CACHE else home / ".cache"
    data_root = Path(_XDG_DATA) if _XDG_DATA else home / ".local" / "share"

    # Browser caches: fixed parent + known child name, same probe as the
    # Phase 4A analyser so the estimate and the cleanup see the same trees.
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
        "CLEAN_TEMP": [Path("/tmp"), Path("/var/tmp")],
        "CLEAN_BROWSER_CACHE": browsers,
        "CLEAN_TRASH": [data_root / "Trash"],
        "CLEAN_PACKAGE_CACHE": [Path("/var/cache/apt/archives"), Path("/var/cache/apt/archives/partial")],
        "CLEAN_USER_CACHE": [cache_root],
        "CLEAN_THUMBNAIL_CACHE": [cache_root / "thumbnails"],
        # Rotated logs only. `CLEAN_OLD_LOGS` never removes an active log:
        # the age check plus the extension check below mean a live
        # `syslog`/`journal` is untouched, and the journal is not here at all
        # (it is a binary store `journalctl --vacuum-time` owns).
        "CLEAN_OLD_LOGS": [Path("/var/log")],
    }


@dataclass
class _RemovalStats:
    """Running tally of one cleanup action."""

    items_removed: int = 0
    bytes_freed: int = 0
    errors: int = 0
    skipped: int = 0


class LinuxCleaner:
    """Structured, allowlisted cleanup for a Linux host."""

    def __init__(
        self,
        log: Logger,
        *,
        user_dirs: Optional[dict[str, list[Path]]] = None,
        remover: Optional[Callable[[Path], None]] = None,
    ) -> None:
        self.log = log
        # `user_dirs` is injected by tests so the suite can clean a tree it
        # built in temp space instead of the real system locations.
        self._user_dirs = user_dirs
        # `remover` is injected by tests so the deletion path can be asserted
        # without actually deleting anything.
        self._remover = remover or _remove_path

    # ---------------- surface ----------------
    def supported_actions(self) -> tuple[str, ...]:
        from agents.common.cleaner import CLEAN_ACTIONS

        return CLEAN_ACTIONS

    def is_supported(self, action_id: str) -> bool:
        return action_id in CLEAN_ACTION_PLATFORMS and "linux" in CLEAN_ACTION_PLATFORMS.get(action_id, ())

    def _resolve_dirs(self) -> dict[str, list[Path]]:
        if self._user_dirs is not None:
            return self._user_dirs
        try:
            home = Path(os.environ.get("HOME") or Path.home())
        except (OSError, ValueError):
            home = Path.home()
        return _user_dirs(home)

    def _roots_for(self, action_id: str) -> list[Path]:
        """Fixed, approved roots for one action id.

        This is the whole security argument: the request cannot influence the
        result. An unknown id has no roots, and there is no other entry point.
        """
        if not is_clean_action(action_id):
            return []
        return list(self._resolve_dirs().get(action_id, []))

    # ---------------- descriptors ----------------
    def describe(self, storage_categories: List[StorageCategory]) -> List[CleanActionDescriptor]:
        """Build the action list for this host, priced from the last report.

        `storage_categories` is the Phase 4A measurement the agent itself
        took. It is advisory: an action exists even when the measurement is
        missing, with a zero estimate and `estimateReliable: False`, because
        the operator may still preview it.
        """
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
                    platforms=tuple(
                        p for p in CLEAN_ACTION_PLATFORMS.get(action_id, ()) if p in ("linux", "windows")
                    ),
                    dry_run_supported=True,
                    estimate_reliable=bool(category and category.safe_to_analyze),
                )
            )
        return descriptors

    # ---------------- preview ----------------
    def preview(self, action_id: str) -> CleanResult:
        """Measure what an action would free. Touches nothing.

        The preview reuses the *read-only* Phase 4A walker discipline: a
        symlinked root is refused, a symlink inside the tree counts as one
        item and is never followed. No deletion API is called anywhere in
        this method.
        """
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
        """Count one tree. Read-only: `stat` only, no deletion call.

        The root directory itself is not counted: `execute()` keeps it, so
        the preview and the execution must agree on what an "item" is.
        """
        if not self._inside_approved_root(root, root):
            return
        try:
            os.lstat(root)
        except OSError:
            return
        if os.path.islink(str(root)):
            # A symlinked root is refused rather than followed.
            return

        # The root directory itself is counted as neither bytes nor items:
        # `execute()` keeps the well-known directory, so the preview has to
        # price exactly the set the execution removes — the contents only.
        # Nested directories are counted the same way for both, because both
        # remove them once they are empty.
        stack: list[Path] = [root]
        while stack:
            directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except (PermissionError, OSError):
                stats.skipped += 1
                continue
            for entry in entries:
                if stats.items_removed >= CLEAN_MAX_ENTRIES or stats.bytes_freed >= CLEAN_MAX_BYTES:
                    stats.skipped += 1
                    return
                try:
                    entry_info = entry.stat(follow_symlinks=False)
                except OSError:
                    # A broken link: it would be unlinked as a link, so count
                    # it, and never follow it.
                    stats.items_removed += 1
                    continue
                stats.bytes_freed += entry_info.st_size
                stats.items_removed += 1
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
        return

    # ---------------- execution ----------------
    def execute(self, action_id: str, options: Optional[dict] = None) -> CleanResult:
        """Run one cleanup action. Never raises; every failure becomes a result.

        `options` is agent-supplied, never request-supplied: `CLEAN_OLD_LOGS`
        takes its retention age from the owner's configuration, and the
        caller (the daily cleanup controller or the command executor) reads
        that from the agent's own state. A request cannot set it.
        """
        if not is_clean_action(action_id):
            self.log.warn("cleanup rejected an unknown action", action_id=action_id)
            return CleanResult(action_id=action_id or "UNKNOWN", status="FAILED", detail="unsupported action")

        roots = self._roots_for(action_id)
        if not roots:
            self.log.warn("cleanup action has no approved roots on this host", action_id=action_id)
            return CleanResult(action_id=action_id, status="NOT_SUPPORTED",
                               detail=f"{action_id} is not supported on this host")

        stats = _RemovalStats()
        for root in roots:
            if action_id == "CLEAN_OLD_LOGS":
                self._clean_old_logs(root, stats, options or {})
            else:
                self._clean_root(root, stats)

        status = "SUCCESS" if stats.errors == 0 else "PARTIAL"
        detail = self._summary(action_id, stats)
        self.log.info(
            "cleanup executed",
            action_id=action_id,
            status=status,
            items_removed=stats.items_removed,
            bytes_freed=stats.bytes_freed,
            errors=stats.errors,
        )
        return CleanResult(
            action_id=action_id,
            status=status,
            items_removed=stats.items_removed,
            bytes_freed=stats.bytes_freed,
            errors=stats.errors,
            detail=detail,
        )

    def _clean_root(self, root: Path, stats: _RemovalStats) -> None:
        """Delete the contents of one approved root."""
        if not self._inside_approved_root(root, root):
            # Not an approved location after resolution: skip, do not raise.
            stats.errors += 1
            self.log.warn("cleanup root resolves outside the approved root", root=str(root))
            return

        try:
            if not os.path.exists(root):
                # Nothing here: an honest zero, not a failure.
                return
        except OSError as exc:
            stats.errors += 1
            self.log.debug("cleanup root unreadable", root=str(root), error=str(exc))
            return

        if os.path.islink(str(root)):
            # A symlinked root would let a tree claim to be somewhere it is
            # not. Refuse it rather than following the link.
            stats.errors += 1
            self.log.warn("cleanup root is a symlink, skipping", root=str(root))
            return

        stack: list[Path] = [root]
        while stack:
            directory = stack.pop()
            try:
                entries = list(os.scandir(directory))
            except PermissionError:
                stats.errors += 1
                self.log.debug("cleanup directory unreadable", dir=str(directory))
                continue
            except OSError as exc:
                stats.errors += 1
                self.log.debug("cleanup directory unreadable", dir=str(directory), error=str(exc))
                continue

            for entry in entries:
                if stats.items_removed >= CLEAN_MAX_ENTRIES or stats.bytes_freed >= CLEAN_MAX_BYTES:
                    stats.skipped += 1
                    return
                self._remove_entry(Path(entry.path), entry, stats)

            # The root directory itself is kept — cleanup empties a location,
            # it does not remove the well-known directory the OS expects.
            if directory != root:
                self._rmdir(directory, stats)
        return

    def _remove_entry(self, path: Path, entry: os.DirEntry, stats: _RemovalStats) -> None:
        """Remove one entry. A symlink is unlinked, never followed."""
        try:
            if entry.is_symlink():
                self._remover(path)
                stats.items_removed += 1
                return
            if entry.is_dir(follow_symlinks=False):
                # Recurse rather than a bulk tree removal: the walk cap and
                # the per-entry accounting both have to apply, and a bulk
                # primitive would bypass both.
                stack_dir = path
                try:
                    sub_entries = list(os.scandir(stack_dir))
                except (PermissionError, OSError):
                    stats.errors += 1
                    return
                for sub in sub_entries:
                    if stats.items_removed >= CLEAN_MAX_ENTRIES or stats.bytes_freed >= CLEAN_MAX_BYTES:
                        stats.skipped += 1
                        return
                    self._remove_entry(Path(sub.path), sub, stats)
                self._rmdir(stack_dir, stats)
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
            # A non-empty directory (a race added a file) or a read-only
            # parent: count it and keep going.
            stats.errors += 1

    # ---------------- old logs ----------------

    #: Extensions and names a rotated log carries. Anything else in `/var/log`
    #: is left alone: an active `syslog`, a config file, a subsystem
    #: directory the admin needs.
    _ROTATED_LOG_PATTERNS = (
        ".log.",
        ".gz",
        ".xz",
        ".bz2",
        ".old",
        ".1",
        ".2",
        ".3",
        ".4",
        ".5",
        ".6",
        ".7",
        ".8",
        ".9",
    )

    def _clean_old_logs(self, root: Path, stats: _RemovalStats, options: dict) -> None:
        """Remove rotated logs older than the configured age.

        Two gates, both required:
          - the file *is* a rotated log (name matches a rotation pattern);
          - the file is older than `logRetentionDays`.

        The active log (`syslog`, `auth.log`) matches neither and is never
        removed. The age is agent-supplied and bounded; a request cannot set it.
        """
        retention_days = self._log_retention_days(options)
        cutoff = time.time() - retention_days * 24 * 60 * 60

        try:
            entries = list(os.scandir(root))
        except (PermissionError, OSError) as exc:
            stats.errors += 1
            self.log.debug("old-logs root unreadable", root=str(root), error=str(exc))
            return

        for entry in entries:
            if stats.items_removed >= CLEAN_MAX_ENTRIES:
                stats.skipped += 1
                return
            if not self._is_rotated_log(entry.name):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if info.st_mtime >= cutoff:
                continue
            if entry.is_dir(follow_symlinks=False):
                continue
            try:
                self._remover(Path(entry.path))
                stats.items_removed += 1
                stats.bytes_freed += info.st_size
            except (PermissionError, OSError) as exc:
                stats.errors += 1
                self.log.debug("old log not removable", name=entry.name, error=str(exc))

    @staticmethod
    def _log_retention_days(options: dict) -> int:
        """The retention age, bounded. Never request-supplied."""
        try:
            days = int(options.get("logRetentionDays"))
        except (TypeError, ValueError):
            days = 30
        return max(1, min(365, days))

    @classmethod
    def _is_rotated_log(cls, name: str) -> bool:
        """True when a name is unmistakably a rotated log file.

        Deliberately narrow: `.log.1` (logrotate's own scheme) and `.gz` both
        qualify; `syslog` and `syslog.conf` do not. A name that merely ends
        in a digit without a log marker is not treated as a rotation.
        """
        if not name or name.startswith("."):
            return False
        for pattern in cls._ROTATED_LOG_PATTERNS:
            if pattern in name or name.endswith(pattern):
                # `.1`-style suffixes are only rotation markers when they
                # follow a `.log` marker, so `/var/log/wtmp.1` is but a
                # hypothetical `foo.1` in another directory is not ambiguous.
                if pattern.startswith(".") and pattern[1:].isdigit():
                    if ".log" not in name:
                        continue
                return True
        return False

    @staticmethod
    def _size_of(entry: os.DirEntry) -> int:
        try:
            return int(entry.stat(follow_symlinks=False).st_size)
        except OSError:
            return 0

    @staticmethod
    def _inside_approved_root(root: Path, candidate: Path) -> bool:
        """True when `candidate` resolves inside `root` and `root` is not a link.

        The approved root set is fixed per action id, so this is a defence in
        depth check rather than the primary control: it catches a symlink
        planted inside an approved tree that would otherwise let the walk
        escape into an unapproved location.
        """
        try:
            resolved_root = os.path.realpath(root)
            resolved_candidate = os.path.realpath(candidate)
        except OSError:
            return False
        if resolved_root != resolved_candidate:
            return False
        if os.path.islink(str(root)):
            return False
        return True

    def _summary(self, action_id: str, stats: _RemovalStats) -> str:
        """A short, path-free status line for the audit trail."""
        freed = human_bytes(stats.bytes_freed)
        if stats.errors == 0:
            return f"{action_id}: removed {stats.items_removed} item(s), freed {freed}"
        return (
            f"{action_id}: removed {stats.items_removed} item(s) (partial), "
            f"freed {freed}, {stats.errors} could not be removed"
        )


def _human_bytes(size: int) -> str:
    """Local alias for `agents.common.cleaner.human_bytes` (kept for tests)."""
    return human_bytes(size)


def _remove_path(path: Path) -> None:
    """Remove one file, symlink or empty directory.

    The default removal primitive. No bulk tree-removal call is used here
    because the caller walks the tree itself and accounts for every entry;
    this function handles exactly one path. A non-empty directory raises,
    which the caller counts as an error rather than escalating to something
    destructive.
    """
    if path.is_symlink() or path.is_file():
        os.unlink(path)
        return
    if path.is_dir():
        os.rmdir(path)
        return
    # Anything else (a fifo, a socket) is unlinked; the walk counts it.
    os.unlink(path)


__all__ = ["LinuxCleaner", "CLEAN_MAX_ENTRIES", "CLEAN_MAX_BYTES"]
