"""Narrow Phase 6 filesystem contract. No caller-supplied paths reach OS APIs."""
from __future__ import annotations

import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from secrets import token_urlsafe
from typing import Callable, Optional

FILE_OPERATIONS = {"FILE_LIST", "FILE_STAT", "FILE_SEARCH", "FILE_DOWNLOAD", "FILE_UPLOAD", "FILE_RENAME", "FILE_MOVE", "FILE_DELETE"}
FILE_ROOTS = {"HOME", "DOCUMENTS", "DOWNLOADS", "PICTURES", "VIDEOS", "DESKTOP", "PROJECTS"}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_RESULTS = 100
MAX_SEARCH_ENTRIES = 5000
HANDLE_TTL_SECONDS = 15 * 60


@dataclass
class Handle:
    path: Path
    root: str
    expires: float


class SafeFileManager:
    """Agent-side capability resolver for explicit user directories only."""
    def __init__(self, home: Optional[Path] = None, projects: Optional[Path] = None) -> None:
        self.home = (home or Path.home()).resolve()
        requested_projects = projects or (Path(os.environ["VIGILIA_FILE_ROOT"]) if os.environ.get("VIGILIA_FILE_ROOT") else self.home / "Projects")
        # Fixture override is allowed only below home. Agent state/source/system paths never become a root.
        try:
            project_root = requested_projects.resolve().relative_to(self.home)
            project_root = self.home / project_root
        except ValueError:
            project_root = self.home / "Projects"
        self.roots = {
            "HOME": self.home, "DOCUMENTS": self.home / "Documents", "DOWNLOADS": self.home / "Downloads",
            "PICTURES": self.home / "Pictures", "VIDEOS": self.home / "Videos", "DESKTOP": self.home / "Desktop",
            "PROJECTS": project_root,
        }
        self.handles: dict[str, Handle] = {}
        self.transfers: dict[str, dict] = {}

    def execute(self, request: dict) -> dict:
        operation = request.get("operation")
        request_id = str(request.get("requestId") or "")
        if operation not in FILE_OPERATIONS or not request_id:
            return self._result(request_id, str(operation or ""), "FAILED", "VALIDATION_FAILED", "invalid request")
        try:
            if operation == "FILE_LIST": return self._list(request_id, request)
            if operation == "FILE_SEARCH": return self._search(request_id, request)
            if operation == "FILE_STAT": return self._stat(request_id, request)
            if operation in {"FILE_DOWNLOAD", "FILE_UPLOAD"}: return self._transfer_start(request_id, request)
            if operation == "FILE_RENAME": return self._rename(request_id, request)
            if operation == "FILE_MOVE": return self._move(request_id, request)
            if operation == "FILE_DELETE": return self._delete(request_id, request)
        except PermissionError:
            return self._result(request_id, operation, "NOT_ALLOWED", "NOT_ALLOWED", "permission denied")
        except FileNotFoundError:
            return self._result(request_id, operation, "FAILED", "NOT_FOUND", "entry no longer exists")
        except OSError:
            return self._result(request_id, operation, "FAILED", "IO_ERROR", "filesystem operation failed")
        return self._result(request_id, operation, "FAILED", "VALIDATION_FAILED", "unsupported operation")

    def _root(self, root_id: object) -> tuple[str, Path]:
        if root_id not in FILE_ROOTS: raise PermissionError()
        root = self.roots[str(root_id)]
        if not root.exists() or self._is_link(root): raise PermissionError()
        return str(root_id), root.resolve()

    def _resolve(self, entry_id: object, expect_dir: bool = False) -> tuple[Path, str]:
        handle = self.handles.get(str(entry_id))
        if not handle or handle.expires < time.time(): raise PermissionError()
        root = self.roots.get(handle.root)
        if not root: raise PermissionError()
        self._safe_path(handle.path, root)
        if expect_dir and not handle.path.is_dir(): raise PermissionError()
        return handle.path, handle.root

    def _safe_path(self, path: Path, root: Path) -> None:
        # Every segment is lstat'd. Symlinks and Windows reparse points never become traversable.
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise PermissionError() from None
        current = root
        for part in relative.parts:
            current = current / part
            if self._is_link(current): raise PermissionError()
        if not path.exists(): raise FileNotFoundError()
        if not path.is_file() and not path.is_dir(): raise PermissionError()
        if self._protected(path): raise PermissionError()

    @staticmethod
    def _is_link(path: Path) -> bool:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            raise
        return stat.S_ISLNK(mode) or bool(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0) and getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)

    @staticmethod
    def _protected(path: Path) -> bool:
        name = path.name.lower()
        return name == ".env" or name.startswith(".env.") or name in {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".config", ".vigilia", "vigilia", "id_rsa", "id_ed25519", "credentials", "credentials.json"} or name.endswith((".pem", ".key", ".pfx", ".p12"))

    def _entry(self, path: Path, root_id: str) -> dict:
        self._safe_path(path, self.roots[root_id])
        info = path.stat(follow_symlinks=False)
        handle = "f_" + token_urlsafe(24).replace("-", "_")
        self.handles[handle] = Handle(path, root_id, time.time() + HANDLE_TTL_SECONDS)
        return {"id": handle, "name": path.name, "type": "directory" if path.is_dir() else "file", "size": info.st_size, "modifiedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(info.st_mtime)), "permissions": stat.filemode(info.st_mode)[-9:]}

    def _list(self, request_id: str, req: dict) -> dict:
        root_id, root = self._root(req.get("rootId"))
        parent = root if not req.get("entryId") else self._resolve(req["entryId"], True)[0]
        entries = []
        for child in sorted(parent.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold())):
            if len(entries) >= min(int(req.get("limit") or MAX_RESULTS), MAX_RESULTS): break
            if self._is_link(child) or self._protected(child): continue
            try: entries.append(self._entry(child, root_id))
            except (PermissionError, FileNotFoundError): continue
        return self._result(request_id, "FILE_LIST", "SUCCEEDED", entries=entries)

    def _search(self, request_id: str, req: dict) -> dict:
        root_id, root = self._root(req.get("rootId"))
        query = str(req.get("query") or "").casefold()
        if not query or any(x in query for x in ("/", "\\", "\0")): return self._result(request_id, "FILE_SEARCH", "FAILED", "VALIDATION_FAILED", "invalid query")
        entries, scanned = [], 0
        for base, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not self._is_link(Path(base) / d)]
            for name in dirs + names:
                scanned += 1
                if scanned > MAX_SEARCH_ENTRIES or len(entries) >= min(int(req.get("limit") or MAX_RESULTS), MAX_RESULTS): return self._result(request_id, "FILE_SEARCH", "SUCCEEDED", entries=entries)
                child = Path(base) / name
                if query in name.casefold() and not self._is_link(child) and not self._protected(child):
                    try: entries.append(self._entry(child, root_id))
                    except (PermissionError, FileNotFoundError): continue
        return self._result(request_id, "FILE_SEARCH", "SUCCEEDED", entries=entries)

    def _stat(self, request_id: str, req: dict) -> dict:
        path, root = self._resolve(req.get("entryId"))
        return self._result(request_id, "FILE_STAT", "SUCCEEDED", entry=self._entry(path, root))

    def _transfer_start(self, request_id: str, req: dict) -> dict:
        operation = req["operation"]
        if operation == "FILE_DOWNLOAD":
            path, _ = self._resolve(req.get("entryId"))
            if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES: return self._result(request_id, operation, "NOT_ALLOWED", "NOT_ALLOWED", "file type or size not allowed")
            self.transfers[request_id] = {"direction": "download", "path": path, "size": path.stat().st_size, "expires": time.time() + HANDLE_TTL_SECONDS}
            return self._result(request_id, operation, "SUCCEEDED", bytes=path.stat().st_size, transfer={"direction": "download", "name": path.name, "size": path.stat().st_size})
        parent, root = self._resolve(req.get("destinationId"), True)
        name = str(req.get("name") or "")
        if not self._valid_name(name) or (parent / name).exists() or self._is_link(parent): return self._result(request_id, operation, "NOT_ALLOWED", "NOT_ALLOWED", "unsafe upload destination")
        target = parent / name
        temporary = parent / ("." + name + ".vigilia-upload")
        self.transfers[request_id] = {"direction": "upload", "path": target, "temporary": temporary, "root": root, "size": int(req.get("size") or 0), "received": 0, "next": 0, "expires": time.time() + HANDLE_TTL_SECONDS}
        return self._result(request_id, operation, "SUCCEEDED", transfer={"direction": "upload", "name": name, "size": int(req.get("size") or 0)})

    def download_chunks(self, request_id: str, emit: Callable[[dict], None], chunk_size: int = 64 * 1024) -> None:
        transfer = self.transfers.get(request_id)
        if not transfer or transfer["direction"] != "download" or transfer["expires"] < time.time(): return
        import base64
        sent = 0
        with transfer["path"].open("rb") as stream:
            while chunk := stream.read(chunk_size):
                emit({"type": "file.transfer.chunk", "data": {"requestId": request_id, "sequence": sent // chunk_size, "dataBase64": base64.b64encode(chunk).decode("ascii")}})
                sent += len(chunk)
                emit({"type": "file.transfer.progress", "data": {"requestId": request_id, "transferred": sent, "total": transfer["size"]}})
        emit({"type": "file.transfer.complete", "data": {"requestId": request_id}})
        self.transfers.pop(request_id, None)

    def upload_chunk(self, request_id: str, sequence: int, data: bytes) -> dict:
        transfer = self.transfers.get(request_id)
        if not transfer or transfer["direction"] != "upload" or transfer["expires"] < time.time() or sequence != transfer["next"]: raise PermissionError()
        if len(data) > 64 * 1024 or transfer["received"] + len(data) > MAX_FILE_BYTES or (transfer["size"] and transfer["received"] + len(data) > transfer["size"]): raise PermissionError()
        mode = "ab" if transfer["received"] else "xb"
        with transfer["temporary"].open(mode) as stream: stream.write(data)
        transfer["received"] += len(data); transfer["next"] += 1
        return {"requestId": request_id, "transferred": transfer["received"], "total": transfer["size"]}

    def complete_upload(self, request_id: str) -> dict:
        transfer = self.transfers.pop(request_id, None)
        if not transfer or transfer["direction"] != "upload" or transfer["expires"] < time.time() or (transfer["size"] and transfer["size"] != transfer["received"]): raise PermissionError()
        transfer["temporary"].replace(transfer["path"])
        return self._result(request_id, "FILE_UPLOAD", "SUCCEEDED", entry=self._entry(transfer["path"], transfer["root"]), bytes=transfer["received"])

    def cancel_transfer(self, request_id: str) -> None:
        transfer = self.transfers.pop(request_id, None)
        if transfer and transfer.get("temporary"):
            try: transfer["temporary"].unlink()
            except FileNotFoundError: pass

    def _rename(self, request_id: str, req: dict) -> dict:
        path, root = self._resolve(req.get("entryId")); name = str(req.get("name") or "")
        if not self._valid_name(name): return self._result(request_id, "FILE_RENAME", "NOT_ALLOWED", "NOT_ALLOWED", "unsafe file name")
        target = path.with_name(name)
        if target.exists(): return self._result(request_id, "FILE_RENAME", "NOT_ALLOWED", "NOT_ALLOWED", "destination already exists")
        path.rename(target)
        return self._result(request_id, "FILE_RENAME", "SUCCEEDED", entry=self._entry(target, root))

    def _move(self, request_id: str, req: dict) -> dict:
        path, root = self._resolve(req.get("entryId")); parent, destination_root = self._resolve(req.get("destinationId"), True)
        if root != destination_root or (parent / path.name).exists(): return self._result(request_id, "FILE_MOVE", "NOT_ALLOWED", "NOT_ALLOWED", "cross-root move or collision denied")
        target = parent / path.name; path.rename(target)
        return self._result(request_id, "FILE_MOVE", "SUCCEEDED", entry=self._entry(target, root))

    def _delete(self, request_id: str, req: dict) -> dict:
        path, _ = self._resolve(req.get("entryId"))
        if path.is_dir():
            if any(path.iterdir()): return self._result(request_id, "FILE_DELETE", "NOT_ALLOWED", "NOT_ALLOWED", "non-empty directory deletion denied")
            path.rmdir()
        else: path.unlink()
        return self._result(request_id, "FILE_DELETE", "SUCCEEDED")

    def _valid_name(self, name: str) -> bool:
        return bool(name and len(name) <= 180 and name not in {".", ".."} and not any(x in name for x in ("/", "\\", "\0")) and not self._protected(Path(name)))

    @staticmethod
    def _result(request_id: str, operation: str, status: str, code: Optional[str] = None, reason: Optional[str] = None, **extra) -> dict:
        value = {"requestId": request_id, "operation": operation, "status": status, **extra}
        if code: value["code"] = code
        if reason: value["reason"] = reason
        return value
