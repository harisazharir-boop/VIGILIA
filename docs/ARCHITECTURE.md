# VIGILIA — Architecture

How a telemetry sample travels from a laptop to a dashboard, and what the
system will never do.

## Phase 8 hardware and network

Phase 8 extends existing authenticated telemetry WebSocket frames rather than
adding a second device-state channel. Linux reads `/proc`, `/sys`, resolver
state, sockets and optional `psutil`; Windows uses existing Win32/approved
optional-library paths. No collector invokes a shell. Hardware controls use a
strict paired-token endpoint. Unsupported native operations return audited
`UNSUPPORTED`; no generic execution path exists.

## Phase 9 local Guardian

Guardian calls a local OpenAI-compatible Qwen2.5-3B-Instruct runtime only. If
unavailable, deterministic evaluation over persisted telemetry/security state
continues. Model input is bounded and redacted; templates, images, credentials,
paths and raw files are excluded. Alerts use existing authenticated WebSocket
broadcasts. Confirmation records are paired-token and device-bound, expire in
five minutes and never execute sensitive actions themselves.

## Phase 10B anomaly artifacts

Offline tooling exports only complete numeric telemetry features. Optional local
Isolation Forest fitting produces versioned JSON metadata/calibration, not a
pickle or executable model payload. Guardian loads an artifact only when its
format, version, exact feature set, evaluation marker, row minimum,
calibration bounds and SHA-256 digest validate. Rejection is fail-closed to
existing threshold rules; it never produces an anomaly from missing data.

## Phase 10C Guardian actions

Guardian has no execution channel. A recognized OS action is stored as a
short-lived confirmation preview. Only paired confirmation on same device
calls existing command dispatch; agent command result verifies outcome. No
filesystem, cleanup, application or arbitrary action intent can bypass this
path.

## Phase 10D Guardian observability

Guardian audit and alert reads are paired-token/device-bound. Alert cooldown
uses device, severity, category and message identity for five minutes.
Preference storage controls notification broadcast, not evaluation, audit or
security policy. Reconnect replay uses existing read-only WebSocket surface;
audit rows remain bounded metadata only.

## Phase 10E native brightness

Linux agent writes only a discovered writable sysfs backlight brightness file,
scaled from validated 0-100 input. Backend uses paired token, capability gate,
device-bound request ID and authenticated result binding. No subprocess,
shell, privilege escalation or generic hardware execution exists.

## Phase 10F local model UX

Local Guardian streaming is a paired-token SSE proxy to loopback local runtime.
Backend forwards only parsed bounded token deltas and aborts upstream on client
disconnect. Browser cancellation uses `AbortController`. Context deletion is
device-bound. Model unavailability, timeout and malformed SSE do not block
monitoring/security and return to deterministic Guardian behavior.

## Phase 11A service deployment

Production Linux backend runs under dedicated non-root system account. Agent
runs as graphical user's systemd user unit, never root, to retain Wayland
portal session access. Deployment uses production workspace dependency install,
Prisma generation, migration deploy and a Prisma connection preflight before
systemd enablement. Token plus device-id sidecar are agent-user-owned mode 0600.

## Phase 11B local model packaging

External official GGUF model files are excluded from Git and preserved outside
application rsync deletion. `vigilia-llama.service` runs llama.cpp as
`vigilia`, binds only loopback and uses bounded threads, context and one
parallel request. Startup validates GGUF presence, header, minimum size and
configured SHA-256. Guardian separates artifact path from runtime model alias;
status becomes `READY` only when both artifact and local `/v1/models` alias
validate.

## Components

```
┌──────────────┐   ws (subprotocol auth)   ┌──────────────┐   ws (?deviceId)   ┌──────────────┐
│  Linux agent │ ────────────────────────▶ │   backend    │ ─────────────────▶ │   mobile UI  │
│ Win32 agent  │   telemetry + heartbeat   │ Fastify+WS   │  sanitized frames │  HTML/JS/CSS │
└──────────────┘                           │  Prisma/SQLite│                   └──────────────┘
        ▲                                   └──────────────┘                            ▲
        │           POST /api/monitoring/:id/telemetry (HTTP fallback)                    │
        └────────────────────────────────────────────────────────────────────────────────-┘
```

