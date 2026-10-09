# VIGILIA — Agents

Install, configure and run an agent on Ubuntu or Windows. The agent reports
system telemetry only — see `docs/ARCHITECTURE.md#what-the-agent-will-never-do`.

## Requirements

- Python **3.11+** (works on stock CPython; no package is required to run).
- Network access to the backend on its HTTP port (3000 by default).

`psutil` is optional but recommended on Linux for accuracy of CPU and process
metrics:

```sh
pip install -r agents/linux/requirements.txt
```

## Configuration

Every setting is an environment variable. The agent reads the process
environment, then the first existing `.env` among `./`, `agents/.env` and
`agents/<platform>/.env` (process environment wins).

| Variable | Default | Purpose |
| --- | --- | --- |
| `VIGILIA_AGENT_NAME` | `VIGILIA Laptop` | Label shown in the dashboard |
| `VIGILIA_BACKEND_URL` | `http://127.0.0.1:3000` | Backend base URL (`/ws` is derived from it) |
| `VIGILIA_AGENT_TOKEN` | *(empty)* | Leave empty on first run; the agent registers and stores the token |
| `VIGILIA_AGENT_TOKEN_FILE` | `.agent-token` | Where the issued token is persisted (mode `0600`) |
| `VIGILIA_TELEMETRY_INTERVAL` | `5` | Seconds between telemetry samples |
| `VIGILIA_HEARTBEAT_INTERVAL` | `10` | Seconds between heartbeats (refreshes `status`/`lastSeen`) |
| `VIGILIA_PROCESS_INTERVAL` | `15` | Seconds between process-list snapshots |
| `VIGILIA_PROCESS_LIMIT` | `64` | Max entries per snapshot (hard cap 512) |
| `VIGILIA_PROCESS_INCLUDE_COMMAND_LINE` | `0` | `1` also reports each process's command line. Off by default: a command line can carry secrets, and the dashboard has no use for it |
| `VIGILIA_APPLICATION_INTERVAL` | `120` | Seconds between application-catalog reports |
| `VIGILIA_STORAGE_INTERVAL` | `120` | Seconds between storage analyses (Phase 4A) |
| `VIGILIA_SECURITY_INTERVAL` | `300` | Seconds between owner-presence evaluations (Phase 5) |
| `VIGILIA_STATE_DIR` | `.vigilia` | Where the durable agent state lives (mode `0600`) |
| `VIGILIA_AGENT_QUIET` | `0` | `1` suppresses logs except errors |

The token is never written into configuration or source. It lives only in the
token file, created mode `0600` before any data is written
(`agents/common/token_store.py`).

## Remote File Manager (Phase 6)

The paired File Manager is an explicit exception to telemetry-only reporting.
It uses native Python filesystem APIs only, never a shell, PowerShell, cmd,
`rm`, or a generic execution API. Client input is an opaque agent-issued
handle, not a path.

Safe roots are the agent user's Home, Documents, Downloads, Pictures, Videos,
Desktop and Projects. Projects must resolve below Home. Every target is checked
again immediately before action; symlinks and Windows reparse points are
rejected. `.env`, key/certificate, credential, cloud-credential, VIGILIA
state/config and system-sensitive material are blocked. Delete is one file or
an empty directory only; recursive delete is unavailable.

Transfers are chunked at 64 KiB with a 10 MiB cap, request/device-bound expiry
and cancellation cleanup. Linux and Windows share the protocol; native Windows
junction E2E was not run on the Linux verification host.

## Consented Remote Desktop (Phase 7)

Linux GNOME Wayland supports view-only remote desktop through its normal
`xdg-desktop-portal` ScreenCast consent dialog. Starting a paired remote
session opens the system picker; nothing is captured until local user consent
and first real PipeWire frame succeed. Denial, timeout or portal errors fail
the session with its actual error. No X11, root, fake-frame or input-injection
fallback exists.

The agent receives the portal PipeWire FD and encodes real frames as JPEG.
Capture is capped at 1280x720, 5 FPS and 900 KiB per frame. Media travels as
binary WebSocket frames over authenticated agent transport; base64 JSON frame
streams are not used. The relay sends media only to read-only subscribers for
the matching device and active remote session. Reconnecting a subscriber gets
new live frames; historical frames are not stored or replayed.

Mouse and keyboard are intentionally unavailable. They remain fail-closed
until a separately consented EIS/libei implementation exists.

## Linux

```sh
# from the repo root, against a backend already on :3000
npm run agent:linux

# or directly
python3 agents/linux/main.py
```

Collection: `psutil` when importable, otherwise a pure-stdlib `/proc` +
`/sys` fallback. Metrics collected: CPU usage, memory, storage, battery,
uptime, network link state, OS/hostname.

Run the suite (42 tests, no backend needed):

```sh
npm run agent:linux:test
```

## Windows

```sh
python agents\windows\main.py
```

Collection: `ctypes` into `kernel32`/`psapi` — `GlobalMemoryStatusEx`,
`GetSystemTimes`, `GetDiskFreeSpaceExW`, `GetSystemPowerStatus`,
`GetTickCount64` — with a stdlib/psutil fallback when the Win32 entry points
are unavailable.

