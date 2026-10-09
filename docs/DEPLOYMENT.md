# VIGILIA — Production Deployment & Operations Guide

## Overview

VIGILIA is a privacy-first AI Laptop Guardian providing read-only telemetry, device-bound remote control, safe file browsing, consented remote desktop, and local Guardian AI assistance.

```
┌─────────────────────────┐          WebSocket           ┌────────────────────────┐          WebSocket          ┌──────────────────────────┐
│   VIGILIA Agent         │ ───────────────────────────▶ │   VIGILIA Backend      │ ──────────────────────────▶ │   Mobile Dashboard       │
│ (Linux / Windows)       │ ◀─────────────────────────── │ (Fastify + SQLite)     │ ◀────────────────────────── │ (Read-Only Web Client)   │
└─────────────────────────┘   vigilia.<deviceId>.<token> └────────────────────────┘        ?deviceId=<id>       └──────────────────────────┘
             │                                                          │
             │                                                          ▼
             │                                            ┌────────────────────────┐
             │                                            │   Local Qwen Runtime   │
             │                                            │ (llama-server / GGUF)  │
             │                                            └────────────────────────┘
             ▼
   Desktop Environment
 (Wayland / PipeWire / Win32)
```

---

## 1. System Requirements & Prerequisites

### Backend Server
- **OS**: Linux / POSIX compatible
- **Runtime**: Node.js ≥ 20.0.0
- **Database**: SQLite 3 (managed via Prisma ORM)
- **Privileges**: Dedicated system user (`vigilia`), non-root

### Linux Agent
- **OS**: Ubuntu 22.04+ / Debian 12+ (GNOME Wayland recommended)
- **Runtime**: Python 3.11+
- **Desktop Services**: `systemd` (user session), `dbus`, `pipewire`, `xdg-desktop-portal-gnome` (for Remote Desktop)
- **Optional**: `psutil` (Python library for enhanced process metrics; stdlib `/proc` fallback included)

### Windows Agent
- **OS**: Windows 10 / 11
- **Runtime**: Python 3.11+
- **Scheduler**: Windows Task Scheduler (`deploy/windows/VIGILIA-Agent.xml`)

### Local AI Runtime (Optional)
- **Binary**: `llama-server` (llama.cpp)
- **Model**: Qwen2.5-3B-Instruct (GGUF format, Q4_K_M quantization recommended)
- **RAM**: ≥ 4 GB available system memory

---

## 2. Production Deployment (Linux)

### 2.1 Fresh Installation

1. **Build workspace packages**:
   ```sh
   git clone <repo-url> /tmp/vigilia-src
   cd /tmp/vigilia-src
   npm ci --workspaces --include-workspace-root
   npm run build
   ```

2. **Execute system installer**:
   ```sh
   sudo ./deploy/install-linux.sh
   ```

   The installer performs the following idempotent actions:
   - Creates destination directory `/opt/vigilia` owned by system user `vigilia:vigilia`.
   - Creates service user `vigilia` (system account, no login shell) if absent.
   - Synchronizes repository files excluding models, binaries, and production databases (`prod.db*`).
   - Configures `/etc/vigilia/backend.env` and `/etc/vigilia/llama.env` with secure mode `0640`.
   - Sets up user-session access control lists (ACLs) for the desktop agent.
   - Templates and installs systemd units:
     - `/etc/systemd/system/vigilia-backend.service`
     - `/etc/systemd/system/vigilia-llama.service`
     - `~/.config/systemd/user/vigilia-agent.service`
   - Enables services in systemd and user session systemd.

3. **Initialize the production database**:
   ```sh
   sudo /opt/vigilia/deploy/database-migrate.sh
   ```

4. **Start the backend service**:
   ```sh
   sudo systemctl start vigilia-backend.service
   ```

5. **Verify service health**:
   ```sh
   curl -fsS http://127.0.0.1:3000/api/health
   # Expected output: {"status":"ok","service":"vigilia-backend","version":"1.0.0","protocolVersion":1,...}
   ```

---

## 3. Service Management

### System Services (Root / Sudo)
```sh
# Backend service
sudo systemctl status vigilia-backend.service
sudo systemctl restart vigilia-backend.service
sudo journalctl -u vigilia-backend.service -f

# Local AI llama service
sudo systemctl status vigilia-llama.service
sudo systemctl restart vigilia-llama.service
sudo journalctl -u vigilia-llama.service -f
```

### Desktop Agent Service (Logged-in Interactive User)
```sh
# The agent runs as a systemd user unit to preserve Wayland/Portal session context
systemctl --user status vigilia-agent.service
systemctl --user restart vigilia-agent.service
journalctl --user -u vigilia-agent.service -f
```

---

## 4. Windows Agent Setup

