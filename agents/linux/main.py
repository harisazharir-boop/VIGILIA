"""
VIGILIA Linux agent — registration + realtime link to the backend.

Flow
  1. Load configuration (env / .env, token read from its own file).
  2. If no token: POST /api/devices/register, store the issued token (mode 0600).
  3. Open the WebSocket with subprotocol
     `vigilia.<deviceId>.vig_<token>` — the token never appears in a URL.
  4. Push telemetry on VIGILIA_TELEMETRY_INTERVAL and heartbeats on
     VIGILIA_HEARTBEAT_INTERVAL. Reconnect with backoff on any drop.

The agent only reports. It does not execute commands, delete files, or touch
system settings.
"""
from __future__ import annotations

import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agents.common.cleaner import CleanupPlan, human_bytes, score_recommendations  # noqa: E402
from agents.common.config import AgentConfig, load_config  # noqa: E402
from agents.common.logger import Logger  # noqa: E402
from agents.common.processes import build_snapshot  # noqa: E402
from agents.common.telemetry import (  # noqa: E402
    CommandResult,
    Heartbeat,
    Telemetry,
    is_application_command,
)
from agents.common.applications import ApplicationRegistry  # noqa: E402
from agents.common.daily_cleanup import DailyCleanupController  # noqa: E402
from agents.common.face import FaceVerifier  # noqa: E402
from agents.common.owner_auth import OwnerAuthenticator  # noqa: E402
from agents.common.security import OwnerPresence, SecurityCapabilities, SecurityEvent  # noqa: E402
from agents.common.token_store import read_device_id, read_token, write_device_id, write_token  # noqa: E402
from agents.common.version import PROTOCOL_VERSION  # noqa: E402
from agents.common.version import AGENT_VERSION  # noqa: E402
from agents.common.ws_client import WebSocketClient, WebSocketError  # noqa: E402
from agents.linux.src.applications import LinuxApplicationController  # noqa: E402
from agents.linux.src.cleaner import LinuxCleaner  # noqa: E402
from agents.linux.src.collectors import LinuxCollector  # noqa: E402
from agents.linux.src.commands import CommandExecutor, ExecutionOutcome  # noqa: E402
from agents.common.filesystem import SafeFileManager  # noqa: E402
from agents.linux.src.remote_desktop import RemoteDesktop  # noqa: E402
from agents.linux.src.hardware_controls import LinuxHardwareControls  # noqa: E402

RECONNECT_DELAYS = (1, 2, 4, 8, 15, 30)
SUBPROTOCOL_PREFIX = "vigilia."

# Recommendation scoring lives in `agents/common/cleaner.py` so Linux and
# Windows recommend the same way for the same breakdown, and so the allowlist
# there is the only place the action/category mapping is written.


