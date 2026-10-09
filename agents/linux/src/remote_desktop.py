"""Consented GNOME Wayland ScreenCast capture. No X11 or privilege fallback."""
from __future__ import annotations

import threading
import time
from typing import Callable

MAX_WIDTH = 1280
MAX_HEIGHT = 720
MAX_FPS = 5
MAX_JPEG_BYTES = 900 * 1024
PORTAL_TIMEOUT_SECONDS = 90
CAPABILITIES = {
    "screenshot": False, "streaming": False, "mouse": False, "keyboard": False,
    "maxWidth": MAX_WIDTH, "maxHeight": MAX_HEIGHT, "maxFps": MAX_FPS,
    "supportedFormats": ["jpeg"],
}


class PortalError(RuntimeError):
    pass


class RemoteDesktop:
    """One portal session per remote session, newest JPEG only."""
    def __init__(self, send_frame: Callable[[str, bytes], bool] | None = None, log=None) -> None:
        self._send_frame = send_frame
        self._log = log
        self._lock = threading.Lock()
        self._session_id = ""
        self._session_handle = ""
        self._pipeline = None
        self._loop = None
        self._stop = threading.Event()
        self._latest: bytes | None = None
        self._latest_lock = threading.Lock()
        self._sender: threading.Thread | None = None

    def capabilities(self) -> dict:
        # Availability means dependencies exist. Capture success is reported only
        # after portal consent and first real PipeWire JPEG in start().
        try:
            self._load_gst()
            self._load_dbus()
            return dict(CAPABILITIES)
        except Exception:
            return dict(CAPABILITIES)

    def start(self, session_id: str) -> dict:
        if not session_id:
            return self._failed(session_id, "INVALID_SESSION")
        # Do not call stop() for an empty session. stop() reports capabilities,
        # which initializes GLib unnecessarily on a portal-failure test path.
        if self._session_id:
            self.stop(self._session_id, "replaced")
        try:
            self._session_id = session_id
            fd, node_id, handle = self._open_portal_stream()
            self._session_handle = handle
            self._start_pipeline(fd, node_id)
            frame = self._wait_frame(PORTAL_TIMEOUT_SECONDS)
            if frame is None:
                raise PortalError("PIPEWIRE_FRAME_TIMEOUT")
            self._stop.clear()
            self._sender = threading.Thread(target=self._send_loop, name="vigilia-remote-media", daemon=True)
            self._sender.start()
            return {"sessionId": session_id, "status": "ACTIVE", "capabilities": {**CAPABILITIES, "screenshot": True, "streaming": True}}
        except Exception as exc:  # Portal errors must reach audit/result; never fake a frame.
            self._teardown()
            detail = str(exc)[:280] or type(exc).__name__
            if self._log:
                self._log.warn("remote desktop portal capture failed", error=detail)
            return self._failed(session_id, detail)

    def stop(self, session_id: str, reason: str) -> dict:
        if not session_id or session_id == self._session_id:
            self._teardown()
        return {"sessionId": session_id, "status": "ENDED", "reason": reason or "stopped", "capabilities": self.capabilities()}

    def input(self, _event: dict) -> None:
        return None

    def _failed(self, session_id: str, reason: str) -> dict:
        return {"sessionId": session_id, "status": "FAILED", "reason": reason, "capabilities": {**CAPABILITIES, "screenshot": False, "streaming": False}}

    @staticmethod
    def _load_dbus():
        import dbus  # type: ignore
        import dbus.mainloop.glib  # type: ignore
        from gi.repository import GLib  # type: ignore
        return dbus, GLib

    @staticmethod
    def _load_gst():
        import gi  # type: ignore
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst  # type: ignore
        Gst.init(None)
        return Gst

    def _request(self, bus, invoke, timeout: int = PORTAL_TIMEOUT_SECONDS):
        dbus, GLib = self._load_dbus()
        result: dict[str, object] = {}
        loop = GLib.MainLoop()
        def response(code, values):
            result["code"] = int(code); result["values"] = values; loop.quit()
        # Register before invoking. A portal may answer fast enough to race a
        # listener added after method return, which would look like a timeout.
        bus.add_signal_receiver(response, signal_name="Response", dbus_interface="org.freedesktop.portal.Request")
        invoke()
        GLib.timeout_add_seconds(timeout, lambda: (result.setdefault("timeout", True), loop.quit(), False)[2])
        loop.run()
        if result.get("timeout"):
            raise PortalError("PORTAL_RESPONSE_TIMEOUT")
        if result.get("code") != 0:
            values = result.get("values", {})
            raise PortalError(f"PORTAL_RESPONSE_{result.get('code')}:{dict(values)}")
        return result.get("values", {})

    def _open_portal_stream(self) -> tuple[int, int, str]:
        dbus, _ = self._load_dbus()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        portal = dbus.Interface(bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"), "org.freedesktop.portal.ScreenCast")
        token = f"vigilia{int(time.time() * 1000)}"
        values = self._request(bus, lambda: portal.CreateSession({
            "handle_token": dbus.String(token),
            "session_handle_token": dbus.String(token + "session"),
        }))
        handle = str(values["session_handle"])
        self._request(bus, lambda: portal.SelectSources(handle, {"types": dbus.UInt32(1), "multiple": dbus.Boolean(False), "cursor_mode": dbus.UInt32(1), "handle_token": dbus.String(token + "s")}))
        values = self._request(bus, lambda: portal.Start(handle, "", {"handle_token": dbus.String(token + "t")}))
        streams = values.get("streams")
        if not streams:
            raise PortalError("PORTAL_START_NO_STREAM")
        node_id = int(streams[0][0])
        fd = portal.OpenPipeWireRemote(handle, {})
        return int(fd.take()), node_id, handle

    def _start_pipeline(self, fd: int, node_id: int) -> None:
        Gst = self._load_gst()
        pipeline = Gst.parse_launch(
            f"pipewiresrc fd={fd} path={node_id} ! queue max-size-buffers=1 leaky=downstream "
            f"! videoconvert ! videoscale ! video/x-raw,width=(int)[1,{MAX_WIDTH}],height=(int)[1,{MAX_HEIGHT}] "
            f"! videorate ! video/x-raw,framerate={MAX_FPS}/1 ! jpegenc quality=75 "
            f"! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false"
        )
        sink = pipeline.get_by_name("sink")
        sink.connect("new-sample", self._on_sample)
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise PortalError("PIPEWIRE_PIPELINE_START_FAILED")
        self._pipeline = pipeline

    def _on_sample(self, sink):
        Gst = self._load_gst()
        sample = sink.emit("pull-sample")
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        try:
            data = bytes(mapped.data)
        finally:
            buffer.unmap(mapped)
        if 0 < len(data) <= MAX_JPEG_BYTES:
            with self._latest_lock:
                self._latest = data
        return Gst.FlowReturn.OK

    def _wait_frame(self, timeout: int) -> bytes | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self._latest_lock:
                if self._latest:
                    return self._latest
            time.sleep(0.05)
        return None

    def _send_loop(self) -> None:
        while not self._stop.wait(1 / MAX_FPS):
            with self._latest_lock:
                frame, self._latest = self._latest, None
            if frame and self._send_frame and not self._send_frame(self._session_id, frame):
                self._stop.set()

    def _teardown(self) -> None:
        self._stop.set()
        pipeline, self._pipeline = self._pipeline, None
        if pipeline:
            Gst = self._load_gst()
            pipeline.set_state(Gst.State.NULL)
        handle, self._session_handle = self._session_handle, ""
        if handle:
            try:
                dbus, _ = self._load_dbus(); bus = dbus.SessionBus()
                dbus.Interface(bus.get_object("org.freedesktop.portal.Desktop", handle), "org.freedesktop.portal.Session").Close()
            except Exception:
                pass
        self._session_id = ""
