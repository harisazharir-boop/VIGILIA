# VIGILIA — AI Laptop Guardian

Read-only system telemetry for laptops: a small agent on each machine, a
Fastify backend with a WebSocket hub, and a mobile web dashboard.

```
agent (Linux/Windows)  ──ws──▶  backend  ──ws──▶  mobile dashboard
   psutil / Win32 api            Fastify + Prisma      static HTML/JS
        ▲ command.dispatch ────── command.result ──────┘
```

Telemetry is read-only outside explicitly paired controls. Remote commands (`LOCK`, `SLEEP`, `HIBERNATE`,
`RESTART`, `SHUTDOWN`) travel the same socket but through a separate
allowlisted path — no shell exists anywhere in the chain. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#command-path).

The agents only **report** by default. They do not execute commands, read files outside
`/proc`, `/sys` or the Win32 APIs, or change system settings — see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#what-the-agent-will-never-do).

## Layout

```
agents/           Python agents (stdlib-first; psutil optional)
  common/         shared: config, logger, telemetry contract, token store, WS client
  linux/          psutil + /proc fallback
  windows/        ctypes into kernel32/psapi, stdlib fallback
backend/          Fastify + Prisma (SQLite) + WebSocket hub
shared/           TypeScript types + zod schemas — the contract for everything
mobile/           dashboard static assets + e2e tests
docs/             ARCHITECTURE.md, AGENTS.md, API.md, PROGRESS.md
scripts/          backend-start.sh
index.html styles.css app.js vigilia.js   the dashboard, served by the backend
```

## Quickstart

Requirements: Node.js ≥ 20, Python 3, Chromium (only for the e2e checks).

```sh
npm install                       # backend + shared workspaces
cp .env.example .env              # then edit VIGILIA_SERVER_SECRET
npx prisma generate --schema backend/prisma/schema.prisma
npm run dev --workspace backend   # http://127.0.0.1:3000
```

In a second terminal, start an agent:

```sh
npm run agent:linux               # registers itself, then streams telemetry
```

Open `http://127.0.0.1:3000/` in a browser. That is the dashboard; pick a
device or pass `?device=<id>`.

## Scripts

```sh
npm run dev                      # backend in watch mode
npm run build                    # tsc for backend + shared
npm run test                     # backend suite
npm run typecheck                # tsc --noEmit across workspaces
npm run lint                     # ESLint 9 flat config, both workspaces
npm run agent:linux              # python3 agents/linux/main.py
npm run agent:windows            # python3 agents/windows/main.py
npm run agent:linux:test         # Linux agent suite
npm run agent:windows:test       # 77 tests (3 host-native skips off-Windows)
npm run e2e:ui                   # mobile UI ↔ backend (headless Chromium)
npm run e2e:realtime             # agent → backend → mobile WS → live DOM
npm run e2e:applications         # application control: Open/Restart/Close
npm run e2e:phase7               # consented Wayland portal -> PipeWire -> browser stream
npm run e2e:phase8               # real hardware/network telemetry + controls UI
npm run e2e:phase9               # local Qwen Guardian status, chat and confirmation
npm run e2e:phase10d             # Guardian local observability flow
npm run ai:test                   # local anomaly preparation tests
npm run e2e:activity             # the audit trail on the Activity screen
npm run e2e:storage              # the storage screen
npm run e2e:cleaner              # the cleaner screen + a real cleanup
npm run e2e:security             # security regression (live agent + browser)
npm run e2e:phase5               # the security screen + Daily Auto Cleanup
npm run e2e:phase6               # Remote File Manager isolated real E2E
```

Agent behaviour is configured through environment variables or `.env` —
see [`docs/AGENTS.md`](docs/AGENTS.md).

## Remote File Manager (Phase 6)

Paired mobile clients can browse only fixed user roots and operate through
opaque agent-issued handles: `FILE_LIST`, `FILE_STAT`, bounded `FILE_SEARCH`,
`FILE_DOWNLOAD`, `FILE_UPLOAD`, `FILE_RENAME`, `FILE_MOVE`, and `FILE_DELETE`.
No request carries a filesystem path, shell command, glob, or agent token.

Transfers use request-bound 64 KiB chunks, a 10 MiB total limit, progress,
expiry, cancellation, temporary-file cleanup and streamed backend downloads.
The audit stores request/device IDs, operation, status, timestamps and a hash
of an opaque handle; it never stores paths, contents, or credentials. See
`docs/API.md`, `docs/ARCHITECTURE.md`, and `docs/AGENTS.md` for limits and
platform policy.

## Commands

Remote control is an allowlist of eight commands, never arbitrary execution — five OS verbs plus three application verbs:

```sh
# One-time pairing (requires the device's agent token):
curl -X POST http://127.0.0.1:3000/api/devices/<deviceId>/pair \
  -H "Authorization: Bearer <agent token>" \
  -H "x-vigilia-device-id: <deviceId>" -H "Content-Type: application/json" \
  -d '{"deviceId":"<deviceId>"}'
# → { "commandToken": "vc_…" }   keep this with the mobile client

# Dispatch a command:
curl -X POST http://127.0.0.1:3000/api/devices/<deviceId>/commands \
  -H "x-vigilia-command-token: <command token>" \
  -H "Content-Type: application/json" -d '{"command":"LOCK"}'
# → 202 { "command": { … "status": "sent" } }
```

