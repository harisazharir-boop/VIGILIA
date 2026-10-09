"""
VIGILIA agent — WebSocket client tests.

A real server socket is used so the RFC 6455 handshake, masking, and close
frames are exercised end to end.

Run:  python3 -m unittest discover -s agents/linux -p 'test_*.py' -v
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.ws_client import WebSocketClient, WebSocketError  # noqa: E402


class TinyWsServer:
    """Minimal server: one connection, echo text frames, respond to close."""

    def __init__(self) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.subprotocol: str | None = None
        self.received: list[str] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self.listener.accept()
        except OSError:
            return
        try:
            self._handle(conn)
        finally:
            conn.close()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        head = data.split(b"\r\n\r\n", 1)[0].decode("ascii")
        lines = head.split("\r\n")
        for line in lines[1:]:
            if line.lower().startswith("sec-websocket-protocol:"):
                self.subprotocol = line.split(":", 1)[1].strip()

        import base64
        import hashlib

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
        if self.subprotocol:
            response += f"Sec-WebSocket-Protocol: {self.subprotocol}\r\n"
        conn.sendall((response + "\r\n").encode("ascii"))

        buffer = data.split(b"\r\n\r\n", 1)[1]
        while True:
            payload, opcode, leftover = self._read_frame(conn, buffer)
            buffer = leftover
            if payload is None:
                return
            if opcode == 0x8:  # close
                conn.sendall(bytes([0x88, 0x00]))
                return
            if opcode == 0x9:  # ping -> pong
                conn.sendall(bytes([0x8A, len(payload)]) + payload)
            elif opcode == 0x1:  # text
                self.received.append(payload.decode("utf-8"))
                header = bytes([0x81])
                if len(payload) < 126:
                    header += bytes([len(payload)])
                elif len(payload) < 65536:
                    header += bytes([126]) + len(payload).to_bytes(2, "big")
                else:
                    header += bytes([127]) + len(payload).to_bytes(8, "big")
                conn.sendall(header + payload)

    @staticmethod
    def _read_frame(conn: socket.socket, buffer: bytes) -> tuple[bytes | None, int, bytes]:
        while len(buffer) < 2:
            chunk = conn.recv(4096)
            if not chunk:
                return None, 0, buffer
            buffer += chunk
        opcode = buffer[0] & 0x0F
        masked = bool(buffer[1] & 0x80)
        length = buffer[1] & 0x7F
        offset = 2
        if length == 126:
            while len(buffer) < offset + 2:
                buffer += conn.recv(4096)
            length = int.from_bytes(buffer[offset : offset + 2], "big")
            offset += 2
        elif length == 127:
            while len(buffer) < offset + 8:
                buffer += conn.recv(4096)
            length = int.from_bytes(buffer[offset : offset + 8], "big")
            offset += 8
        if masked:
            while len(buffer) < offset + 4:
                buffer += conn.recv(4096)
            mask = buffer[offset : offset + 4]
            offset += 4
        while len(buffer) < offset + length:
            buffer += conn.recv(4096)
        payload = buffer[offset : offset + length]
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return payload, opcode, buffer[offset + length :]

    def close(self) -> None:
        self.listener.close()
        self._thread.join(timeout=2)


class WebSocketClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = TinyWsServer()

    def tearDown(self) -> None:
        self.server.close()

    def test_connect_negotiates_the_subprotocol(self) -> None:
        client = WebSocketClient(f"ws://127.0.0.1:{self.server.port}/ws", subprotocols=["vigilia.dev1.vig_token"])
        client.connect()
        try:
            self.assertEqual(client.negotiated_subprotocol, "vigilia.dev1.vig_token")
            self.assertEqual(self.server.subprotocol, "vigilia.dev1.vig_token")
        finally:
            client.close()

    def test_send_and_receive_text(self) -> None:
        client = WebSocketClient(f"ws://127.0.0.1:{self.server.port}/ws")
        client.connect()
        try:
            client.send_text("hello agent")
            reply = client.recv_text()
            self.assertEqual(reply, "hello agent")
            self.assertEqual(self.server.received, ["hello agent"])
        finally:
            client.close()

    def test_large_payload_is_fragmented_correctly(self) -> None:
        client = WebSocketClient(f"ws://127.0.0.1:{self.server.port}/ws")
        client.connect()
        try:
            message = "x" * 100_000
            client.send_text(message)
            self.assertEqual(client.recv_text(), message)
        finally:
            client.close()

    def test_close_returns_none(self) -> None:
        client = WebSocketClient(f"ws://127.0.0.1:{self.server.port}/ws")
        client.connect()
        client.send_text("one")
        self.assertEqual(client.recv_text(), "one")
        client.close()
        self.assertIsNone(client.recv_text())

    def test_ping_is_answered_with_pong(self) -> None:
        client = WebSocketClient(f"ws://127.0.0.1:{self.server.port}/ws")
        client.connect()
        try:
            client._send_frame(0x9, b"pingdata")  # noqa: SLF001 — exercising the frame writer
            # The server answers a ping with a pong, which recv_text skips over.
            client.send_text("after-ping")
            self.assertEqual(client.recv_text(), "after-ping")
        finally:
            client.close()

    def test_unsupported_scheme_raises(self) -> None:
        client = WebSocketClient("https://example.com/ws")
        with self.assertRaises(WebSocketError):
            client.connect()

    def test_tls_is_rejected_explicitly(self) -> None:
        client = WebSocketClient("wss://example.com/ws")
        with self.assertRaises(WebSocketError):
            client.connect()

    def test_frame_is_json_round_trip(self) -> None:
        client = WebSocketClient(f"ws://127.0.0.1:{self.server.port}/ws")
        client.connect()
        try:
            client.send_text(json.dumps({"type": "telemetry", "data": {"cpu": 42.5}}))
            reply = json.loads(client.recv_text())
            self.assertEqual(reply["type"], "telemetry")
            self.assertEqual(reply["data"]["cpu"], 42.5)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
