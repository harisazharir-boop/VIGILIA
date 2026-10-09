"""
VIGILIA agent — application registry (Phase 3B).

The single source of truth for which applications this device will open,
close or restart on a remote request. It is an allowlist, not a hint: an
`applicationId` the registry does not know is refused before anything is
launched or terminated.

Why a registry and not a free-form executable path
--------------------------------------------------
The mobile client names an *application*, never a binary. If the protocol
carried a path, the person holding the phone would be choosing what runs on
the laptop — which is arbitrary process execution with one extra step. The
registry keeps that decision on the device:

  - the operator registers `firefox` once, with the fixed argv it maps to;
  - the mobile request only carries `firefox`;
  - the agent resolves `firefox` to its fixed argv, or refuses.

Nothing from the request reaches `exec`. There is no parsing step between
"is this id in the registry" and "run this constant argv", so a crafted frame
cannot smuggle arguments.

Identification
--------------
`applicationId` is the process `name` the Phase 3A collector reports, lower
cased. That makes one namespace for both telemetry and control: a row in the
process list is bindable to a controllable application without a second id,
and "close what I see running" needs no translation table.

Discovery
---------
`discover()` walks a list of well-known binary names and keeps the ones that
exist on this host (`shutil.which`). Nothing is launched to probe it — an
application that is installed but not running is still controllable, it just
reports `running: False`. The result is pushed to the backend as the
`applications` frame so the dashboard only ever offers applications that
actually exist here.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

#: Maximum entries the wire contract accepts (mirrors the process cap: the
#: catalog is bounded so a host cannot flood a mobile socket with apps).
APPLICATION_CATALOG_CAP = 512

#: Length caps mirrored from the TS contract.
APPLICATION_ID_MAX_LENGTH = 64
APPLICATION_LABEL_MAX_LENGTH = 120

#: Binary names probed on this host. Order does not matter; discovery keeps
#: whichever exist. Add an application here, not to the command surface.
KNOWN_LINUX_BINARIES = (
    "firefox",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "code",
    "code-insiders",
    "gnome-calculator",
    "gnome-text-editor",
    "gedit",
    "mousepad",
    "pluma",
    "nano",
    "vim",
    "gvim",
    "thunderbird",
    "telegram-desktop",
    "discord",
    "slack",
    "spotify",
    "vlc",
    "gimp",
    "inkscape",
    "evince",
    "eog",
    "nautilus",
    "thunar",
    "zenity",
    "xcalc",
)

#: Fixed argv for each application id. The launcher never builds this from a
#: request: it copies the constant for the resolved id. An id without an entry
#: is not launchable, full stop.
_LINUX_LAUNCH_ARGV: dict[str, list[str]] = {
    "firefox": ["firefox"],
    "chromium": ["chromium"],
    "chromium-browser": ["chromium-browser"],
    "google-chrome": ["google-chrome"],
    "code": ["code"],
    "code-insiders": ["code-insiders"],
    "gnome-calculator": ["gnome-calculator"],
    "gnome-text-editor": ["gnome-text-editor"],
    "gedit": ["gedit"],
    "mousepad": ["mousepad"],
    "pluma": ["pluma"],
    "nano": ["nano"],
    "vim": ["vim"],
    "gvim": ["gvim"],
    "thunderbird": ["thunderbird"],
    "telegram-desktop": ["telegram-desktop"],
    "discord": ["discord"],
    "slack": ["slack"],
    "spotify": ["spotify"],
    "vlc": ["vlc"],
    "gimp": ["gimp"],
    "inkscape": ["inkscape"],
    "evince": ["evince"],
    "eog": ["eog"],
    "nautilus": ["nautilus"],
    "thunar": ["thunar"],
    "zenity": ["zenity"],
    "xcalc": ["xcalc"],
}

#: Windows application ids -> fixed launcher. Kept separate from the Linux
#: table because the argv is genuinely different (an .exe on a per-user path);
#: the ids are the same so a mobile client does not need to care which OS it
#: is talking to. Windows resolves these through its own native launcher.
_WINDOWS_APP_IDS: tuple[str, ...] = (
    "notepad",
    "calc",
    "mspaint",
    "write",
    "explorer",
    "firefox",
    "chrome",
    "code",
)


@dataclass(frozen=True)
class ApplicationDescriptor:
    """One controllable application."""

    application_id: str
    label: str
    #: Fixed argv used to launch this application on Linux, or `None` when the
    #: application is known to this host but not launchable from here.
    launch_argv: Optional[list[str]] = None
    #: Whether the application is running right now. Discovery resolves this
    #: from the process list; an installed-but-closed application reports
    #: `False` so the dashboard can still offer to open it.
    running: bool = False

    def to_payload(self) -> dict:
        return {
            "applicationId": self.application_id,
            "label": self.label,
            "running": self.running,
        }


@dataclass
class ApplicationCatalog:
    """The registry snapshot an agent reports to the backend."""

    device_id: str
    applications: list[ApplicationDescriptor] = field(default_factory=list)
    timestamp: str = ""

    @classmethod
    def empty(cls, device_id: str) -> "ApplicationCatalog":
        return cls(device_id=device_id, timestamp=_now())

    def with_timestamp(self) -> "ApplicationCatalog":
        return ApplicationCatalog(
            device_id=self.device_id,
            applications=list(self.applications),
            timestamp=_now(),
        )

    def find(self, application_id: Optional[str]) -> Optional[ApplicationDescriptor]:
        """Case-insensitive lookup by id. `None` when unknown."""
        if not isinstance(application_id, str):
            return None
        key = application_id.strip().lower()
        for app in self.applications:
            if app.application_id == key:
                return app
        return None

    def ids(self) -> list[str]:
        return [app.application_id for app in self.applications]

    def to_payload(self) -> dict:
        return {
            "deviceId": self.device_id,
            "applications": [app.to_payload() for app in self.applications],
            "timestamp": self.timestamp or _now(),
        }

    def validate(self) -> list[str]:
        """Local pre-send validation; mirrors the backend schema."""
        issues: list[str] = []
        if len(self.applications) > APPLICATION_CATALOG_CAP:
            issues.append(f"too many applications ({len(self.applications)} > {APPLICATION_CATALOG_CAP})")
        seen: set[str] = set()
        for app in self.applications:
            if not is_valid_application_id(app.application_id):
                issues.append(f"bad applicationId: {app.application_id!r}")
                continue
            if app.application_id in seen:
                issues.append(f"duplicate applicationId: {app.application_id}")
            seen.add(app.application_id)
            if not app.label or len(app.label) > APPLICATION_LABEL_MAX_LENGTH:
                issues.append(f"bad label for {app.application_id!r}")
        return issues


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_id(name: str) -> str:
    """Lower-case and strip an id; empty stays empty (and is then rejected)."""
    return (name or "").strip().lower()


def _process_names() -> list[str]:
    """Process names on this host, or an empty list when unreadable.

    Read-only and best effort: this only powers the catalog's `running` flag,
    so a host where the process list is unavailable reports nothing rather
    than failing discovery.
    """
    try:
        import psutil  # optional dependency; the /proc fallback covers Linux
        return [p.info["name"] or "" for p in psutil.process_iter(attrs=["name"])]
    except Exception:  # noqa: BLE001 - non-fatal
        pass
    try:
        names: list[str] = []
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                # `/proc/<pid>/comm` is capped at 15 characters by the kernel
                # (TASK_COMM_LEN), so `gnome-calculator` reads as
                # `gnome-calculato` and never matches. The first argv element
                # carries the full name; fall back to `comm` when it is gone.
                with open(f"/proc/{entry}/cmdline", "rb") as handle:
                    argv0 = handle.read().split(b"\0")[0]
                name = os.path.basename(argv0.decode("utf-8", "replace")).strip()
                names.append(name or _proc_comm(entry))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
        return names
    except Exception:  # noqa: BLE001 - non-fatal
        return []


def _proc_comm(pid: str) -> str:
    """`/proc/<pid>/comm`, empty when unreadable (truncated to 15 chars)."""
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return ""


def is_valid_application_id(value: object) -> bool:
    """Same shape check the backend zod schema performs."""
    if not isinstance(value, str):
        return False
    key = _normalize_id(value)
    if not key or len(key) > APPLICATION_ID_MAX_LENGTH:
        return False
    for char in key:
        if not (char.isalnum() or char in "._-"):
            return False
    return key[0].isalnum()


class ApplicationRegistry:
    """Resolves an `applicationId` to a fixed, launchable argv."""

    def __init__(self, platform: str = "linux") -> None:
        self.platform = platform
        self._catalog = ApplicationCatalog.empty("")

    @property
    def catalog(self) -> ApplicationCatalog:
        return self._catalog

    def supported_application_ids(self) -> tuple[str, ...]:
        return tuple(app.application_id for app in self._catalog.applications)

    def register(self, application_id: str, label: str, launch_argv: Optional[list[str]] = None) -> None:
        """Add one application to the registry."""
        key = _normalize_id(application_id)
        if not is_valid_application_id(key):
            raise ValueError(f"invalid application id: {application_id!r}")
        if self._catalog.find(key) is not None:
            raise ValueError(f"duplicate application id: {key!r}")
        self._catalog.applications.append(
            ApplicationDescriptor(application_id=key, label=label.strip() or key, launch_argv=launch_argv)
        )

    def resolve(self, application_id: Optional[str]) -> Optional[ApplicationDescriptor]:
        """Find a registered application by id, or `None` when not allowed."""
        return self._catalog.find(application_id)

    def discover(self) -> ApplicationCatalog:
        """Build the registry from the binaries present on this host.

        Probes with `shutil.which` only — nothing is executed to test it. On
        Windows the same call works for names on `PATH`; the native launcher
        in `agents/windows/src/commands.py` handles the ones that are not.
        """
        if self.platform == "windows":
            self._catalog = ApplicationCatalog.empty(self._catalog.device_id)
            for app_id in _WINDOWS_APP_IDS:
                self._catalog.applications.append(
                    ApplicationDescriptor(
                        application_id=_normalize_id(app_id),
                        label=app_id,
                        launch_argv=None,
                    )
                )
            return self._catalog

        found: list[ApplicationDescriptor] = []
        running_names = self._running_process_names()
        for binary in KNOWN_LINUX_BINARIES:
            if shutil.which(binary) is None:
                continue
            argv = _LINUX_LAUNCH_ARGV.get(binary)
            if argv is None:
                continue
            label = binary
            if label == "code":
                label = "VS Code"
            elif label == "code-insiders":
                label = "VS Code Insiders"
            found.append(
                ApplicationDescriptor(
                    application_id=_normalize_id(binary),
                    label=label,
                    launch_argv=list(argv),
                    running=_normalize_id(binary) in running_names,
                )
            )
        self._catalog = ApplicationCatalog(device_id=self._catalog.device_id, applications=found)
        return self._catalog

    @staticmethod
    def _running_process_names() -> set[str]:
        """Lowercased process names on this host, best effort.

        Used only to set the `running` flag on a discovered application. A
        failure to read the process list means "unknown", reported as `False`
        — never as an error that blocks the catalog.
        """
        names: set[str] = set()
        try:
            for entry in _process_names():
                names.add(entry.lower())
        except Exception:  # noqa: BLE001 - discovery must not fail on this
            pass
        return names


__all__ = [
    "ApplicationCatalog",
    "ApplicationDescriptor",
    "ApplicationRegistry",
    "APPLICATION_CATALOG_CAP",
    "APPLICATION_ID_MAX_LENGTH",
    "APPLICATION_LABEL_MAX_LENGTH",
    "is_valid_application_id",
]