The command name is validated as an enum on the backend and again on the
agent; each resolves to a fixed argv (Linux) or a fixed Win32 entry point
(Windows). Anything not in the eight is rejected before it reaches a socket.
Platform details: [`docs/AGENTS.md#commands`](docs/AGENTS.md#commands).

## Process telemetry

The agent also reports what is running — read-only, same collectors, no
process is ever touched:

```sh
curl http://127.0.0.1:3000/api/processes/<deviceId>
# → { "snapshot": { "processes": [ { pid, name, cpu, memoryMb, … } ], "fresh": true } }
```

Each snapshot replaces the previous one (a process list is current state, not
history), and the same view arrives live over `/ws` as `processes.latest`.

## Owner presence (Phase 5)

The agent authenticates its owner on the device — face verification when a
camera and an enrolled template exist, a salted PIN digest otherwise — and
reports the **outcome** only: a trust state, a timestamp, a failure count and
a capability set. No biometric data ever leaves the agent; the schema has no
field for a template, an image or a liveness signal.

```sh
curl http://127.0.0.1:3000/api/security/<deviceId>
# → { "presence": { "state": "UNKNOWN", … }, "events": […], "dailyCleanup": { … } }
```

Four states, one gate: `OWNER` (the only state owner-only paths run in),
`GUEST` (unknown person, monitored), `UNKNOWN` (the protected default — never
granted, never assumed) and `LOCKED` (3 consecutive failures).

**Daily Auto Cleanup** runs only after an authentication that concluded
`OWNER`, at most once per device-day, on the daily-safe subset of the cleanup
allowlist — never on boot, never because the Security screen was opened, and
never twice in one day (the counter is a durable file, so a reboot does not
reset it).

## Production Deployment (Phase 11G)

### Linux Deployment

Prerequisites: Linux with systemd, Node.js >= 20, Python 3 with venv/pip, GStreamer (for remote desktop).

```sh
# 1. Build TypeScript workspaces
npm run build

# 2. Run system installer (as user with sudo access)
sudo ./deploy/install-linux.sh
```

The installer:
- Deploys application to `/opt/vigilia` with non-root `vigilia` service account.
- Configures graphical agent as a systemd user unit under logged-in desktop user.
- Sets up `vigilia-backend.service`, `vigilia-llama.service`, and `vigilia-agent.service`.
- Preserves existing database (`backend/prisma/prod.db*`), models (`/opt/vigilia/models`), and backups.
- Never runs destructive database operations during normal application install/upgrade.

### Database Operations (Operator Controlled)

```sh
# Apply pending Prisma migrations with automatic pre-migration backup:
sudo /opt/vigilia/deploy/database-migrate.sh

# Restore database from backup (requires backend service stopped):
sudo systemctl stop vigilia-backend.service
sudo /opt/vigilia/deploy/database-restore.sh /var/lib/vigilia/backups/prod-YYYYMMDD-HHMMSS.db
sudo systemctl start vigilia-backend.service
```

### Local AI Model Setup

Place Qwen2.5-3B-Instruct GGUF under `/opt/vigilia/models/`:

```sh
sudo chown -R vigilia:vigilia /opt/vigilia/models
sudo chmod 0640 /opt/vigilia/models/*.gguf
node /opt/vigilia/deploy/model-preflight.mjs
```

### Uninstallation

```sh
# Remove systemd units while preserving databases, backups, and credentials:
sudo ./deploy/uninstall-linux.sh
```

### Windows Deployment

Import `deploy/windows/VIGILIA-Agent.xml` into Windows Task Scheduler for logon autostart under interactive user credentials.

## Documentation

| Doc | Contents |
| --- | --- |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | production deployment, operations, recovery & release acceptance matrix |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | data flow, security model, trust boundaries |
| [`docs/AGENTS.md`](docs/AGENTS.md) | install, configure and run an agent on Ubuntu or Windows |
| [`docs/API.md`](docs/API.md) | every REST route and WebSocket frame |
| [`docs/PROGRESS.md`](docs/PROGRESS.md) | what is done, layout notes, known gaps |

## Status

**Release Ready (Phase 11H Verified)**.
Backend, Linux agent, Windows agent, and mobile dashboard are functional and fully regression tested. Remote commands (`LOCK`, `SLEEP`, `HIBERNATE`, `RESTART`, `SHUTDOWN`, `APPLICATION_OPEN`, `APPLICATION_CLOSE`, `APPLICATION_RESTART`), Remote File Manager (opaque handle navigation/transfers), consented Remote Desktop (Wayland ScreenCast via `xdg-desktop-portal`), and local Qwen Guardian AI are fully validated. See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for full acceptance matrix, operational procedures, and known limitations.

## License

Private project, not licensed for redistribution.