The suite runs on Linux too: 19 tests pass with 3 host-native skips where the
Win32 APIs are absent.

```sh
npm run agent:windows:test
```

## What happens on first run

1. No token file → the agent POSTs its name, OS and hostname to
   `/api/devices/register` and stores the returned `vig_` token.
2. On later starts it verifies the stored token against
   `/api/auth/verify` before opening the socket; an unreachable backend is
   treated as "continue to registration".
3. It opens `/ws` with subprotocol `vigilia.<deviceId>.vig_<token>`, sends
   one telemetry sample immediately, then loops on the two intervals.
4. On any drop it reconnects with backoff `1, 2, 4, 8, 15, 30` seconds and
   re-registers if the token no longer verifies.

## Running as a service (Ubuntu)

Phase 11A ships repeatable profiles under `deploy/`. Build first, then run
`./deploy/install-linux.sh`. Configuration is explicit and idempotent:

```sh
VIGILIA_INSTALL_ROOT=/opt/vigilia \
VIGILIA_BACKEND_USER=vigilia \
./deploy/install-linux.sh
```

The installer creates a non-root backend system service and an agent *user*
service. The agent must remain a user service for GNOME Wayland/portal consent.
It creates environment files only when absent, so secrets and operator changes
are not overwritten. Start/restart checks use `systemctl` and `/api/health`;
logs are journal-bounded. Roll back unit installation with
`./deploy/uninstall-linux.sh`; it intentionally retains environment, state and
token files. Windows uses `deploy/windows/VIGILIA-Agent.xml`; validate/install
that Task Scheduler profile on a real Windows host.

```sh
cat > /etc/systemd/system/vigilia-agent.service <<'EOF'
[Unit]
Description=VIGILIA agent
After=network-online.target

[Service]
EnvironmentFile=/etc/vigilia/agent.env
WorkingDirectory=/opt/vigilia
ExecStart=/usr/bin/python3 /opt/vigilia/agents/linux/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl enable --now vigilia-agent
```

`/etc/vigilia/agent.env` holds the variables from the table above; the token
file (`.agent-token`) lands in `WorkingDirectory`. Confirm the token file is
`0600` after first start.

## Multiple machines

Each machine runs its own agent with its own `VIGILIA_AGENT_NAME`. A
registration with a name+hostname pair that already exists re-issues a token
for that device rather than creating a duplicate.

## Commands

An agent accepts remote commands over the same `/ws` socket. The allowlist is
fixed and identical on both platforms — there is no shell anywhere in the
chain:

| Command | Linux | Windows |
| --- | --- | --- |
| `LOCK` | `loginctl lock-session` per active seat session (fallback `xdg-screensaver lock`) | `user32.LockWorkStation()` |
| `SLEEP` | `systemctl suspend` (fallback `shutdown`) | `powrprof.SetSuspendState(FALSE, …)` |
| `HIBERNATE` | `systemctl hibernate` | `powrprof.SetSuspendState(TRUE, …)` |
| `RESTART` | `systemctl reboot` | `advapi32.InitiateSystemShutdownExW(…, reboot)` |
| `SHUTDOWN` | `systemctl poweroff` | `advapi32.InitiateSystemShutdownExW(…, no reboot)` |

Command argv is fixed per command — nothing from the request reaches `exec`.
Each command runs with a 15 s timeout and the outcome is always reported,
including the platform's own error text, so a missing capability shows up as
`failed` with a reason rather than a silent no-op.

Power actions (`SLEEP`…`SHUTDOWN`) need elevated privileges. On Ubuntu run the
agent as a service with the right polkit rules or as root; on Windows run it
as a service or an elevated process so `SE_SHUTDOWN_NAME` is held.

`LOCK` requires an active graphical login session. A headless host reports
`failed: no active login session to lock` rather than pretending.

To enable remote commands for a device, pair it once with the agent token:

```sh
curl -X POST http://127.0.0.1:3000/api/devices/<deviceId>/pair \
  -H "Authorization: Bearer <agent token>" \
  -H "x-vigilia-device-id: <deviceId>" \
  -H "Content-Type: application/json" \
  -d '{"deviceId":"<deviceId>"}'
```

Keep the returned `vc_` command token with the mobile client only — it is a
separate credential from the agent token and is bound to this device.

## Process telemetry (Phase 3A)

The agent also reports the process list. It is read-only telemetry with the
same properties as the other collectors — no shell, no process is touched.

| Platform | Source |
| --- | --- |
| Linux | `psutil` when importable, otherwise a pure-stdlib walk over `/proc/<pid>/{stat,status,cmdline,exe}` |
| Windows | `psutil` when importable, otherwise native `EnumProcesses` + `GetProcessMemoryInfo` + `GetProcessTimes` + `QueryFullProcessImageNameW` through ctypes |

Notes:

- A process the agent cannot read (another user's, or a protected one) is
  simply reported with the fields that *were* readable. `executablePath` is
  the most common omission.
- CPU% is instantaneous against the previous snapshot of the same pid. The
  first time a pid is seen it reports `0.0` — an honest "not measured yet"
  rather than a fabricated number.
- A process that dies mid-enumeration is skipped; it never fails the sample.
- Off-Windows the native path returns an empty list because there is no
  process list to read. It does not fake one.

To enable the command line per process (it is off by default because a
command line can carry secrets):

```sh
VIGILIA_PROCESS_INCLUDE_COMMAND_LINE=1 python3 agents/linux/main.py
```

## Application catalog (Phase 3B)

The agent also reports which applications the phone may open, close or
restart. Discovery probes with `shutil.which` — nothing is executed to test
it — and sets each entry's `running` flag from the current process list, so
the catalog reflects what is actually open.

The catalog is **re-discovered on every push**, not cached from boot: the
`running` flags would otherwise freeze at the state the agent saw when it
started, and a user opening Firefox afterwards would still be shown as
stopped. It is sent on socket open as well as on the interval, because a
reconnect makes the backend replay the last *stored* catalog to every phone
watching, and that copy is only as fresh as the previous interval. An
`APPLICATION_*` command is followed by an immediate re-push, so the phone's
answer and its catalog agree.

On Linux the process-name source is `psutil` when importable, otherwise
`/proc/<pid>/cmdline` — **not** `/proc/<pid>/comm`, which the kernel caps at
15 characters and would report `gnome-calculator` as `gnome-calculato`.

## Storage analysis (Phase 4A)

The agent also reports a read-only disk breakdown: how much space Downloads,
the user cache, the temp directory, the trash, the package cache and the
browser cache occupy, measured against the filesystem totals.

It is a census of a fixed, named list of locations — not a filesystem scan —
and it is analysis only. There is no delete, no cleanup and no verb anywhere
in this phase that could modify a file.

| Platform | Source |
| --- | --- |
| Linux | stdlib `os.scandir` + `stat(follow_symlinks=False)` over `~/Downloads`, `~/.cache`, `/tmp`, `~/.local/share/Trash`, `/var/cache/apt/archives` and the browser cache profiles |
| Windows | the same walk over the known folders resolved with `SHGetKnownFolderPath` (Win32 shell API via ctypes), with a stdlib fallback. No PowerShell, no `cmd /c`, no WMI pipe |

Notes:

- A symlinked root is refused, and a symlink inside a tree counts as one item
  and is never followed. A hostile directory cannot redirect the walk.
- A directory the agent cannot read marks the bucket `safeToAnalyze: false`
  and keeps the partial total, so `/tmp` on a shared host reports a lower
  bound and says so rather than presenting an estimate as exact.
- Each walk is capped at 200 000 entries and 8 GiB, so a pathological
  directory cannot stall the agent.
- Only byte totals and item counts leave the machine. No file name, no path
  and no content is ever reported; the collector never opens a file, it only
  `stat`s it.

## Owner presence (Phase 5)

The agent also owns the trust state machine. It authenticates its owner on
the device — face verification when a camera, PIL and an enrolled template
exist, a salted PIN digest otherwise — and reports the **outcome** only.

Four states, one gate:

| State | Meaning |
| --- | --- |
| `OWNER` | the owner authenticated. The only state owner-only paths run in |
| `GUEST` | an unknown person is at the machine and Guest Mode is on |
| `UNKNOWN` | no authentication succeeded — the protected default, never assumed |
| `LOCKED` | 3 consecutive failures. Only an owner authentication clears it |

The initial state is always `UNKNOWN`. There is no "default to owner" path
anywhere, and a host with no camera and no enrolled PIN stays `UNKNOWN`
honestly rather than being granted.

What never leaves the machine: the face template, the captured image and the
liveness signal. Only the boolean result of a verification is consumed, and
the `security.state` frame has no field for any of them. A security photo is
captured **only** on the third failure, is stored beside the token at mode
`0600`, capped at 96 px on the long edge and grayscale, and is never attached
to a frame.

The durable state lives in `VIGILIA_STATE_DIR`:

| File | Contents |
| --- | --- |
| `owner-state.json` | the trust state, `since`, the failure count, the last authentication, the last daily-cleanup date |
| `daily-cleanup.json` | the once-per-day counter and the Daily Auto Cleanup config |

Both are `0600`. The state survives a reboot — but the *daily cleanup trigger
does not*: it is set only by an authentication that concluded `OWNER` and is
cleared on read, so a boot-time `OWNER` state never arms a cleanup.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `registration rejected by backend` | Backend reachable? `curl :3000/api/health` |
| `token verification unreachable` | Same — then the agent retries registration |
| Dashboard shows `No data` for a card | The agent does not report that metric (GPU, temperature). It renders `—` rather than a stale mockup number |
| Security screen says `Unknown user` | Correct: no owner authentication has succeeded, and `UNKNOWN` is the protected default. Enroll a PIN or a face template |
| Security screen stuck on `Waiting for the device to report…` | The agent's `security.state` frame never landed — check the agent log for `owner presence rejected locally`, and that the device the dashboard opened matches the one the agent registered |
| Agent logs but dashboard never updates | Token file stale; delete `.agent-token` and let it re-register, or the device is not the one open in the dashboard (`?device=<id>`) |