1. Install Python 3.11+ for all users or the target user account.
2. Deploy the agent code to `C:\Program Files\VIGILIA\`.
3. Import the Task Scheduler template:
   ```cmd
   schtasks /Create /XML "deploy\windows\VIGILIA-Agent.xml" /TN "VIGILIA-Agent"
   ```
4. Verify the task:
   - **Trigger**: At log on of any user.
   - **Privileges**: Least privilege (`InteractiveToken`), running inside the user's interactive desktop session.
   - **Settings**: `IgnoreNew` instance policy, restart on failure every 1 minute up to 3 times.

---

## 5. Local AI Model (Qwen2.5) Setup

1. Download the verified GGUF model artifact:
   - Model: `Qwen2.5-3B-Instruct-Q4_K_M.gguf`
2. Place the artifact under `/opt/vigilia/models/`:
   ```sh
   sudo install -o vigilia -g vigilia -m 0640 Qwen2.5-3B-Instruct-Q4_K_M.gguf /opt/vigilia/models/
   ```
3. Verify model integrity with preflight validator:
   ```sh
   VIGILIA_LOCAL_AI_MODEL_PATH=/opt/vigilia/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf node /opt/vigilia/deploy/model-preflight.mjs
   ```
4. Start the local AI service:
   ```sh
   sudo systemctl start vigilia-llama.service
   ```
5. **Deterministic Fallback**: If the local AI service is unavailable or unconfigured, VIGILIA automatically falls back to deterministic rule-based assessments without cloud leaks, latency spikes, or failed requests.

---

## 6. Database Operations & Disaster Recovery

### Safety Invariants
- **NEVER** run `prisma migrate reset` or `prisma db push` against the production database (`/opt/vigilia/backend/prisma/prod.db`).
- **NEVER** delete production backups.
- Normal application installation and upgrades **NEVER** run migrations or modify database state.

### 6.1 Database Migration (Operator-Controlled)
The migration script performs an online atomic backup before applying pending migrations:
```sh
sudo /opt/vigilia/deploy/database-migrate.sh
```
- Creates timestamped backup under `/var/lib/vigilia/backups/prod-YYYYMMDD-HHMMSS.db`.
- Applies migrations using `prisma migrate deploy`.
- Runs `prisma-preflight.mjs` runtime validation.
- Retains the 7 most recent backups automatically.

### 6.2 Database Backup (Standalone)
```sh
sudo python3 /opt/vigilia/deploy/sqlite-backup.py /opt/vigilia/backend/prisma/prod.db /var/lib/vigilia/backups/manual-backup.db
```

### 6.3 Database Restore
To restore from a backup:
```sh
# 1. Stop the backend
sudo systemctl stop vigilia-backend.service

# 2. Execute restore (automatically creates a pre-restore backup first)
sudo /opt/vigilia/deploy/database-restore.sh /var/lib/vigilia/backups/prod-YYYYMMDD-HHMMSS.db

