"""Phase 7 remote desktop capability contract. No capture or input fallback is unsafe."""
from __future__ import annotations

EMPTY_CAPABILITIES = {
    "screenshot": False, "streaming": False, "mouse": False, "keyboard": False,
    "maxWidth": 0, "maxHeight": 0, "maxFps": 0, "supportedFormats": [],
}


class RemoteDesktop:
    """Explicitly unavailable until platform has a consented native implementation."""
    def capabilities(self) -> dict:
        return dict(EMPTY_CAPABILITIES)

    def start(self, session_id: str) -> dict:
        return {"sessionId": session_id, "status": "FAILED", "reason": "CAPABILITY_UNAVAILABLE", "capabilities": self.capabilities()}

    def stop(self, session_id: str, _reason: str) -> dict:
        return {"sessionId": session_id, "status": "ENDED", "reason": "stopped", "capabilities": self.capabilities()}

    def input(self, _event: dict) -> None:
        # Input injection stays unavailable until a native, user-consented backend exists.
        return None
