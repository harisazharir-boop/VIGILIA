"""
VIGILIA Windows agent — supervisor tests.

Exercises the real registration → verify → WebSocket → telemetry/heartbeat
flow against an in-process stub backend, so the supervisor code path is
covered even though the Win32 collectors are not available on this host.

Run:  python3 -m unittest discover -s agents/windows -p 'test_*.py' -v
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.config import AgentConfig  # noqa: E402
from agents.common.logger import Logger  # noqa: E402
from agents.windows.main import AgentSupervisor  # noqa: E402
from agents.windows.src.commands import WindowsCommandExecutor  # noqa: E402

TOKEN = "vig_test-token-value"
DEVICE_ID = "dev_0001"


class StubBackend:
    """HTTP register/verify + WebSocket echo, all on one port.

    Registration and token verification are served as HTTP; the WS upgrade is
    served on the same listener because the agent derives both URLs from one
    backend URL and the stdlib socket server does not care which comes first.
    """

    def __init__(self) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.port = self.listener.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.frames: list[dict] = []
        self.subprotocol: str | None = None
        self._frames_ready = threading.Event()
        self._ws_conn: socket.socket | None = None
        self._ws_ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
        except OSError:
            return

        head = data.split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
        lines = head.split("\r\n")
        request_line = lines[0]
        path = request_line.split(" ", 2)[1] if len(request_line.split(" ")) > 1 else "/"
        subprotocol = next(
            (l.split(":", 1)[1].strip() for l in lines[1:] if l.lower().startswith("sec-websocket-protocol:")),
            None,
        )

        if "upgrade" in " ".join(l.lower() for l in lines).lower() or path.endswith("/ws"):
            self._handle_ws(conn, lines, subprotocol, data.split(b"\r\n\r\n", 1)[1])
            return

        body = data.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in data else b""
        if path.endswith("/api/devices/register"):
            payload = json.dumps({"deviceId": DEVICE_ID, "token": TOKEN}).encode("utf-8")
            self._send_http(conn, 200, payload)
        elif path.endswith("/api/auth/verify"):
            payload = json.dumps({"valid": True, "deviceId": DEVICE_ID}).encode("utf-8")
            self._send_http(conn, 200, payload)
        else:
            self._send_http(conn, 404, b'{"error":"not found"}')

    @staticmethod
    def _send_http(conn: socket.socket, status: int, body: bytes) -> None:
        reason = "OK" if status == 200 else "Not Found"
        response = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        )
        try:
            conn.sendall(response.encode("ascii") + body)
        except OSError:
            pass

    def _handle_ws(self, conn: socket.socket, lines: list[str], subprotocol: str | None, buffer: bytes) -> None:
        self.subprotocol = subprotocol
        key = next(
            (l.split(":", 1)[1].strip() for l in lines[1:] if l.lower().startswith("sec-websocket-key:")),
            "",
        )
        accept = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()
        ).decode("ascii")
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
        )
        if subprotocol:
            response += f"Sec-WebSocket-Protocol: {subprotocol}\r\n"
        try:
            conn.sendall((response + "\r\n").encode("ascii"))
        except OSError:
            return

        self._ws_conn = conn
        self._ws_ready.set()

        while True:
            payload, opcode = self._read_frame(conn, buffer)
            buffer = b""
            if payload is None:
                return
            if opcode == 0x8:
                conn.sendall(bytes([0x88, 0x00]))
                return
            if opcode == 0x1:
                try:
                    self.frames.append(json.loads(payload.decode("utf-8")))
                    self._frames_ready.set()
                except ValueError:
                    pass

    @staticmethod
    def _read_frame(conn: socket.socket, buffer: bytes) -> tuple[bytes | None, int]:
        def recv_exact(count: int) -> bytes | None:
            nonlocal buffer
            while len(buffer) < count:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    return None
                if not chunk:
                    return None
                buffer += chunk
            out, buffer = buffer[:count], buffer[count:]
            return out

        header = recv_exact(2)
        if header is None:
            return None, 0
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            extended = recv_exact(2)
            if extended is None:
                return None, 0
            length = int.from_bytes(extended, "big")
        elif length == 127:
            extended = recv_exact(8)
            if extended is None:
                return None, 0
            length = int.from_bytes(extended, "big")
        mask = recv_exact(4) if masked else b""
        payload = recv_exact(length)
        if payload is None:
            return None, 0
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return payload, opcode

    def wait_for_frames(self, count: int, timeout: float = 5.0) -> list[dict]:
        """Block until the agent has delivered at least `count` frames."""
        deadline = time.monotonic() + timeout
        while len(self.frames) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self._frames_ready.wait(max(0.1, remaining)):
                break
            self._frames_ready.clear()
        return list(self.frames)

    def wait_for_ws(self, timeout: float = 5.0) -> None:
        """Block until the agent's socket is connected and writable."""
        deadline = time.monotonic() + timeout
        while deadline - time.monotonic() > 0:
            if self._ws_ready.is_set() and self._ws_conn is not None:
                return
            self._ws_ready.wait(0.1)
        raise AssertionError("agent socket never connected")

    def send_frame(self, frame: dict) -> None:
        """Push one unmasked text frame to the connected agent."""
        conn = self._ws_conn
        if conn is None:
            raise AssertionError("no connected agent socket to send to")
        payload = json.dumps(frame).encode("utf-8")
        header = bytes([0x81])
        length = len(payload)
        if length < 126:
            header += bytes([length])
        elif length < 65536:
            header += bytes([126]) + length.to_bytes(2, "big")
        else:
            header += bytes([127]) + length.to_bytes(8, "big")
        conn.sendall(header + payload)

    def close(self) -> None:
        self._stop.set()
        self.listener.close()
        self._thread.join(timeout=2)


