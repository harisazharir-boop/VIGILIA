"""
VIGILIA agent — shared configuration loading.

Environment is read from (in order): process environment, then the .env file
that sits next to the running agent. Nothing is hardcoded, and the agent token
is never written to source — it is only persisted to its own token file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

AGENT_ENV_FILES = (".env", "agents/.env", "agents/linux/.env")
DEFAULT_TOKEN_FILE = ".agent-token"
#: Default seconds between process-list snapshots.
DEFAULT_PROCESS_INTERVAL = 15
#: Default seconds between application-catalog reports (Phase 3B). Slow: the
#: catalog only changes when software is installed or removed.
DEFAULT_APPLICATION_INTERVAL = 120
#: Default seconds between storage analyses (Phase 4A). Slow: a disk breakdown
#: moves at the pace of downloads and cache churn, not telemetry.
DEFAULT_STORAGE_INTERVAL = 120
#: Default seconds between owner-presence evaluations (Phase 5). Slow on
#: purpose: a face verification reads the camera, and the security state is a
#: claim about who is in front of the machine, not a telemetry sample.
DEFAULT_SECURITY_INTERVAL = 300
#: Default cap on entries per snapshot.
DEFAULT_PROCESS_LIMIT = 64


def _load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE parser (no dependency on python-dotenv)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got: {raw!r}")
    if value <= 0:
        raise ValueError(f"{name} must be positive, got: {value}")
    return value


@dataclass(frozen=True)
class AgentConfig:
    agent_name: str
    backend_url: str
    token: Optional[str]
    token_file: Path
    telemetry_interval: int
    heartbeat_interval: int
    process_interval: int
    process_limit: int
    process_include_command_line: bool
    application_interval: int
    storage_interval: int
    security_interval: int
    quiet: bool
    #: Where the agent keeps its own durable state: the owner-auth state, the
    #: face template, the PIN digest, the security photos and the daily
    #: cleanup state. Defaults to `<repo>/.vigilia`; `VIGILIA_STATE_DIR` lets
    #: a test or a second agent isolate it.
    state_dir: Path = Path(__file__).resolve().parent.parent.parent / ".vigilia"

    @property
    def ws_url(self) -> str:
        """Backend WebSocket endpoint derived from the base URL."""
        url = self.backend_url.rstrip("/")
        if url.startswith("https://"):
            return "wss://" + url[len("https://"):] + "/ws"
        if url.startswith("http://"):
            return "ws://" + url[len("http://"):] + "/ws"
        if url.startswith("ws://") or url.startswith("wss://"):
            return url if url.endswith("/ws") else url + "/ws"
        return "ws://" + url + "/ws"

    @property
    def register_url(self) -> str:
        return self.backend_url.rstrip("/") + "/api/devices/register"

    @property
    def verify_url(self) -> str:
        return self.backend_url.rstrip("/") + "/api/auth/verify"


def load_config(working_dir: Optional[Path] = None) -> AgentConfig:
    base = Path(working_dir) if working_dir else Path.cwd()
    for candidate in AGENT_ENV_FILES:
        _load_dotenv(base / candidate)

    agent_name = _env("VIGILIA_AGENT_NAME", "VIGILIA Laptop")
    backend_url = _env("VIGILIA_BACKEND_URL", "http://127.0.0.1:3000")
    if not backend_url:
        raise ValueError("VIGILIA_BACKEND_URL is required")

    token_file = Path(_env("VIGILIA_AGENT_TOKEN_FILE", DEFAULT_TOKEN_FILE))

    state_dir_env = _env("VIGILIA_STATE_DIR")
    state_dir = Path(state_dir_env) if state_dir_env else (
        Path(__file__).resolve().parent.parent.parent / ".vigilia"
    )

    return AgentConfig(
        agent_name=agent_name,
        backend_url=backend_url,
        token=_env("VIGILIA_AGENT_TOKEN"),
        token_file=token_file,
        telemetry_interval=_env_int("VIGILIA_TELEMETRY_INTERVAL", 5),
        heartbeat_interval=_env_int("VIGILIA_HEARTBEAT_INTERVAL", 10),
        process_interval=_env_int("VIGILIA_PROCESS_INTERVAL", DEFAULT_PROCESS_INTERVAL),
        process_limit=_env_int("VIGILIA_PROCESS_LIMIT", DEFAULT_PROCESS_LIMIT),
        process_include_command_line=_env_int("VIGILIA_PROCESS_INCLUDE_COMMAND_LINE", 0) == 1,
        application_interval=_env_int("VIGILIA_APPLICATION_INTERVAL", DEFAULT_APPLICATION_INTERVAL),
        storage_interval=_env_int("VIGILIA_STORAGE_INTERVAL", DEFAULT_STORAGE_INTERVAL),
        security_interval=_env_int("VIGILIA_SECURITY_INTERVAL", DEFAULT_SECURITY_INTERVAL),
        quiet=_env_int("VIGILIA_AGENT_QUIET", 0) == 1,
        state_dir=state_dir,
    )
