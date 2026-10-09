"""
VIGILIA agent — safe token storage.

The backend-issued agent token is the machine's credential. We persist it to a
dedicated file with restrictive permissions (0400) instead of writing it back
into configuration or source.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from agents.common.logger import Logger


def read_token(path: Path, log: Logger) -> Optional[str]:
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError as exc:
        log.error("failed to read agent token", error=str(exc))
        return None


def write_token(path: Path, token: str, log: Logger) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Create with restrictive perms before writing any data.
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
        path.write_text(token, encoding="utf-8")
        os.chmod(path, 0o600)
        log.info("agent token persisted", path=str(path))
    except OSError as exc:
        log.error("failed to persist agent token", error=str(exc))


def read_device_id(path: Path, log: Logger) -> Optional[str]:
    try:
        value = path.with_suffix(path.suffix + ".device-id").read_text(encoding="ascii").strip()
        return value or None
    except OSError:
        return None


def write_device_id(path: Path, device_id: str, log: Logger) -> None:
    try:
        target = path.with_suffix(path.suffix + ".device-id")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(device_id, encoding="ascii")
        os.chmod(target, 0o600)
    except OSError as exc:
        log.error("failed to persist agent device id", error=str(exc))


__all__ = ["read_token", "write_token", "read_device_id", "write_device_id"]
