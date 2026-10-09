"""Native Linux hardware controls. Sysfs backlight only; no shell or privilege escalation."""
from __future__ import annotations

from pathlib import Path

CONTROLS = ("VOLUME", "BRIGHTNESS", "WIFI", "BLUETOOTH", "MICROPHONE", "CAMERA", "MEDIA_PLAYBACK")


class LinuxHardwareControls:
    def __init__(self, sysfs: Path = Path("/sys/class/backlight")) -> None:
        self.sysfs = sysfs

    def capabilities(self) -> dict:
        values = {control: False for control in CONTROLS}
        values["BRIGHTNESS"] = self._backlight() is not None
        return values

    def execute(self, control: str, value) -> dict:
        if control != "BRIGHTNESS":
            return {"control": control, "status": "UNSUPPORTED", "reason": "native control unavailable"}
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
            return {"control": control, "status": "FAILED", "reason": "invalid brightness value"}
        backlight = self._backlight()
        if backlight is None:
            return {"control": control, "status": "UNSUPPORTED", "reason": "writable backlight unavailable"}
        brightness, maximum = backlight
        target = max(0, min(maximum, round(maximum * value / 100)))
        try:
            brightness.write_text(str(target), encoding="ascii")
            return {"control": control, "status": "SUCCEEDED", "value": value}
        except OSError:
            return {"control": control, "status": "UNSUPPORTED", "reason": "backlight permission denied"}

    def _backlight(self) -> tuple[Path, int] | None:
        if not self.sysfs.is_dir(): return None
        for entry in sorted(self.sysfs.iterdir()):
            brightness, maximum = entry / "brightness", entry / "max_brightness"
            try:
                limit = int(maximum.read_text(encoding="ascii").strip())
                if limit > 0 and brightness.is_file() and brightness.stat().st_mode & 0o200:
                    return brightness, limit
            except (OSError, ValueError):
                continue
        return None
