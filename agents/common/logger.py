"""
VIGILIA agent — structured logging without third-party dependencies.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone


def _emit(level: str, message: str, **fields: object) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    sys.stderr.write(f"[{stamp}] {level.upper():7s} {message}{' ' + payload if payload else ''}\n")
    sys.stderr.flush()


class Logger:
    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet

    def debug(self, message: str, **fields: object) -> None:
        if not self.quiet:
            _emit("debug", message, **fields)

    def info(self, message: str, **fields: object) -> None:
        if not self.quiet:
            _emit("info", message, **fields)

    def warn(self, message: str, **fields: object) -> None:
        _emit("warn", message, **fields)

    def error(self, message: str, **fields: object) -> None:
        _emit("error", message, **fields)
