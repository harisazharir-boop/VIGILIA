"""Phase 7 bounded Linux capture contract tests. No portal consent mocked as pixels."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.linux.src.remote_desktop import (  # noqa: E402
    CAPABILITIES, MAX_FPS, MAX_HEIGHT, MAX_JPEG_BYTES, MAX_WIDTH, RemoteDesktop,
)
from agents.linux.main import AgentSupervisor  # noqa: E402


class RemoteDesktopTests(unittest.TestCase):
    def test_capability_frame_uses_text_transport(self) -> None:
        supervisor = AgentSupervisor.__new__(AgentSupervisor)
        supervisor._ws = Mock()  # noqa: SLF001
        supervisor.log = Mock()
        self.assertTrue(supervisor._send({"type": "remote.capabilities", "data": CAPABILITIES}))  # noqa: SLF001
        supervisor._ws.send_text.assert_called_once()  # noqa: SLF001

    def test_capability_limits_are_conservative(self) -> None:
        self.assertEqual((MAX_WIDTH, MAX_HEIGHT, MAX_FPS), (1280, 720, 5))
        self.assertLessEqual(MAX_JPEG_BYTES, 900 * 1024)
        self.assertFalse(CAPABILITIES["mouse"])
        self.assertFalse(CAPABILITIES["keyboard"])

    def test_start_never_claims_capture_on_portal_failure(self) -> None:
        remote = RemoteDesktop()
        with patch.object(remote, "_open_portal_stream", side_effect=RuntimeError("org.freedesktop.portal.Error.NotAllowed")):
            result = remote.start("rds_abcdefghijklmnop")
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("NotAllowed", result["reason"])
        self.assertFalse(result["capabilities"]["screenshot"])
        self.assertFalse(result["capabilities"]["streaming"])

    def test_sender_drops_stale_frame_after_send(self) -> None:
        sent: list[bytes] = []
        remote = RemoteDesktop(lambda _session, frame: sent.append(frame) or True)
        remote._session_id = "rds_abcdefghijklmnop"  # noqa: SLF001
        remote._latest = b"jpeg"  # noqa: SLF001
        remote._stop.clear()  # noqa: SLF001
        with patch.object(remote._stop, "wait", side_effect=[False, True]):  # noqa: SLF001
            remote._send_loop()  # noqa: SLF001
        self.assertEqual(sent, [b"jpeg"])
        self.assertIsNone(remote._latest)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