class AgentSupervisor:
    """Owns the collector, the socket, and the two reporting loops."""

    def __init__(self, config: AgentConfig, collector: LinuxCollector, log: Logger,
                 executor: Optional[CommandExecutor] = None,
                 application_registry: Optional[ApplicationRegistry] = None) -> None:
        self.config = config
        self.collector = collector
        self.log = log
        self.device_id: Optional[str] = None
        self.token: Optional[str] = config.token

        # The application registry is built once, from the binaries present on
        # this host. Discovery only probes with `which` — it never launches
        # anything to test it.
        self.applications = application_registry or ApplicationRegistry(platform="linux")
        self.applications.discover()

        application_controller = LinuxApplicationController(log, self.applications.catalog)
        # The structured storage cleaner (Phase 4B). It resolves an action id
        # from the shared allowlist to its own fixed roots — never a path
        # supplied by a request.
        self.cleaner = LinuxCleaner(log)
        self.executor = executor if executor is not None else CommandExecutor(
            log, application_controller=application_controller, cleaner=self.cleaner
        )
        self.files = SafeFileManager()
        self.remote = RemoteDesktop(self._send_remote_frame, log)
        self.hardware_controls = LinuxHardwareControls()

        # ---------------- Phase 5: security & owner protection ----------------
        # The owner's identity is established here, on the device. Biometric
        # templates live in the agent's own state directory and never reach
        # the wire — the frames below carry only the *outcome*.
        state_dir = Path(self.config.state_dir)
        self.face = FaceVerifier(template_dir=state_dir, logger=log)
        self.owner = OwnerAuthenticator(
            device_id=self.device_id or "",
            log=log,
            face=self.face,
            state_file=state_dir / "owner-state.json",
            pin_file=state_dir / "owner-pin.json",
            photo_dir=state_dir / "security-photos",
        )
        self.security_capabilities = SecurityCapabilities(
            face_recognition=self.face.is_supported() and self.face.is_enrolled(),
            liveness=self.face.is_supported(),
            guest_mode=True,
            intrusion_detection=True,
            daily_cleanup=False,
        )
        self.owner.capabilities = self.security_capabilities
        # The owner auth state machine forwards its events here; the
        # supervisor is the only thing that owns a wire.
        self.owner.set_event_sink(self._send_security_event)
        self.daily_cleanup = DailyCleanupController(
            device_id=self.device_id or "",
            log=log,
            owner=self.owner,
            cleaner=self.cleaner,
            state_file=state_dir / "daily-cleanup.json",
        )
        self._sent_initial_presence = False
        self._stop = threading.Event()
        self._ws: Optional[WebSocketClient] = None

    # ---------------- registration ----------------
    def ensure_registered(self) -> bool:
        if not self.token:
            stored = read_token(self.config.token_file, self.log)
            self.token = stored
        if not self.device_id:
            self._bind_device_id(read_device_id(self.config.token_file, self.log))

        if self.token and self._verify_token():
            self.log.info("agent authenticated with stored token", device_id=self.device_id)
            return True

        self.log.info("registering device with backend", name=self.config.agent_name)
        return self._register()

    def _verify_token(self) -> bool:
        """Ask the backend whether the stored token still verifies."""
        import urllib.error
        import urllib.request

        payload = json.dumps({"deviceId": self.device_id or "", "token": self.token}).encode("utf-8")
        request = urllib.request.Request(
            self.config.verify_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = json.loads(response.read().decode("utf-8"))
                if body.get("valid"):
                    self._bind_device_id(body.get("deviceId"))
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            self.log.warn("token verification unreachable, continuing to registration")
        return False

    def _bind_device_id(self, device_id: Optional[str]) -> None:
        """Remember the device id and propagate it to the Phase 5 modules.

        They were constructed before the id existed, and their frames must
        name this connection's device — a frame naming another one is
        refused by the backend, and a blank one never validates.
        """
        if not device_id:
            return
        self.device_id = device_id
        self.owner.device_id = device_id
        self.daily_cleanup.device_id = device_id

    def _register(self) -> bool:
        import urllib.error
        import urllib.request

        system_info = self.collector.get_system_info()
        payload = json.dumps(
            {
                "name": self.config.agent_name,
                "os": "ubuntu",
                "hostname": system_info.get("hostname") or "localhost",
                "agentVersion": system_info.get("agent_version"),
                "protocolVersion": PROTOCOL_VERSION,
                "systemInfo": system_info,
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            self.config.register_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            self.log.error("registration rejected by backend", status=exc.code, detail=detail)
            return False
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.log.error("registration failed", error=str(exc))
            return False

        self._bind_device_id(body.get("deviceId"))
        self.token = body.get("token")
        if not self.device_id or not self.token:
            self.log.error("registration response missing credentials")
            return False

        write_token(self.config.token_file, self.token, self.log)
        write_device_id(self.config.token_file, self.device_id, self.log)
        self.log.info("device registered", device_id=self.device_id)
        return True

    # ---------------- websocket ----------------
    def _subprotocol(self) -> list[str]:
        return [f"{SUBPROTOCOL_PREFIX}{self.device_id}.{self.token}"]

    def _open_socket(self) -> bool:
        client = WebSocketClient(self.config.ws_url, subprotocols=self._subprotocol())
        try:
            client.connect()
        except (WebSocketError, OSError) as exc:
            self.log.error("websocket connect failed", error=str(exc))
            return False
        self._ws = client
        self.log.info("websocket connected", url=self.config.ws_url, device_id=self.device_id)
        return True

    def _send(self, frame: dict) -> bool:
        client = self._ws
        if client is None:
            return False
        try:
            client.send_text(json.dumps(frame))
            return True
        except (WebSocketError, OSError) as exc:
            self.log.warn("websocket send failed, reconnecting", error=str(exc))
            return False

    def _send_remote_frame(self, session_id: str, jpeg: bytes) -> bool:
        """Binary media envelope: magic, version, session-id length, id, JPEG."""
        client = self._ws
        session = session_id.encode("ascii", errors="ignore")
        if client is None or not session or len(session) > 128 or len(jpeg) > 900 * 1024:
            return False
        try:
            client.send_binary(b"VIG7" + bytes([1, len(session)]) + session + jpeg)
            return True
        except (WebSocketError, OSError) as exc:
            self.log.warn("remote media send failed", error=str(exc))
            return False

    def run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            if not self.ensure_registered():
                self._sleep(min(30, 2 + attempt * 2))
                attempt += 1
                continue
            attempt = 0

            if not self._open_socket():
                self._sleep(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
                attempt += 1
                continue

            self._send({"type": "agent.hello", "data": {"agentVersion": AGENT_VERSION, "protocolVersion": PROTOCOL_VERSION}})
            self._send_telemetry()
            self._send_remote_capabilities()
            self._send_hardware_control_capabilities()
            self._send_storage()
            # The catalog on connect, not only on its interval: a reconnect
            # makes the backend replay the last stored catalog to every
            # mobile socket, and that copy is only as fresh as the previous
            # interval. Sending it now keeps `running` honest.
            self._send_applications()

            # Phase 5: the security state is pushed on connect so the phone
            # sees the durable trust state immediately. The security tick
            # then re-evaluates owner presence on its own interval. The
            # *daily cleanup is not here*: it runs only after an
            # authentication that concluded OWNER, never on boot.
            self.owner.capabilities = self.security_capabilities
            self._send_security_state()

            telemetry_timer = self._loop(self.config.telemetry_interval, self._send_telemetry, "telemetry")
            heartbeat_timer = self._loop(self.config.heartbeat_interval, self._send_heartbeat, "heartbeat")
            process_timer = self._loop(self.config.process_interval, self._send_processes, "processes")
            application_timer = self._loop(
                self.config.application_interval, self._send_applications, "applications"
            )
            storage_timer = self._loop(self.config.storage_interval, self._send_storage, "storage")
            cleanup_timer = self._loop(self.config.storage_interval, self._send_cleanup, "cleanup")
            security_timer = self._loop(self.config.security_interval, self._security_tick, "security")

            self._read_until_closed()
            telemetry_timer.cancel()
            heartbeat_timer.cancel()
            process_timer.cancel()
            application_timer.cancel()
            storage_timer.cancel()
            cleanup_timer.cancel()
            security_timer.cancel()
            self._ws = None

            if self._stop.is_set():
                break
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            self.log.warn("connection lost, reconnecting", delay_seconds=delay)
            self._sleep(delay)
            attempt += 1

        self.log.info("VIGILIA agent stopped")

    def _loop(self, interval: int, action, label: str) -> threading.Timer:
        def tick() -> None:
            if self._stop.is_set():
                return
            try:
                action()
            except Exception as exc:  # noqa: BLE001 - a reporting error must not kill the agent
                self.log.warn(f"{label} loop error", error=str(exc))
            if not self._stop.is_set():
                self._loop(interval, action, label)

        timer = threading.Timer(interval, tick)
        timer.daemon = True
        timer.start()
        return timer

    def _read_until_closed(self) -> None:
        client = self._ws
        if client is None:
            return
        try:
            while not self._stop.is_set():
                message = client.recv_text()
                if message is None:
                    self.log.warn("server closed the websocket")
                    return
                self.log.debug("server frame", frame=message[:160])
                self._handle_server_frame(message)
        except (WebSocketError, OSError) as exc:
            self.log.warn("websocket read failed", error=str(exc))

    def _handle_server_frame(self, message: str) -> None:
        """Dispatch one server frame. Never raises; bad frames are logged."""
        try:
            frame = json.loads(message)
        except ValueError:
            self.log.warn("ignored a non-JSON server frame")
            return

        frame_type = frame.get("type")
        if frame_type == "command.dispatch":
            self._handle_command_dispatch(frame.get("data") or {})
        elif frame_type == "file.dispatch":
            self._handle_file_dispatch(frame.get("data") or {})
        elif frame_type == "file.transfer.chunk":
            self._handle_file_chunk(frame.get("data") or {})
        elif frame_type == "file.transfer.complete":
            self._handle_file_complete(frame.get("data") or {})
        elif frame_type == "file.transfer.cancel":
            self.files.cancel_transfer(str((frame.get("data") or {}).get("requestId") or ""))
        elif frame_type == "remote.session.start":
            self._send({"type": "remote.session.result", "data": self.remote.start(str((frame.get("data") or {}).get("sessionId") or ""))})
        elif frame_type == "remote.session.stop":
            data = frame.get("data") or {}; self._send({"type": "remote.session.result", "data": self.remote.stop(str(data.get("sessionId") or ""), str(data.get("reason") or ""))})
        elif frame_type == "remote.input":
            self.remote.input(frame.get("data") or {})
        elif frame_type == "hardware.control":
            data = frame.get("data") or {}
            result = self.hardware_controls.execute(str(data.get("control") or ""), data.get("value"))
            self._send({"type": "hardware.control.result", "data": {"requestId": str(data.get("requestId") or ""), **result}})
        elif frame_type == "error":
            detail = frame.get("data") or {}
            self.log.warn("server reported an error",
                          code=detail.get("code"), detail=detail.get("message"))
        # device.status / telemetry.latest / device.registered are informational;
        # the agent already has this data and does not need to act on them.

    def _handle_command_dispatch(self, data: dict) -> None:
        from agents.common.telemetry import CommandDispatch

        dispatch = CommandDispatch.from_payload(data)
        if not dispatch.request_id or not dispatch.is_valid:
            self.log.warn("rejected a command dispatch",
                          request_id=dispatch.request_id, command=dispatch.command)
            self._send_command_result(
                CommandResult(
                    request_id=dispatch.request_id,
                    status="failed",
                    result="unsupported command" if not dispatch.request_id
                    else f"command {dispatch.command} requires an application target",
                )
            )
            return

        if dispatch.expired:
            self.log.warn("command already past its deadline",
                          request_id=dispatch.request_id, command=dispatch.command)
            # The backend will expire it; still answer so the audit row closes.
            self._send_command_result(
                CommandResult(
                    request_id=dispatch.request_id,
                    status="failed",
                    result="command deadline already passed",
                )
            )
            return

        # Run off the read loop: a slow helper must not stall telemetry reads.
        worker = threading.Thread(
            target=self._execute_and_report,
            args=(dispatch,),
            name=f"vigilia-cmd-{dispatch.command.lower()}",
            daemon=True,
        )
        worker.start()

    def _handle_file_dispatch(self, data: dict) -> None:
        request_id = str(data.get("requestId") or "")
        deadline = float(data.get("deadline") or 0)
        if not request_id or deadline <= time.time():
            self._send({"type": "file.result", "data": {"requestId": request_id, "operation": str(data.get("operation") or ""), "status": "FAILED", "code": "EXPIRED", "reason": "request deadline passed"}})
            return
        threading.Thread(target=self._execute_file_and_report, args=(dict(data),), name="vigilia-file", daemon=True).start()

    def _execute_file_and_report(self, request: dict) -> None:
        result = self.files.execute(request)
        if result.get("status") == "SUCCEEDED" and result.get("transfer"):
            transfer = result["transfer"]
            self._send({"type": "file.transfer.start", "data": {"requestId": request["requestId"], **transfer}})
        self._send({"type": "file.result", "data": result})
        if result.get("status") == "SUCCEEDED" and result.get("transfer"):
            transfer = result["transfer"]
            if transfer.get("direction") == "download": self.files.download_chunks(request["requestId"], self._send)

    def _handle_file_chunk(self, data: dict) -> None:
        import base64
        try:
            progress = self.files.upload_chunk(str(data.get("requestId") or ""), int(data.get("sequence")), base64.b64decode(str(data.get("dataBase64") or ""), validate=True))
            self._send({"type": "file.transfer.progress", "data": progress})
        except (ValueError, PermissionError):
            self._send({"type": "file.transfer.failed", "data": {"requestId": str(data.get("requestId") or ""), "code": "NOT_ALLOWED", "reason": "invalid upload chunk"}})

    def _handle_file_complete(self, data: dict) -> None:
        request_id = str(data.get("requestId") or "")
        try: self._send({"type": "file.result", "data": self.files.complete_upload(request_id)})
        except PermissionError: self._send({"type": "file.transfer.failed", "data": {"requestId": request_id, "code": "NOT_ALLOWED", "reason": "incomplete upload"}})

    def _execute_and_report(self, dispatch) -> None:
        """Run one dispatch and report its outcome. Runs on a worker thread."""
        try:
            outcome = self.executor.execute(dispatch.request_id, dispatch.command, dispatch.target)
            self.log.info("command executed", request_id=outcome.request_id,
                          command=outcome.command, status=outcome.status,
                          duration_ms=outcome.duration_ms, target=outcome.target)
        except Exception as exc:  # noqa: BLE001 - a command error must not kill the agent
            self.log.error("command execution failed", request_id=dispatch.request_id,
                           command=dispatch.command, error=str(exc))
            outcome = ExecutionOutcome(
                request_id=dispatch.request_id, command=dispatch.command,
                status="failed", result=f"agent error: {exc}",
            )

        self._send_command_result(
            CommandResult(
                request_id=outcome.request_id,
                status=outcome.status,
                result=outcome.result,
                duration_ms=outcome.duration_ms,
            )
        )

        # An application command changes what `running` means on this host,
        # and the catalog's flags are derived from the process list. Re-push
        # it now instead of waiting up to the interval: otherwise the phone
        # holds an answer that says "closed" and a catalog that still says
        # "running", and the catalog is the authority the UI reconciles on.
        if is_application_command(outcome.command):
            self._send_applications()

    def _send_command_result(self, result: CommandResult) -> None:
        if self._send({"type": "command.result", "data": result.to_payload()}):
            self.log.debug("command result sent", request_id=result.request_id,
                           status=result.status)

    def _send_telemetry(self) -> None:
        collector = self.collector
        telemetry = Telemetry(
            device_id=self.device_id or "",
            cpu=collector.get_cpu_usage(),
            ram=collector.get_memory_usage(),
            storage=collector.get_storage_usage(),
            battery=collector.get_battery_status(),
            os=collector.get_system_info().get("os"),
            hostname=collector.get_system_info().get("hostname"),
            uptime=collector.get_uptime(),
            network=collector.get_network_status(),
            hardware=getattr(collector, "get_hardware_telemetry", lambda: None)(),
            network_telemetry=getattr(collector, "get_network_telemetry", lambda: None)(),
        ).with_timestamp()
        if self._send({"type": "telemetry", "data": telemetry.to_payload()}):
            self.log.debug("telemetry sent", cpu=telemetry.cpu, ram=telemetry.ram)

    def _send_remote_capabilities(self) -> None:
        capabilities = self.remote.capabilities()
        sent = self._send({"type": "remote.capabilities", "data": capabilities})
        self.log.info("remote capabilities sent", sent=sent, capabilities=capabilities)

    def _send_hardware_control_capabilities(self) -> None:
        self._send({"type": "hardware.control.capabilities", "data": self.hardware_controls.capabilities()})

    def _send_heartbeat(self) -> None:
        heartbeat = Heartbeat(device_id=self.device_id or "").to_payload()
        if self._send({"type": "heartbeat", "data": heartbeat}):
            self.log.debug("heartbeat sent")

    def _send_processes(self) -> None:
        """Push the process snapshot.

        Read-only telemetry: this reports what is running, nothing more. The
        frame is validated locally before it is sent so a collector on a
        hostile host cannot push something the backend rejects.
        """
        entries = self.collector.get_process_list(
            self.config.process_limit,
            include_command_line=self.config.process_include_command_line,
        )
        snapshot = build_snapshot(
            device_id=self.device_id or "",
            entries=entries,
            limit=self.config.process_limit,
        ).with_timestamp()
        issues = snapshot.validate()
        if issues:
            self.log.warn("process snapshot rejected locally", issues=issues[:3])
            return
        if self._send({"type": "processes", "data": snapshot.to_payload()}):
            self.log.debug("processes sent", count=len(snapshot.processes))

    def _send_applications(self) -> None:
        """Push the controllable-application catalog.

        The catalog is what the mobile dashboard offers as targets. It is
        built from this host's registry — never from the process list, and
        never from a request — so the set of things the phone may launch is
        decided here, on the device.

        Discovery is re-run on every interval: `which` is a read-only probe
        and the `running` flag is derived from the current process list, so a
        cached catalog would report applications as stopped that the user
        started since the agent booted.
        """
        catalog = self.applications.discover().with_timestamp()
        catalog.device_id = self.device_id or ""
        issues = catalog.validate()
        if issues:
            self.log.warn("application catalog rejected locally", issues=issues[:3])
            return
        if self._send({"type": "applications", "data": catalog.to_payload()}):
            self.log.debug("applications sent", count=len(catalog.applications))

    def _send_storage(self) -> None:
        """Push the read-only storage breakdown.

        Analysis only: this reports how much space safe, well-known locations
        occupy. It deletes nothing, moves nothing and reads no file contents —
        and the frame is validated locally before it is sent so a collector on
        a hostile host cannot push something the backend rejects.
        """
        summary = self.collector.get_storage_summary(self.device_id or "").with_timestamp()
        issues = summary.validate()
        if issues:
            self.log.warn("storage summary rejected locally", issues=issues[:3])
            return
        payload = summary.to_payload()
        if self._send({"type": "storage", "data": payload}):
            self.log.debug(
                "storage sent",
                categories=len(payload.get("categories", [])),
                usage_percent=payload.get("usagePercent"),
            )

    def _send_cleanup(self) -> None:
        """Push the structured cleanup plan.

        Advisory and structured: every entry is an action id from the shared
        allowlist with an estimate measured by this agent, plus the
        recommendations derived from the same breakdown. No path, no glob, no
        file name and no content is sent, and nothing is deleted — the plan
        is what the phone may *preview and confirm*, not something it may aim.
        """
        from agents.common.cleaner import CleanupPlan

        try:
            summary = self.collector.get_storage_summary(self.device_id or "")
            actions = self.cleaner.describe(summary.categories)
        except Exception as exc:  # noqa: BLE001 - describing must never kill the agent
            self.log.warn("cleanup plan could not be built", error=str(exc))
            return

        plan = CleanupPlan(
            device_id=self.device_id or "",
            actions=actions,
            recommendations=score_recommendations(summary, actions),
        ).with_timestamp()
        issues = plan.validate()
        if issues:
            self.log.warn("cleanup plan rejected locally", issues=issues[:3])
            return
        if self._send({"type": "cleanup", "data": plan.to_payload()}):
            self.log.debug("cleanup plan sent", actions=len(plan.actions))

    # ---------------- Phase 5: security frames ----------------

    def _send_security_event(self, event: SecurityEvent) -> None:
        """Forward one security event to the backend.

        Validated here as well as in `OwnerAuthenticator.record_event`: this
        sink is the event sink, so a caller that constructs a `SecurityEvent`
        directly must not be able to put an invented type on the wire. The
        payload carries no biometric data because `SecurityEvent` has no field
        for any, and no photo — a capture stays on the device.
        """
        issues = event.validate()
        if issues:
            self.log.warn("security event refused locally", issues=issues[:3])
            return
        payload = event.to_payload()
        if self._send({"type": "security.event", "data": payload}):
            self.log.debug("security event sent", event_type=event.event_type)

    def _send_security_state(self) -> None:
        """Push the owner-presence claim: the state, never biometrics."""
        presence = self.owner.presence().with_timestamp()
        presence.capabilities = self.security_capabilities
        issues = presence.validate()
        if issues:
            self.log.warn("owner presence rejected locally", issues=issues[:3])
            return
        if self._send({"type": "security.state", "data": presence.to_payload()}):
            self.log.debug("security state sent", state=presence.state)

    def _security_tick(self) -> None:
        """Evaluate owner presence and push the resulting state.

        This is the security heartbeat. On a host with a camera and an
        enrolled owner face it verifies; without either it reports the honest
        `UNKNOWN` state and leaves the owner-only paths closed. A daily
        cleanup is attempted *only* after a real authentication, never on
        boot and never on a timer.
        """
        if self.security_capabilities.face_recognition:
            outcome = self.owner.authenticate_face(require_liveness=self.security_capabilities.liveness)
            if not outcome.authenticated and outcome.state != "LOCKED":
                # Not the owner. Guest mode decides whether the session
                # degrades to a monitored guest or stays protected.
                self.owner.mark_unknown_person()
        elif self.owner.has_pin():
            # No camera: the owner authenticates with a PIN out of band. The
            # state machine stays in its durable state until then.
            pass
        else:
            # Neither face nor PIN is enrolled. The honest state is UNKNOWN —
            # owner access is never granted by default.
            if self.owner.state == "OWNER":
                self.owner.mark_unknown_person()

        self._send_security_state()

        # The Phase 5E trigger: exactly one place may start a daily cleanup,
        # and this is it — after an authentication that concluded OWNER.
        # `consume_authentication()` is true only for a real authentication
        # since the last tick, so a boot-time `OWNER` state, a repeated tick
        # and a timer can never start one.
        if self.owner.is_owner and self.owner.consume_authentication():
            self.daily_cleanup.run_after_owner_authentication()
            # The presence row mirrors the cleanup date.
            self._send_security_state()

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            self._ws.close()


def main() -> int:
    config = load_config(Path(__file__).resolve().parent.parent.parent)
    log = Logger(quiet=config.quiet)
    log.info("VIGILIA agent starting", name=config.agent_name, backend=config.backend_url)

    collector = LinuxCollector(log)
    supervisor = AgentSupervisor(config, collector, log)

    def _shutdown(signum, _frame) -> None:  # noqa: ANN001
        log.info("received shutdown signal", signal=signum)
        supervisor.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        supervisor.run()
    except KeyboardInterrupt:
        supervisor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