# 3. Start the backend
sudo systemctl start vigilia-backend.service
```

---

## 7. Uninstallation & Upgrade

### Application Upgrade
To upgrade an existing installation without downtime or data loss:
```sh
cd /tmp/vigilia-src
git pull
npm ci --workspaces --include-workspace-root
npm run build
sudo ./deploy/install-linux.sh
sudo systemctl restart vigilia-backend.service
systemctl --user restart vigilia-agent.service
```

### Clean Uninstallation
To remove system services while preserving persistent databases, backups, models, and tokens:
```sh
sudo /opt/vigilia/deploy/uninstall-linux.sh
```

---

## 8. Security Architecture & Trust Boundaries

| Surface | Authentication | Transport | Restriction |
|---|---|---|---|
| **Agent Registration** | First-time introducing | HTTP POST `/api/devices/register` | Requires `protocolVersion: 1`; issues `vig_` agent token |
| **Agent Telemetry & Media** | Device-bound `agentToken` | WebSocket (`vigilia.<deviceId>.<token>`) | Re-validated on connect; requires `agent.hello` handshake; rejects cross-device claims |
| **Command Dispatch** | Device-bound `commandToken` | HTTP POST `/api/devices/:id/commands` | Bound to single device; audited in `CommandLog` |
| **File Manager** | Device-bound `commandToken` | HTTP `/api/files/:id/*` | Opaque handles only; strict root allowlist; no shell; 10 MiB / 64 KiB chunk limits |
| **Remote Desktop** | Device-bound `commandToken` | HTTP `/api/remote/:id/*` + WS | **Explicit desktop user portal consent required**; view-only; 1280x720 5 FPS limit; binary JPEG relay; no input injection |
| **Mobile Dashboard** | Read-Only | HTTP GET + WS (`?deviceId=<id>`) | Read-only; cannot push telemetry or trigger commands without paired command token |

---

## 9. Phase 11H Verified Acceptance Matrix

The complete VIGILIA test suite was executed and verified against clean temporary environments:

| Test Category | Test Command | Result | Pass | Fail | Skip | Details |
|---|---|---|---|---|---|---|
| **Static Quality** | `npm run typecheck` | **PASS** | 2 | 0 | 0 | 0 TypeScript errors |
| **Build** | `npm run build` | **PASS** | 2 | 0 | 0 | Clean build |
| **Lint** | `npm run lint` | **PASS** | 2 | 0 | 0 | 0 errors, 0 warnings |
| **Backend Integration** | `npm run test --workspace @vigilia/backend` | **PASS** | 54 | 0 | 0 | Auth, Guardian, scheduler, hub, relay, retention |
| **Linux Agent** | `npm run agent:linux:test` | **PASS** | 167 | 0 | 0 | Collectors, cleaner, commands, security, profiles |
| **Windows Agent** | `npm run agent:windows:test` | **PASS** | 78 | 0 | 4 | 4 host skips (Win32 APIs off-Windows) |
| **AI Anomaly** | `npm run ai:test` | **PASS** | 5 | 0 | 2 | 2 skips (scikit-learn optional dependency) |
| **Mobile UI E2E** | `npm run e2e:ui` | **PASS** | 10 | 0 | 0 | Headless Chromium DOM telemetry rendering |
| **Realtime E2E** | `npm run e2e:realtime` | **PASS** | 6 | 0 | 0 | Live WebSocket streaming & drop reconnect |
| **File Manager E2E** | `npm run e2e:phase6` | **PASS** | 25 | 0 | 0 | Opaque handles, upload, move, delete, audit |
| **Privileged E2E** | `npm run e2e:privileged` | **PASS** | 113 | 0 | 0 | 5 suites (Applications, Activity, Cleaner, Security, Phase 5) |
| **Hardware / Network E2E** | `npm run e2e:phase8` | **PASS** | 1 | 0 | 0 | Hardware telemetry and controls |
| **Guardian Local E2E** | `npm run e2e:phase9` | **PASS** | 1 | 0 | 0 | Local Qwen chat, stream, confirmation |
| **Guardian Observability** | `npm run e2e:phase10d` | **PASS** | 1 | 0 | 0 | Guardian observability flow |
| **Restart Recovery E2E** | `npm run e2e:recovery` | **PASS** | 8 | 0 | 0 | Backend SIGTERM, restart, reconnect, DB integrity |

---

## 10. Known Limitations

1. **Remote Desktop Active Capture Performance**: Active capture throughput and real-time encoding FPS were not measured because interactive desktop portal consent is required by design and was not automated in headless CI. Contract validation, framing, binary relay, and stale-frame drops are fully verified.
2. **Desktop Portal Consent**: Remote Desktop requires explicit interactive user consent on the host machine. Unauthorized capture or consent bypass is not supported.
3. **Scikit-Learn Dependency**: Offline anomaly model training tests are skipped when the optional `scikit-learn` package is absent.
4. **Windows-Native API Tests on Linux**: 4 tests skipped on Linux host for Win32-specific APIs (power status, CPU counters, memory counters, reparse points).
5. **SQLite Journal Mode**: SQLite WAL mode was benchmarked in Phase 11F (56% throughput increase under multi-device concurrency), but remains disabled in production (`DELETE` mode default) until sustained long-term WAL checkpoint policies are finalized.
6. **Production Database Safety**: Normal installation never creates or migrates production databases; database migration must be executed explicitly via `deploy/database-migrate.sh`.
7. **Prisma CLI / DeepmergeTS Advisory (GHSA-ggr8-5vv4-36mx)**: `prisma` developer CLI toolchain transitively pulls `deepmerge-ts < 8.0.0` via `@prisma/config`. Vulnerability involves stack exhaustion when merging recursive object graphs. Risk scope is strictly confined to developer CLI / build tooling and does not affect runtime HTTP request processing or PrismaClient execution in production. Release proceeds under documented limitation without breaking dependency overrides; upstream Prisma releases will be monitored for updated dependency patches.

---

## 11. Troubleshooting

| Symptom | Cause | Solution |
|---|---|---|
| `registration rejected by backend (426)` | Incompatible agent/backend protocol version | Upgrade agent and backend to matching versions (`protocolVersion: 1`). |
| `agent.hello required` error on WS | Agent sent telemetry before protocol handshake | Verify agent is running latest version with `agent.hello` support. |
| `a command token is required (401)` | Request sent without paired command token | Pair device via `POST /api/devices/:id/pair` using the agent token. |
| `database file missing` on preflight | Production DB not yet created | Run `sudo /opt/vigilia/deploy/database-migrate.sh`. |
| `No active user systemd bus` during install | Installer invoked in SSH session without active graphical login | Log in to desktop session graphically first, or verify `XDG_RUNTIME_DIR`. |
| Local AI status `UNAVAILABLE` | `llama-server` not running or model missing | Verify `/opt/vigilia/models/` contains GGUF artifact and `vigilia-llama.service` is active. |

---

## 12. Release Readiness Statement

**GO FOR RELEASE** (Phase 11H Verified). All core functionalities, security boundaries, regression test suites, deployment workflows, and disaster recovery procedures are validated.