- **Agent** (`agents/`) — Python 3, standard library only. Platform collectors
  (`agents/<os>/src/collectors.py`) read system statistics; `agents/common/`
  holds everything else: config, logging, the telemetry contract, the process
  contract, token storage and a hand-written RFC 6455 WebSocket client.
  `psutil` is used when importable and improves accuracy; without it Linux
  falls back to `/proc` + `/sys` and Windows to ctypes.
- **Backend** (`backend/`) — Fastify 5, `@fastify/websocket`, Prisma 6 with
  SQLite. Every inbound network input is validated with zod schemas from
  `shared/`. The WebSocket hub (`backend/src/ws/hub.ts`) is an in-memory
  fan-out: one agent's telemetry is persisted, then forwarded to every socket
  subscribed to that device.
- **Shared** (`shared/`) — TypeScript types and zod schemas. This is the
  single contract: the backend validates against it, the mobile client
  consumes the shapes it describes, and the Python agent mirrors it in
  `agents/common/telemetry.py`. When the contract changes, change it here.
- **Mobile UI** (`index.html`, `styles.css`, `app.js`, `vigilia.js`) — static
  files, no build step, served verbatim by `@fastify/static`. `vigilia.js` is
  the only file that talks to the backend (REST for snapshots, `/ws` for the
  live stream); `app.js` is the original UI logic and renders whatever
   `vigilia.js` hands it.

## Remote File Manager (Phase 6)

File control has separate control and transfer planes. The paired mobile token
authorizes a strict REST request; backend creates a request ID and audit row;
only the authenticated agent socket receives `file.dispatch`. The agent maps
opaque handles to a fixed safe-root policy and returns structured results.
Read-only sockets cannot dispatch and never receive agent-only capabilities.

Transfers are request/device-bound sessions. Agent and backend exchange
bounded chunks over the authenticated agent socket; browser upload chunks use
the paired-token HTTP endpoint; backend stages download chunks in a 0600 temp
file and streams them to the paired browser. No component buffers a whole file
as a command result. Maximum file and transfer size is 10 MiB; chunk size is
64 KiB; sessions expire after 120 seconds and cancellation removes incomplete
temporary data.

The agent accepts no client path. It canonicalizes every capability target,
rejects symlinks/reparse points, denies escape from approved roots and protects
system/VIGILIA/config/credential material. `FilesystemAudit` stores only a
hash of an opaque target handle, never a path or file content.

## Data flow

1. **Registration.** An agent with no stored token POSTs `name`, `os`,
   `hostname` and an optional `systemInfo` block to
   `/api/devices/register`. The backend answers with a `deviceId` and one
   opaque `vig_` token. The agent writes the token to a `0600` file
   (`.agent-token`) and never holds it anywhere else.
2. **Handshake.** The agent opens `ws://<host>/ws` with
   `Sec-WebSocket-Protocol: vigilia.<deviceId>.vig_<token>`. Authentication
   runs inside the connection handler, before the hub ever sees the socket, so
   a bad credential closes the connection without joining the fan-out.
3. **Telemetry.** Every `VIGILIA_TELEMETRY_INTERVAL` seconds the agent
   collects a sample and sends `{ type: "telemetry", data: <sample> }`. The
   backend validates it with `wsMessageSchema`, writes one immutable
   `Telemetry` row, then broadcasts a token-free `telemetry.latest` frame to
   every subscribed mobile socket. A heartbeat frame every
   `VIGILIA_HEARTBEAT_INTERVAL` seconds refreshes `Device.status` and
   `lastSeen`.
4. **Mobile.** The dashboard opens `/ws?deviceId=<id>` — no token, read-only —
   and receives `telemetry.latest`, `device.status` and an immediate replay of
   the last persisted sample so the dashboard renders before the agent's next
   push. It pings every 25 s and reconnects on its own.
5. **History.** `GET /api/monitoring/:id/history?limit=30` returns the series
   oldest-first for the sparklines.

## Trust boundaries

```
agent (untrusted network, holds a credential)  │  backend  │  browser (public, read-only)
```

- The **agent boundary** is the only place a credential crosses the network.
  The agent's token is presented either as `Authorization: Bearer vig_…`
  (REST) or in the WS subprotocol (browsers cannot set headers on a
  WebSocket, so the dashboard cannot use that path).
- The **browser boundary** is strictly read-only on the socket. A mobile
  socket carries only a `deviceId`; the hub marks it `readOnly`, and any
  inbound frame from it is answered with `{ type: "error", code: "FORBIDDEN" }`
  and ignored. The two Phase 5 writes (the Daily Auto Cleanup config and the
  security settings) are the exception, and they go through REST with the
  paired command token — never through the socket.

