"""
VIGILIA agent — minimal WebSocket client (RFC 6455), stdlib only.

Implemented because the agent must run on machines where pip is unavailable.
Supports exactly what VIGILIA needs: connect, subprotocol negotiation, send
text frames, receive text frames, ping/pong and clean close.
"""
from __future__ import annotations

import base64
import os
import socket
import struct
from urllib.parse import urlparse

OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


class WebSocketError(RuntimeError):
    pass


class WebSocketClient:
    def __init__(self, url: str, subprotocols: list[str] | None = None, timeout: float = 30.0) -> None:
        self.url = url
        self.subprotocols = subprotocols or []
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._recv_buffer = b""
        self.negotiated_subprotocol: str | None = None

    # ---------------- lifecycle ----------------
    def connect(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme not in ("ws", "wss"):
            raise WebSocketError(f"unsupported scheme: {parsed.scheme}")
        if parsed.scheme == "wss":
            raise WebSocketError("TLS is not supported by the stdlib client; use ws:// or a proxy")

        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        sock = socket.create_connection((host, port), timeout=self.timeout)
        self._sock = sock

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if self.subprotocols:
            request.append("Sec-WebSocket-Protocol: " + ", ".join(self.subprotocols))
        request.extend(["", ""])
        sock.sendall("\r\n".join(request).encode("ascii"))

        response = self._read_http_response()
        status = response["status"]
        if status != 101:
            raise WebSocketError(f"server returned HTTP {status}: {response['body'][:200]}")
        if "upgrade" not in response["headers"].get("connection", "").lower():
            raise WebSocketError("server did not upgrade the connection")

        negotiated = response["headers"].get("sec-websocket-protocol")
        self.negotiated_subprotocol = negotiated if negotiated in self.subprotocols else None

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self._sock is None:
            return
        try:
            self._send_frame(OPCODE_CLOSE, struct.pack(">H", code) + reason.encode("utf-8"))
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None

    # ---------------- frames ----------------
    def send_text(self, message: str) -> None:
        self._send_frame(OPCODE_TEXT, message.encode("utf-8"))

    def send_binary(self, payload: bytes) -> None:
        """Send bounded media bytes. Callers own size limits."""
        self._send_frame(OPCODE_BINARY, payload)

    def recv_text(self) -> str | None:
        """Return one text message, or None when the server closed cleanly."""
        while True:
            payload, opcode = self._recv_frame()
            if payload is None:
                return None
            if opcode == OPCODE_PING:
                self._send_frame(OPCODE_PONG, payload)
                continue
            if opcode == OPCODE_PONG:
                continue
            if opcode == OPCODE_CLOSE:
                return None
            if opcode == OPCODE_TEXT:
                return payload.decode("utf-8")
            # Unknown opcode (continuation/binary): skip rather than crash.
            continue

    # ---------------- internals ----------------
    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._sock is None:
            raise WebSocketError("socket is not connected")
        mask_key = os.urandom(4)
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)

        masked = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        self._sock.sendall(header + mask_key + masked)

    def _recv_frame(self) -> tuple[bytes | None, int]:
        header = self._recv_exact(2)
        if header is None:
            return None, OPCODE_CLOSE
        opcode = header[0] & 0x0F
        masked = bool(header[1] & 0x80)
        length = header[1] & 0x7F
        if length == 126:
            extended = self._recv_exact(2)
            if extended is None:
                return None, OPCODE_CLOSE
            length = struct.unpack(">H", extended)[0]
        elif length == 127:
            extended = self._recv_exact(8)
            if extended is None:
                return None, OPCODE_CLOSE
            length = struct.unpack(">Q", extended)[0]

        mask_key = self._recv_exact(4) if masked else None
        payload = self._recv_exact(length)
        if payload is None:
            return None, OPCODE_CLOSE
        if masked and mask_key:
            payload = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        return payload, opcode

    def _recv_exact(self, count: int) -> bytes | None:
        if self._sock is None:
            return None
        if count == 0:
            return b""
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            try:
                chunk = self._sock.recv(remaining)
            except socket.timeout:
                return None
            except OSError:
                return None
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_http_response(self) -> dict:
        if self._sock is None:
            raise WebSocketError("socket is not connected")
        raw = b""
        self._sock.settimeout(self.timeout)
        while b"\r\n\r\n" not in raw:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise WebSocketError("connection closed during handshake")
            raw += chunk

        head, _, leftover = raw.partition(b"\r\n\r\n")
        self._recv_buffer = leftover
        lines = head.decode("iso-8859-1").split("\r\n")
        status_line = lines[0].split(" ", 2)
        status = int(status_line[1]) if len(status_line) >= 2 else 0

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()
        return {"status": status, "headers": headers, "body": leftover.decode("iso-8859-1")}