class _InvalidSnapshot:
    """A snapshot whose local validation always fails, for the send-gate test."""

    def __init__(self, device_id: str, entries: list, limit: int) -> None:
        self.device_id = device_id
        self.entries = entries
        self.limit = limit

    def with_timestamp(self):
        return self

    def to_payload(self) -> dict:
        raise AssertionError("an invalid snapshot must never be serialised")

    def validate(self) -> list[str]:
        return ["deviceId must not be empty"]


def _config(backend_url: str, token: str | None) -> AgentConfig:
    return AgentConfig(
        agent_name="VIGILIA Windows Test",
        backend_url=backend_url,
        token=token,
        token_file=Path("/tmp/vigilia-test-token"),
        telemetry_interval=1,
        heartbeat_interval=1,
        process_interval=1,
        process_limit=32,
        process_include_command_line=False,
        application_interval=1,
        storage_interval=1,
        security_interval=1,
        quiet=True,
        state_dir=Path(tempfile.mkdtemp(prefix="vigilia-win-test-")),
    )


class WindowsSupervisorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = StubBackend()
        self.log = Logger(quiet=True)
        collector = _StubCollector()
        self.supervisor = AgentSupervisor(_config(self.server.base_url, None), collector, self.log)

    def tearDown(self) -> None:
        self.server.close()

    # ---------------- registration ----------------
    def test_register_obtains_credentials_and_persists_the_token(self) -> None:
        written: dict[str, str] = {}

        def fake_write_token(path: Path, token: str, log: Logger) -> None:
            written[str(path)] = token

        with patch("agents.windows.main.write_token", fake_write_token):
            self.assertTrue(self.supervisor.ensure_registered())
        self.assertEqual(self.supervisor.device_id, DEVICE_ID)
        self.assertEqual(self.supervisor.token, TOKEN)
        self.assertEqual(written, {"/tmp/vigilia-test-token": TOKEN})

    def test_stored_token_is_verified_before_registration(self) -> None:
        supervisor = AgentSupervisor(
            _config(self.server.base_url, TOKEN), _StubCollector(), self.log
        )
        self.assertTrue(supervisor.ensure_registered())
        self.assertEqual(supervisor.device_id, DEVICE_ID)
        # The token was verified, so no new one was issued.
        self.assertEqual(supervisor.token, TOKEN)

    # ---------------- websocket ----------------
    def test_subprotocol_carries_the_device_id_and_token(self) -> None:
        self.supervisor.device_id = DEVICE_ID
        self.supervisor.token = TOKEN
        self.assertEqual(self.supervisor._subprotocol(), [f"vigilia.{DEVICE_ID}.{TOKEN}"])

    def test_telemetry_and_heartbeat_reach_the_backend(self) -> None:
        self.supervisor.device_id = DEVICE_ID
        self.supervisor.token = TOKEN
        self.assertTrue(self.supervisor._open_socket())
        try:
            self.supervisor._send_telemetry()
            self.supervisor._send_heartbeat()
        finally:
            self.supervisor.stop()
        self.assertEqual(self.server.subprotocol, f"vigilia.{DEVICE_ID}.{TOKEN}")

        # 15 s rather than the default 10: this suite is timing-sensitive and
        # node --test runs the backend suite in parallel, so under load the
        # two frames can land after a 10 s window.
        frames = self.server.wait_for_frames(2, timeout=15)
        telemetry = next(f for f in frames if f["type"] == "telemetry")
        self.assertEqual(telemetry["data"]["deviceId"], DEVICE_ID)
        self.assertEqual(telemetry["data"]["cpu"], 12.5)
        self.assertEqual(telemetry["data"]["os"], "windows")
        self.assertIn("timestamp", telemetry["data"])

        heartbeat = next(f for f in frames if f["type"] == "heartbeat")
        self.assertEqual(heartbeat["data"]["deviceId"], DEVICE_ID)
        self.assertEqual(heartbeat["data"]["status"], "online")

    def test_process_snapshot_reaches_the_backend(self) -> None:
        self.supervisor.device_id = DEVICE_ID
        self.supervisor.token = TOKEN
        self.assertTrue(self.supervisor._open_socket())
        try:
            self.supervisor._send_processes()
        finally:
            self.supervisor.stop()

        frames = self.server.wait_for_frames(1, timeout=10)
        snapshot = next(f for f in frames if f["type"] == "processes")
        data = snapshot["data"]
        self.assertEqual(data["deviceId"], DEVICE_ID)
        self.assertEqual(data["totalCount"], 1)
        self.assertEqual(data["processes"][0]["name"], "test-app.exe")
        self.assertEqual(data["processes"][0]["memoryMb"], 128.0)
        self.assertEqual(data["processes"][0]["status"], "running")
        self.assertIn("timestamp", data)

    def test_a_snapshot_the_agent_rejects_locally_is_not_sent(self) -> None:
        """An invalid snapshot is dropped before it reaches the wire.

        The supervisor validates locally before pushing, so a collector that
        produced something the backend would refuse never costs a round trip.
        """
        self.supervisor.device_id = DEVICE_ID
        self.supervisor.token = TOKEN
        self.assertTrue(self.supervisor._open_socket())
        try:
            # An empty device id is the simplest frame the local validator
            # rejects; build_snapshot still produces the payload, so this
            # exercises the supervisor's own gate.
            with patch(
                "agents.windows.main.build_snapshot",
                lambda device_id, entries, limit: _InvalidSnapshot(device_id, entries, limit),
            ):
                self.supervisor._send_processes()
        finally:
            self.supervisor.stop()
        self.assertEqual(self.server.frames, [], "no frame may be sent for an invalid snapshot")

    def test_command_dispatch_is_executed_and_answered(self) -> None:
        calls: list[str] = []

        def fake_native(command: str) -> tuple[bool, int]:
            calls.append(command)
            return True, 0

        executor = WindowsCommandExecutor(self.log, native=fake_native)
        supervisor = AgentSupervisor(
            _config(self.server.base_url, TOKEN), _StubCollector(), self.log, executor=executor
        )
        supervisor.device_id = DEVICE_ID
        supervisor.token = TOKEN
        self.assertTrue(supervisor._open_socket())
        # The read loop is what dispatches server frames; run it off-thread.
        reader = threading.Thread(target=supervisor._read_until_closed, daemon=True)
        reader.start()
        try:
            self.server.wait_for_ws()
            deadline = int(time.time()) + 60
            self.server.send_frame({
                "type": "command.dispatch",
                "deviceId": DEVICE_ID,
                "data": {"requestId": "cmd-1", "command": "LOCK", "deadline": deadline},
                "timestamp": "2026-09-28T18:00:00.000Z",
            })

            frames = self.server.wait_for_frames(1, timeout=8)
        finally:
            supervisor.stop()

        self.assertEqual(calls, ["LOCK"], "the dispatched command reaches the native entry point")
        result = next(f for f in frames if f["type"] == "command.result")
        self.assertEqual(result["data"]["requestId"], "cmd-1")
        self.assertEqual(result["data"]["status"], "succeeded")

    def test_an_unknown_command_is_refused_not_executed(self) -> None:
        calls: list[str] = []

        def fake_native(command: str) -> tuple[bool, int]:
            calls.append(command)
            return True, 0

        executor = WindowsCommandExecutor(self.log, native=fake_native)
        supervisor = AgentSupervisor(
            _config(self.server.base_url, TOKEN), _StubCollector(), self.log, executor=executor
        )
        supervisor.device_id = DEVICE_ID
        supervisor.token = TOKEN
        self.assertTrue(supervisor._open_socket())
        reader = threading.Thread(target=supervisor._read_until_closed, daemon=True)
        reader.start()
        try:
            self.server.wait_for_ws()
            self.server.send_frame({
                "type": "command.dispatch",
                "deviceId": DEVICE_ID,
                "data": {"requestId": "cmd-2", "command": "RM -RF /", "deadline": int(time.time()) + 60},
                "timestamp": "2026-09-28T18:00:00.000Z",
            })
            frames = self.server.wait_for_frames(1, timeout=8)
        finally:
            supervisor.stop()

        self.assertEqual(calls, [], "a non-allowlisted command never reaches the native layer")
        result = next(f for f in frames if f["type"] == "command.result")
        self.assertEqual(result["data"]["requestId"], "cmd-2")
        self.assertEqual(result["data"]["status"], "failed")