## Security model

- **One token per device**, issued once at registration, format
  `vig_<base64url(32 bytes)>`.
- **The database stores no raw tokens.** It stores
  `sha256(deviceId:token:serverSecret)`, so:
  - a token issued for device A cannot verify against device B (the id is
    mixed into the digest);
  - a copied dev database is useless against a server with a different
    `VIGILIA_SERVER_SECRET`.
- **Constant-time comparison** (`timingSafeEqual`) for every digest check.
- **The mobile client never sees an agent token.** `/api/devices` and
  `/api/devices/:id` return public views with no token fields, and
  `/api/auth/session` bootstraps a mobile session deliberately token-free.
- **Every input is validated.** zod schemas in `shared/src/schemas.ts` cover
  registration, telemetry, heartbeats and WS frames; percentages are clamped
  to `0–100`, timestamps must be ISO-8601, and a frame whose `deviceId` does
  not match the authenticated device is rejected.

## Application & process telemetry (Phase 3A)

An agent reports the process list on its own interval
(`VIGILIA_PROCESS_INTERVAL`, default 15 s). The chain mirrors telemetry:
agent → `processes` frame → validate → one upserted `ProcessSnapshot` row →
`processes.latest` broadcast to subscribers → `GET /api/processes/:id`.

```
agent                            backend                          mobile
  │  ws "processes" frame          │                                 │
  │ ─────────────────────────────▶ │                                 │
  │                                │ zod validate (cap 512, fields)  │
  │                                │ upsert one ProcessSnapshot row  │
  │                                │ processes.latest ─────────────▶ │
  │                                │              GET /api/processes │
```

Two deliberate differences from telemetry:

- **No history.** A process list is the current state, not a series. Every
  report replaces the single row for that device, so the table cannot grow
  and there is nothing to retain or expire.
- **The `deviceId` is rebound, exactly as telemetry is.** An agent may only
  publish a process list for the device its socket authenticated as; a frame
  naming another device is answered `FORBIDDEN` and nothing is written.

Process *control* does not exist yet. This phase reports only — there is no
kill, terminate or launch verb anywhere in the chain. Control will arrive in
Phase 3B as three new entries in the existing command allowlist
(`APPLICATION_OPEN` / `APPLICATION_CLOSE` / `APPLICATION_RESTART`), reusing
the pairing token, the audit row and the fixed-argv execution model from
Phase 2 rather than opening a new surface.

## Owner presence & Daily Auto Cleanup (Phase 5)

The trust state machine lives on the **device**, not the server. The agent
authenticates its owner (face verification when a camera and an enrolled
template exist, a salted PIN digest otherwise), and reports only the
*outcome*: a state, a timestamp, a failure count and a capability set.
Biometric data never leaves the agent — the schema has no field for a
template, an image or a liveness signal.

```
agent                            backend                          mobile
  │  ws "security.state"          │                                 │
  │ ─────────────────────────────▶ │                                 │
  │                                │ zod validate (no biometric field)│
  │                                │ upsert one OwnerState row       │
  │                                │ security.state ───────────────▶ │
  │                                │       GET /api/security/:id     │
  │  ws "security.event"           │                                 │
  │ ─────────────────────────────▶ │ severity derived from the type  │
  │                                │ one SecurityEvent row           │
  │                                │ security.event ──────────────▶  │
```

Four states, one gate:

| State | `is_owner()` | Meaning |
| --- | --- | --- |
| `OWNER` | yes | the owner authenticated. The only state owner-only paths run in |
| `GUEST` | no | unknown person + Guest Mode on: monitored, owner-only actions blocked |
| `UNKNOWN` | no | no authentication succeeded — the protected default |
| `LOCKED` | no | 3 consecutive failures; only an owner authentication clears it |

`is_owner()` is the single gate the rest of the agent consults. There is no
"default to owner" path anywhere, and the initial state is always `UNKNOWN`.

**Daily Auto Cleanup** has exactly one trigger — after an authentication that
concluded `OWNER` — and it is evaluated once per authentication
(`OwnerAuthenticator.consume_authentication()`, cleared on read). Boot does
not set it, a timer does not set it, and a durable `OWNER` state restored
after a reboot does not set it. The once-per-day counter is a **file** the
agent owns (mode 0600), so a reboot does not reset the daily limit; the day
is the device's own calendar day, because only it knows its timezone.

The categories are filtered through the daily-safe subset of the Phase 4
allowlist, and the cleanup goes through the *same* `Cleaner.execute()` a
manual dispatch uses — there is no second deletion path and no way for this
module to name a path. Downloads, Documents, projects, source, `node_modules`,
config, credentials, SSH keys, `.env` and databases have no action id at all.

The phone's only writes into this surface are two paired-token PUTs
(`daily-cleanup` config and the three security settings booleans). The
settings land on the `OwnerState.capabilities` row, which the agent re-reads
on its next report — so a capability the platform cannot honour re-syncs the
phone's switch to the truth instead of pretending.

## What the agent will never do

- no shell is spawned, no subprocess, no `eval`;
- no file is opened outside `/proc`, `/sys`, or (Windows) the Win32 device
  status APIs;
- no system setting is written, no process is killed, no package installed;

The one deliberate exception is the remote-command surface, and it is
allowlisted end to end rather than opened up:

- the protocol accepts exactly five frames: `LOCK`, `SLEEP`, `HIBERNATE`,
  `RESTART`, `SHUTDOWN`. Validation is by zod enum, so anything else is
  rejected at the schema before it reaches a socket.
- the backend never builds a command line from a request. It stores the
  command name and sends `{ type: 'command.dispatch', data: { command } }`.
- the agent re-validates the name against its own copy of the allowlist and
  resolves it to a **fixed argv** (Linux) or a **fixed Win32 entry point**
  (Windows). There is no parsing step between the allowlist check and
  execution, so a crafted frame cannot smuggle arguments.
- a command runs with a timeout, its outcome is always reported back as
  `command.result`, and every hop is one auditable `CommandLog` row keyed by
  a server-generated `requestId`.

If a future feature needs more than these five actions, that requires a new
allowlist entry, a new platform implementation and a deliberate security
review. It is not a config flag away. Process control will take exactly this
path (`APPLICATION_OPEN` / `APPLICATION_CLOSE` / `APPLICATION_RESTART` in
Phase 3B) rather than a generic verb.

## Command path

```
mobile                  backend                    agent
  │  POST /devices/:id/commands  │                    │
  │  (x-vigilia-command-token)   │                    │
  │ ───────────────────────────▶ │                    │
  │                              │ CommandLog row      │
  │  202 { command }             │ (status: queued)    │
  │ ◀─────────────────────────── │                    │
  │                              │ command.dispatch ─▶ │
  │                              │  (agent socket only)│ executes fixed argv
  │                              │ ◀── command.result  │
  │                              │ CommandLog update   │
  │   ws command.result          │ broadcast to        │
  │ ◀─────────────────────────── │  mobile subscribers │
```

Authorisation for this path is a **second credential**: pairing
(`POST /api/devices/:id/pair`, which requires the agent token) issues a
`vc_` command token bound to that device. The mobile socket itself stays
read-only — commands enter through REST, and the `command.dispatch` frame is
sent only to the device's read/write agent socket, never to a subscriber.

The same rows are the mobile **Activity** screen: `GET /api/devices/:id/commands`
returns them newest-first, and the mobile client maps `CommandStatus` onto
the feed's categories (`succeeded` → Success, `failed`/`expired` → Failed,
`queued`/`sent` → Pending). The feed is read-only by construction — a client
can only read rows the backend wrote over the lifecycle above, and an agent's
`command.result` is rebound to its own connection, so neither side can forge
an entry for another device.

## Operational notes

- The SQLite file is `backend/prisma/dev.db`; the relative path in
  `DATABASE_URL` is resolved against the **repo root**, not the process cwd
  (npm workspaces run the backend from `backend/`). See
  `backend/src/config/env.ts:resolveDatabaseUrl`.
- `@fastify/static` enumerates the served directory at boot, so a file added
  to the repo root is not visible until the backend restarts.
- The WebSocket plugin is registered at the app root, not inside the routes
  module: in a child scope the handler's second argument becomes a
  `FastifyReply` and the handshake headers become unreachable. Documented at
  `backend/src/ws/routes.ts:19`.
- `@fastify/websocket` v10 wraps the socket in a `Stream` (`.socket`), v11
  passes the `WebSocket` directly; the handler supports both shapes.