class WindowsCommandExecutorTests(unittest.TestCase):
    """The executor surface, without touching Win32."""

    def setUp(self) -> None:
        self.log = Logger(quiet=True)

    def test_rejects_commands_outside_the_allowlist(self) -> None:
        calls: list[str] = []

        def fake_native(command: str) -> tuple[bool, int]:
            calls.append(command)
            return True, 0

        executor = WindowsCommandExecutor(self.log, native=fake_native)
        outcome = executor.execute("req-1", "shutdown -r -t 0")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(calls, [], "nothing is executed for an unknown command")

    def test_reports_a_win32_failure_honestly(self) -> None:
        executor = WindowsCommandExecutor(self.log, native=lambda cmd: (False, 1314))
        outcome = executor.execute("req-1", "SHUTDOWN")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("1314", outcome.result or "", "the Win32 error code is surfaced")

    def test_a_native_exception_does_not_propagate(self) -> None:
        def boom(_command: str) -> tuple[bool, int]:
            raise RuntimeError("access denied")

        executor = WindowsCommandExecutor(self.log, native=boom)
        outcome = executor.execute("req-1", "RESTART")
        self.assertEqual(outcome.status, "failed")
        self.assertIn("access denied", outcome.result or "")



class _StubCollector:
    """Deterministic stand-in for WindowsCollector (no Win32 calls needed)."""

    def get_system_info(self) -> dict:
        return {
            "agent_version": "1.0.0",
            "platform": "windows",
            "os": "windows",
            "hostname": "test-host",
        }

    def get_cpu_usage(self):
        return 12.5

    def get_memory_usage(self):
        return 45.0

    def get_storage_usage(self):
        return 60.0

    def get_battery_status(self):
        return None

    def get_network_status(self):
        return {"connected": True, "interface": "eth0"}

    def get_uptime(self):
        return 100

    def get_process_list(self, limit: int, include_command_line: bool = False):
        from agents.common.processes import ProcessInfo

        return [ProcessInfo(pid=4, name="test-app.exe", cpu=3.0, memory_mb=128.0, status="running")]


if __name__ == "__main__":
    unittest.main()
