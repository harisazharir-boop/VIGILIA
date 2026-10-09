# VIGILIA — API reference

Base URL: `http://<host>:3000`. All routes are prefixed `/api` except the
WebSocket. Every error is `{ "error": { "code": "<CODE>", "message": "…" } }`
with HTTP status; validation failures add an `issues` array.

Errors: `VALIDATION_FAILED` (400), `BAD_REQUEST` (400), `UNAUTHORIZED` (401),
`FORBIDDEN` (403), `NOT_FOUND` (404), `CONFLICT` (409), `INTERNAL_ERROR` (500).

## Hardware and network (Phase 8)

`GET /api/hardware/:id` returns current hardware and network telemetry from
the authenticated agent's latest sample. `GET /api/network/:id/diagnostics`
returns current structured network diagnostics. Neither route executes a
client-selected network command.

`POST /api/hardware/:id/controls` requires paired command token. Body is
strict `{ control, value }`; controls are fixed and numeric values are 0-100.
Every accepted attempt is stored in `HardwareAudit`. Native controls are not
available yet, so response is `501` with `UNSUPPORTED`; success is never faked.

## Local AI Guardian (Phase 9)

`GET /api/guardian/model-status` reports local Qwen runtime availability.
`POST /api/guardian/:id/chat` requires paired command token and runs only
registered read-only inspections. `GET /api/guardian/:id/alerts` returns
stored health/security alerts. Sensitive language creates a device-bound
confirmation; `POST /api/guardian/:id/confirmations` requires the same paired
token and persists approve/deny only. Guardian does not dispatch destructive
actions.

## Guardian policy workflow (Phase 10C)

Guardian previews only existing fixed OS commands and requires confirmation.
`POST /api/guardian/:id/confirmations` remains paired-token/device-bound; an
approved valid request dispatches through existing command audit/agent result
path. Denied, expired, foreign, duplicate, ambiguous and unsupported requests
do not dispatch a command.

## Guardian observability (Phase 10D)

`GET /api/guardian/:id/audit`, `/alerts` and `/preferences` require paired
device token. `PUT /api/guardian/:id/preferences` accepts strict booleans for
`healthAlerts` and `securityAlerts`. Identical alert conditions deduplicate for
five minutes. Preferences suppress WebSocket notification only; Guardian still
evaluates and audits real conditions. Reconnected read-only sockets replay ten
recent Guardian alerts.

## Local model UX (Phase 10F)

`POST /api/guardian/:id/chat/stream` requires paired token and emits local
OpenAI-compatible SSE token frames only while local model is ready. Closing the
client connection aborts backend local generation. `DELETE /api/guardian/:id/context`
requires paired token and clears only that device's bounded Guardian messages.
When local runtime is unavailable or times out, normal Guardian chat keeps its
deterministic real-telemetry fallback; no cloud provider is used.

## Native brightness (Phase 10E)

`POST /api/hardware/:id/controls` accepts `BRIGHTNESS` integer 0-100 with paired
token. Backend dispatches only when agent advertised writable native brightness
capability. Result must match authenticated device, request ID and control
before audit update. Unsupported hardware returns `UNSUPPORTED`; no other
hardware control is enabled.

## Authentication

Two credentials, two powers:

| Client | Credential | Power |
| --- | --- | --- |
| Agent | `Authorization: Bearer vig_…` + header `x-vigilia-device-id` | read + write telemetry for **its own** device |
| Browser | command token `x-vigilia-command-token: vc_…` (per device) | dispatch commands + read audit for **that** device |
| Browser (read-only) | none — just a `deviceId` | realtime telemetry stream only |

Agent tokens are issued once by `POST /api/devices/register`. Command tokens
are issued once by `POST /api/devices/:id/pair`. The database stores only
`sha256(deviceId:token:serverSecret)` for each; no endpoint ever returns a
token back, and an agent token cannot be presented as a command token (or
vice versa) because the prefixes differ.

## Health

### `GET /api/health`
Liveness. `{ status: "ok", service, uptime, timestamp }`

## Remote File Manager (Phase 6)

Every file route requires `x-vigilia-command-token` for the exact `:id` device.
Filesystem requests are strict objects and use opaque `f_...` handles issued
by the agent. Raw paths, globs, shell strings, unknown fields and arbitrary
options are rejected.

### `POST /api/files/:id/requests`

Queues one structured operation and returns `202 { requestId, status }`.
Operations: `FILE_LIST`, `FILE_STAT`, `FILE_SEARCH`, `FILE_DOWNLOAD`,
`FILE_UPLOAD`, `FILE_RENAME`, `FILE_MOVE`, `FILE_DELETE`. List/search results
return entries with opaque IDs, basename, file/directory type, size, modified
time and safe permissions. Search is bounded to 100 results and 5,000 scanned
entries. Delete permits only one file or an empty directory.

### Transfers

`FILE_DOWNLOAD` and `FILE_UPLOAD` create request-bound transfer sessions.
Chunks are at most 64 KiB and transfer totals at most 10 MiB. Browser upload
uses `PUT /api/files/:id/transfers/:requestId/chunks/:sequence`; completion is
`POST /api/files/:id/transfers/:requestId/complete`; download is streamed from
`GET /api/files/:id/transfers/:requestId/download`; cancellation is
`DELETE /api/files/:id/transfers/:requestId`. Sessions expire after 120
seconds. Frames are `file.transfer.start`, `accepted`, `chunk`, `progress`,
`complete`, `failed`, and `cancel`.

### `GET /api/files/:id/audit`

Returns device-scoped filesystem audit records. Each contains request ID,
device ID, operation, status, bounded reason and timestamps. The target hash
is intentionally not returned. File contents, raw paths, credentials and
tokens are never stored in this audit.

### `GET /api/health/live`
Trivial alive ping. `{ status: "alive", timestamp }`

### `GET /api/health/ready`
Database readiness — runs `SELECT 1`. `{ status: "ready", database: "connected", timestamp }`

### `GET /api/health/version`
`{ name, version, node, timestamp }` — `version` reports the Node build.

## Auth

### `POST /api/auth/verify` — agent
Confirms a stored token is still valid before the agent opens its socket.

```json
{ "deviceId": "cmul…", "token": "vig_…" }
```
→ 200 `{ valid: true, deviceId, name }` · 401 `UNAUTHORIZED` on any mismatch.

A token bound to device A returns 401 when presented for device B.

### `POST /api/auth/session` — mobile
Bootstrap a dashboard session from a device id. **Returns no token.**

```json
{ "deviceId": "cmul…" }
```
→ 200 `{ device: <DevicePublic> }` · 404 if unknown.

### `GET /api/auth/me` — agent
Self-lookup for a bearer pair.

Headers: `Authorization: Bearer vig_…`, `x-vigilia-device-id: <id>`
→ 200 `{ device: <DevicePublic> }` · 401 without both headers.

## Devices

### `POST /api/devices/register` — unauthenticated (the only such route)
Body (validated by `registerDeviceSchema`):

```json
{
  "name": "Haris's Laptop",
  "os": "ubuntu",
  "hostname": "haris-laptop",
  "agentVersion": "1.0.0",
  "systemInfo": { "platform": "Linux", "cpuCores": 8, "totalMemoryMb": 16112 }
}
```
`os` ∈ `ubuntu | windows | linux | unknown`.

→ 201 `{ deviceId, token: "vig_…", expiresAt }`

Re-registering the same `(name, hostname)` **re-issues a fresh token and
invalidates the old one** rather than creating a duplicate row. The hub also
broadcasts a `device.registered` frame to any open dashboard socket.

### `GET /api/devices` — public
→ 200 `{ devices: DevicePublic[] }`, ordered by `lastSeen` desc, each with its
latest telemetry sample. No token fields anywhere.

### `GET /api/devices/:id` — public
→ 200 `{ device: DevicePublic }` · 404 `NOT_FOUND`.

### `PATCH /api/devices/:id` — agent (own device only)
Headers: bearer + `x-vigilia-device-id` (must match `:id`).
Writable keys, strings only: `name`, `os`, `hostname`, `agentVersion`.
→ 200 `{ device: DevicePublic }` · 403 `FORBIDDEN` for another device's token.

### `POST /api/devices/:id/pair` — agent (own device only)
Issue a command token so a mobile client may send remote commands. This is
the only way to obtain one, and it requires the agent token: the caller must
already be the device.

Headers: `Authorization: Bearer vig_…`, `x-vigilia-device-id: <id>`.
Body: `{ "deviceId": "<id>" }`.

→ 201
```json
{ "deviceId": "cmul…", "commandToken": "vc_…", "expiresAt": 1790000000 }
```
· 401 `UNAUTHORIZED` without a valid agent token.

Re-pairing replaces the stored digest, which invalidates the previous command
token.

## Commands

Remote control is an allowlist, not a shell. Eight commands exist and nothing
else is accepted:

```
LOCK | SLEEP | HIBERNATE | RESTART | SHUTDOWN
APPLICATION_OPEN | APPLICATION_CLOSE | APPLICATION_RESTART
```

`RESTART` restarts the **operating system**. `APPLICATION_RESTART` restarts
**one application**. They are different commands and are not interchangeable.

The backend validates the request against the allowlist before it reaches a
socket; the agent validates it again on receipt and never derives argv from
the request. See `docs/AGENTS.md#commands` for what each command does on
each platform.

### `POST /api/devices/:id/commands` — paired command token
Dispatch a command to the device's live agent socket.

Headers: `x-vigilia-command-token: vc_…`.
Body (`commandRequestSchema`):

```json
{ "command": "LOCK", "note": "left the office" }
```
`command` is required and must be one of the eight; `note` is optional,
max 280 characters. The three `APPLICATION_*` commands additionally require
`target`: an `applicationId` from the device's catalog
(`GET /api/applications/:deviceId`), lowercase, charset `[a-z0-9._-]`:

```json
{ "command": "APPLICATION_CLOSE", "target": "firefox" }
```

A target is never an executable path and never a command line — the schema
rejects both. A target the device never registered is rejected as
`NOT_ALLOWED` before an audit row is created.

→ 202
```json
{ "command": { "id": "e83e2eb6-…", "deviceId": "cmul…", "command": "LOCK",
  "status": "sent", "note": "left the office", "target": null, "result": null,
  "createdAt": "…", "updatedAt": "…", "expiresAt": "…" } }
```
`status` is `sent` when an agent socket is live, `queued` when it is not
(the command is still created and audited; the agent receives nothing until
it reconnects, and the sweeper marks it `expired` after 60 s). `target` is
set only for `APPLICATION_*` commands and is stored on the audit row.

· 400 `VALIDATION_FAILED` for a command outside the allowlist, a target
  that is not a lowercase application id, or any field other than
  `command`/`note`/`target` — the schema is strict, so a probe carrying
  `path`, `argv` or any other key is rejected rather than silently having
  the extra field dropped
· 400 `NOT_ALLOWED` for a target the device's catalog does not contain
· 401 `UNAUTHORIZED` without a valid command token, or on an unpaired device
· 404 `NOT_FOUND` for an unknown device id

### `GET /api/devices/:id/commands` — paired command token
Audit history (same authorization as dispatch).

Query: `limit` (1–100, default 20).
→ 200
```json
{ "deviceId": "cmul…", "limit": 20,
  "commands": [ { "id": "…", "command": "APPLICATION_CLOSE", "target": "firefox",
                  "status": "succeeded", "result": "firefox: 1 process(es) closed",
                  "note": null, "createdAt": "…",
                  "updatedAt": "…", "expiresAt": "…" } ] }
```
Returned **newest-first** — an activity feed is read from the top, so the most
recent command is the first row the mobile client renders.

### Audit lifecycle

Every row is written by the backend over the command's lifetime; no endpoint
accepts an audit record from a client.

```
mobile POST /api/devices/:id/commands
  → ① row created (status queued), id = requestId
  → ② command.dispatch to the agent socket   → status sent
agent command.result
  → ③ row settled                             → status succeeded | failed
no reply by expiresAt (sweeper)
  → ④ row expired                             → status expired
```

`status` therefore spans the whole life: `queued` (no live agent yet) → `sent`
(dispatched to the agent) → `succeeded`/`failed` (the agent replied) or
`expired` (the deadline passed unanswered). A mobile UI maps these onto
activity categories: `succeeded` → Success, `failed`/`expired` → Failed,
`queued`/`sent` → Pending.

`createdAt` is when the request arrived; `updatedAt` is when the row last
changed (dispatch or settlement). `result` holds the agent's detail on either
outcome — on failure it is the platform's own reason, on success a short
confirmation. `target` is set only for `APPLICATION_*` commands.

Security binding:

- The `requestId` is generated server-side and never taken from a client.
- `applyCommandResult` rebinds the reply to the **authenticated connection's**
  device, so an agent cannot close another device's command.
- The catalog check runs before the row is created, so a rejected target
  produces a 400 and no audit trail — there was never a command.
- The history endpoint requires the device's own command token; a token for
  device B reading device A's audit is a 401.

## Applications (Phase 3B)

The controllable-application catalog: which applications this device allows
the mobile client to open, close or restart. The list is reported by the
device's own agent and **cannot be set from the API** — deciding what may be
launched belongs to the device, not to the phone.

### `GET /api/applications/:deviceId` — public
→ 200
```json
{ "catalog": { "deviceId": "cmul…", "timestamp": "…",
               "applications": [ { "applicationId": "firefox", "label": "Firefox",
                                   "running": true } ] },
  "fresh": true,
  "applications": [ <same list> ] }
```
`fresh` is `false` when the agent has never reported or its last catalog is
older than 600 s. `running` is derived from the last process snapshot, so a
catalog entry describes what the agent sees, not what the phone assumes.

A device that never pushed a catalog returns `{ "catalog": null, "fresh": false, "applications": [] }`.

This endpoint cannot be used to *register* an application: there is no POST.

## Storage (Phase 4A)

Read-only disk breakdown: how much space a fixed list of safe, well-known
locations occupy, measured against the filesystem totals. The agent decides
which locations it looks at — a census of Downloads, the user cache, the temp
directory, the trash, the package cache and the browser cache — and the mobile
client receives aggregates only.

There is no DELETE, no POST and no cleanup verb anywhere in this phase.

### `GET /api/storage/:deviceId` — public
→ 200
```json
{ "summary":
  { "deviceId": "cmul…",
    "totalBytes": 154882924544,
    "usedBytes": 22678073344,
    "freeBytes": 124643356672,
    "usagePercent": 14.64,
    "categories": [
      { "categoryId": "downloads",      "label": "Downloads",
        "bytes": 85421168, "itemCount": 2,   "safeToAnalyze": true },
      { "categoryId": "tmp",            "label": "Temporary files",
        "bytes": 28293567, "itemCount": 1276, "safeToAnalyze": false }
    ],
    "timestamp": "2026-09-30T09:36:29.000Z" },
  "fresh": true }
```
`fresh` is `false` when the agent has never reported or its last breakdown is
older than 600 s.

`safeToAnalyze` is the honesty flag. It is `false` when the agent measured
the bucket but could not fully read it — a directory it lacks permission to
list, or a walk that hit its cap. The `bytes` total is then a **lower bound**,
and a UI should say "partial" rather than present it as exact.

The payload carries no file name, no path and no content. Only byte totals
and item counts per category, plus the volume totals they are measured
against.

## Security (Phase 5)

The owner-presence surface: the device's own trust state, its security event
timeline, and the Daily Auto Cleanup configuration.

The owner's identity is established **on the device**. Biometric templates,
images and liveness signals never leave the agent — the backend holds the
*outcome*: a trust state, a timestamp, a failure count and a capability set.
None of the routes below accepts a biometric field, because the schema has
none.

Reads are public, exactly like the process, application, storage and cleanup
surfaces: the mobile dashboard carries no token for them. The two writes take
the **paired command token** — the same credential `APPLICATION_CLOSE` uses —
because they change how the device behaves. There is no second authorisation
system.

The four trust states:

| State | Meaning |
| --- | --- |
| `OWNER` | the owner authenticated (face or PIN). The only state owner-only paths run in |
| `GUEST` | an unknown person is at the machine and Guest Mode is on: monitored, owner-only actions blocked |
| `UNKNOWN` | no authentication succeeded. The protected default — never granted, never assumed |
| `LOCKED` | 3 consecutive failures. Only a successful owner authentication clears it |

### `GET /api/security/:deviceId` — public

The whole security view the dashboard renders. → 200

```jsonc
{
  "deviceId": "cmul…",
  "presence": {                       // null only if the device never reported
    "deviceId": "cmul…",
    "state": "UNKNOWN",               // OWNER | GUEST | UNKNOWN | LOCKED
    "since": 1791155820,              // epoch seconds this state began
    "lastAuthenticatedAt": null,      // epoch seconds, or null
    "failedAttempts": 0,
    "capabilities": {
      "faceRecognition": false,       // enrolment state the device owns
      "liveness": true,
      "guestMode": true,
      "intrusionDetection": true,
      "dailyCleanup": false
    },
    "lastDailyCleanupDate": null,     // "YYYY-MM-DD" or null
    "timestamp": "2026-10-05T01:06:06.895Z"
  },
  "events": [ <SecurityEvent>, … ],   // newest-first, up to 25
  "dailyCleanup": {
    "config": {
      "enabled": false,               // the conservative default
      "trigger": "AFTER_OWNER_LOGIN", // the only trigger
      "categories": [ "CLEAN_TEMP", "CLEAN_BROWSER_CACHE",
                      "CLEAN_THUMBNAIL_CACHE", "CLEAN_PACKAGE_CACHE" ],
      "trashEnabled": false,          // opt-in
      "logRetentionDays": 30
    },
    "lastCleanupDate": null,          // durable once-per-day state
    "lastCleanupAt": null,
    "lastResult": null
  }
}
```

A device that never reported gets `presence` with `state: "UNKNOWN"` and every
capability `false` — the protected default, never a claimed `OWNER`.

### `GET /api/security/:deviceId/state` — public

Owner-presence state only. → 200 `{ deviceId, presence }`. Same `presence`
shape as the full view.

### `GET /api/security/:deviceId/events` — public

The security event timeline, newest-first. → 200 `{ deviceId, events }`.

`?limit=<n>` caps the page (default 25, capped at 100).

The **severity is derived server-side from the event type** and is never
accepted from a client, so a device cannot report `security_lock_triggered`
as `ok`. Metadata is bounded to an enumerated key set (`failedAttempts`,
`bytesFreed`, `actions`, `reason`, `actionId`) that cannot hold a path, a
credential or biometric data.

| `eventType` | Severity |
| --- | --- |
| `owner.authenticated` | ok |
| `owner.authentication_failed` | warn |
| `owner.locked` | bad |
| `unknown_person_detected` | warn |
| `guest.mode_activated` / `guest.mode_deactivated` | warn |
| `security_photo_captured` | warn |
| `security_lock_triggered` / `security_lock_cleared` | bad |
| `daily_cleanup.started` | ok |
| `daily_cleanup.completed` | ok |
| `daily_cleanup.skipped` | warn |
| `daily_cleanup.failed` | bad |

### `PUT /api/security/:deviceId/daily-cleanup` — paired command token

Set the Daily Auto Cleanup configuration. The categories are re-validated
against the allowlist and re-filtered against the daily-safe subset, so a
hostile or buggy client cannot arm an action that is not safe to run
unattended. → 200 `{ deviceId, config }`

```json
{ "config": {
    "enabled": true,
    "trigger": "AFTER_OWNER_LOGIN",
    "categories": [ "CLEAN_TEMP", "CLEAN_BROWSER_CACHE" ],
    "trashEnabled": false,
    "logRetentionDays": 30 } }
```

Unknown fields are a 400 — the schema is strict, so nothing unspecified can
be written. `CLEAN_TRASH` is armed only through `trashEnabled`; it is never
implied by the category list alone.

### `PUT /api/security/:deviceId/settings` — paired command token

Three booleans, no more:

```json
{ "settings": { "intrusionDetection": true, "guestMode": false, "autoLock": true } }
```

→ 200 `{ deviceId, settings }`. They are written to the stored
`OwnerState.capabilities` row, which the agent re-reads on its next report —
so the device keeps the authority, and a capability its platform cannot
honour re-syncs the phone's switch to the truth on the next push.

There is deliberately **no toggle for face recognition or liveness**. Those
are enrolment states the device owns; turning face recognition off from the
phone would silently downgrade the owner's protection.

## Processes (Phase 3A)

Read-only process telemetry. A device reports what is running; this endpoint
and the `processes.latest` frame expose the latest snapshot only — a process
list is the current state, not a series.

### `GET /api/processes/:deviceId` — public
→ 200
```json
{ "snapshot": <ProcessSnapshotPublic> | null, "fresh": true }
```
`fresh` is `false` when the agent has never reported or its last snapshot is
older than 90 s, so a UI can distinguish "nothing is running" from "the agent
has gone quiet".

### `ProcessSnapshotPublic`
```ts
{ deviceId, processes: ProcessInfo[], totalCount: number, limit: number,
  timestamp: string }
```
`totalCount` is how many processes were enumerated before the cap applied;
`limit` is the cap the agent used (`VIGILIA_PROCESS_LIMIT`, default 64).

### `ProcessInfo`
```ts
{ pid: number,                 // -1 when the pid could not be read
  name: string,                // never empty; unreadable is "unknown"
  cpu: number,                 // percentage of one core, 0–100
  memoryMb?: number,           // resident memory in MiB
  memoryPercent?: number,      // share of physical memory, 0–100
  threads?: number, parentPid?: number,
  status?: 'running'|'sleeping'|'stopped'|'zombie'|'idle'|'unknown',
  executablePath?: string,     // only when the agent may read it
  username?: string, startedAt?: string,
  commandLine?: string }       // only when VIGILIA_PROCESS_INCLUDE_COMMAND_LINE=1
```
`memoryMb` and `memoryPercent` are alternatives, not complements: Linux
reports a share of physical memory, Windows reports the working set in
bytes. Consume whichever is set.

## Monitoring

### `GET /api/monitoring/:deviceId/latest` — public
→ 200 `{ telemetry: TelemetryPayload | null }` (null when the device has never
reported).

### `GET /api/monitoring/:deviceId/history` — public
Query: `limit` (1–500, default 60).

→ 200 `{ deviceId, limit, samples: TelemetryPayload[] }` — returned
**oldest-first**.

### `POST /api/monitoring/:deviceId/telemetry` — agent (HTTP fallback for WS)
Headers: bearer + `x-vigilia-device-id`. The body's `deviceId` must equal the
route `:id` and the authenticated device.

Body: one `TelemetryPayload` (see below).
→ 201 `{ telemetry: TelemetryPayload }` — the persisted row.

## WebSocket — `/ws`

One endpoint, two handshake styles.

### Agent handshake

```
GET /ws
Sec-WebSocket-Protocol: vigilia.<deviceId>.vig_<token>
```

The credential rides in the subprotocol because a WebSocket client cannot set
arbitrary headers. The token never appears in the URL.

### Mobile handshake

```
GET /ws?deviceId=<id>
```

No credential. The socket is marked `readOnly`; any frame it sends is answered
with `{ type: "error", data: { code: "FORBIDDEN", message: "read-only connection" } }`
and discarded.

A bad agent credential closes the socket with code `4001 unauthorized` before
the hub registers it.

### Client → server frames (agents only)

Validated by `wsMessageSchema` (a discriminated union). Malformed JSON or a
failed parse is answered with an `error` frame and the connection stays open.

**Every frame's `data.deviceId` must equal the authenticated connection's
device** — checked once, before any frame type is dispatched. A mismatch is
answered `{ "code": "FORBIDDEN", "message": "deviceId mismatch" }` and
nothing is written. Rebinding alone would store the frame as the sender's own
data silently, which is safe but told a probing agent nothing about whether
the lie was noticed.

```jsonc
{ "type": "telemetry",
  "data": {
    "deviceId": "cmul…",              // must equal the authenticated device
    "cpu": 4.2, "ram": 61.3,          // percentages, 0–100
    "storage": 77.1, "battery": 82.0, // battery is a percentage too
    "uptime": 23714,                  // seconds, non-negative integer
    "os": "Linux-…", "hostname": "…",
    "gpu": 12.0, "temp": 54.5,        // temp: -40..200 (°C) — currently unreported
    "network": { "connected": true, "interface": "wlp2s0",
                 "ssid": "…", "downloadMbps": 12.5, "uploadMbps": 1.2 },
    "timestamp": "2026-09-28T16:42:10.000Z"  // ISO-8601, required
  } }
```
```jsonc
{ "type": "heartbeat", "data": { "deviceId": "cmul…", "status": "online",
                                 "timestamp": "…" } }
```
```jsonc
{ "type": "processes",
  "data": {
    "deviceId": "cmul…",                    // must equal the authenticated device
    "processes": [
      { "pid": 7052, "name": "firefox", "cpu": 22.5,
        "memoryMb": 930.5, "memoryPercent": 6.3,
        "threads": 33, "parentPid": 5225,
        "status": "sleeping",               // normalised enum, see ProcessInfo
        "executablePath": "/usr/bin/firefox",
        "username": "anyeong",
        "startedAt": "2026-09-29T03:56:50.820Z" }
    ],
    "totalCount": 245,                       // pre-cap enumeration count
    "limit": 64,                             // cap the agent applied
    "timestamp": "2026-09-29T16:42:10.000Z"
  } }
```
```jsonc
{ "type": "command.result",
  "data": {
    "requestId": "e83e2eb6-…",        // must be a command of THIS device
    "status": "succeeded",             // or "failed"
    "result": "permission denied",     // optional, max 1000 chars
    "durationMs": 120                  // optional
  } }
```
```jsonc
{ "type": "applications",              // Phase 3B: the controllable catalog
  "data": {
    "deviceId": "cmul…",              // must equal the authenticated device
    "applications": [
      { "applicationId": "firefox", "label": "Firefox" }
    ],
    "timestamp": "2026-09-29T16:42:10.000Z"
  } }
```
```jsonc
{ "type": "storage",                    // Phase 4A: read-only disk breakdown
  "data": {
    "deviceId": "cmul…",               // must equal the authenticated device
    "totalBytes": 154882924544,
    "usedBytes": 22678073344,
    "freeBytes": 124643356672,
    "usagePercent": 14.64,
    "categories": [
      { "categoryId": "downloads", "label": "Downloads",
        "bytes": 85421168, "itemCount": 2, "safeToAnalyze": true }
    ],
    "timestamp": "2026-09-30T09:36:29.000Z"
  } }
```
```jsonc
{ "type": "security.state",             // Phase 5A/5B: the owner-presence claim
  "data": {
    "deviceId": "cmul…",               // must equal the authenticated device
    "state": "UNKNOWN",                // OWNER | GUEST | UNKNOWN | LOCKED
    "since": 1791155820,
    "lastAuthenticatedAt": null,
    "failedAttempts": 0,
    "capabilities": { "faceRecognition": false, "liveness": true,
                      "guestMode": true, "intrusionDetection": true,
                      "dailyCleanup": false },
    "lastDailyCleanupDate": null,
    "timestamp": "2026-10-05T01:06:06.895Z"
  } }
```
```jsonc
{ "type": "security.event",             // Phase 5C/5D: one audited event
  "data": {
    "deviceId": "cmul…",               // must equal the authenticated device
    "eventType": "daily_cleanup.completed",
    "description": "Daily cleanup completed: 4 action(s), 91 MB freed",
    "since": 1791155820,               // optional epoch seconds
    "metadata": { "bytesFreed": 95585280, "actions": 4 },
    "requestId": null                  // optional, when tied to a command
  } }
```

A frame whose `data.deviceId` does not match the authenticated device is
rejected — an agent cannot spoof telemetry for another machine. A
`command.result` frame is rebound to the authenticated connection, so an
agent cannot close another device's command either.

A `command.dispatch` frame from the server carries a `target` for the three
application commands:

```jsonc
{ "type": "command.dispatch",
  "data": {
    "requestId": "e83e2eb6-…",
    "command": "APPLICATION_CLOSE",
    "target": "firefox",               // an applicationId the agent registered
    "deadline": 1790618540             // epoch seconds; past = expired
  } }
```

### Server → client frames

Every frame carries `type`, `deviceId`, `data` and a server `timestamp`.

| `type` | `data` | Sent when |
| --- | --- | --- |
| `telemetry.latest` | `{ deviceId, telemetry: TelemetryPayload }` | an agent pushed a sample (persisted first) |
| `processes.latest` | `{ deviceId, snapshot: ProcessSnapshotPublic }` | an agent pushed a process snapshot (persisted first) |
| `applications.latest` | `{ deviceId, catalog: ApplicationCatalogPublic }` | an agent pushed its controllable-application catalog (persisted first) |
| `storage.latest` | `{ deviceId, summary: StorageSummaryPublic }` | an agent pushed its read-only disk breakdown (persisted first) |
| `security.state` | `{ deviceId, presence: OwnerPresence }` | an agent pushed its owner-presence claim (persisted first) |
| `security.event` | `{ deviceId, event: SecurityEvent }` | an agent pushed one security event (persisted first) |
| `device.status` | `{ deviceId, status, lastSeen }` | connect, heartbeat, or status change |
| `device.registered` | `DevicePublic` | `POST /api/devices/register` |
| `error` | `{ code, message }` | malformed or forbidden frame |

On connect, the hub replays the most recent persisted telemetry sample,
process snapshot, application catalog, storage breakdown, cleanup plan,
owner-presence state and the last 25 security events, so a dashboard renders
immediately instead of waiting for the agent's next push.

Command dispatch goes the other way and uses a **different hub method**: a
`command.dispatch` frame is sent only to the read/write agent socket of the
device, never to a subscribed read-only mobile socket, even though both are
subscribed to the same device. A mobile socket that tries to send one is
answered `{ "code": "FORBIDDEN", "message": "read-only connection" }`.

## Types

Authoritative in `shared/src/types.ts` and `shared/src/schemas.ts`; the Python
agent mirrors them in `agents/common/telemetry.py`. The two must stay in sync.

### `DevicePublic`
```ts
{ id, name, os, hostname, status: 'online'|'offline'|'standby',
  lastSeen, agentVersion, createdAt, updatedAt, telemetry?: TelemetryPayload | null }
```

### `TelemetryPayload`
```ts
{ deviceId, cpu?, ram?, storage?, battery?, os?, hostname?, uptime?,
  gpu?, temp?, network?: NetworkStatus, timestamp }
```
All metrics optional; a metric the agent does not collect is absent, and the
dashboard renders `—` / `No data` for it rather than a placeholder number.

### `NetworkStatus`
```ts
{ connected: boolean, ssid?, downloadMbps?, uploadMbps?, interface? }
```

### `CommandRecord`
```ts
{ id, deviceId, command: DeviceCommand, status: CommandStatus,
  note?: string | null, result?: string | null,
  createdAt, updatedAt, expiresAt?: string | null }
```
`DeviceCommand` = `LOCK | SLEEP | HIBERNATE | RESTART | SHUTDOWN`.
`CommandStatus` = `queued | sent | succeeded | failed | expired`.

### `PairResponse`
```ts
{ deviceId, commandToken: string, expiresAt: number }
```

### `StorageSummary`
```ts
{ deviceId, totalBytes: number, usedBytes: number, freeBytes: number,
  usagePercent: number,           // 0–100
  categories: StorageCategory[],  // capped at 32
  timestamp: string }
```

### `StorageCategory`
```ts
{ categoryId: 'downloads'|'user-cache'|'tmp'|'trash'|'package-cache'|'browser-cache'|'other',
  label: string,
  bytes: number,
  itemCount: number,
  safeToAnalyze: boolean }        // false = partial measurement, bytes is a lower bound
```

### `OwnerPresence` (Phase 5)
```ts
{ deviceId: string,
  state: 'OWNER'|'GUEST'|'UNKNOWN'|'LOCKED',
  since: number,                  // epoch seconds this state began
  lastAuthenticatedAt: number | null,
  failedAttempts: number,
  capabilities: { faceRecognition: boolean, liveness: boolean,
                  guestMode: boolean, intrusionDetection: boolean,
                  dailyCleanup: boolean },
  lastDailyCleanupDate: string | null,   // "YYYY-MM-DD"
  timestamp: string }
```

### `SecurityEvent` (Phase 5)
```ts
{ id: string,
  deviceId: string,
  eventType: SecurityEventType,   // the enumerated table above
  severity: 'ok'|'warn'|'bad',    // derived server-side from the type
  description: string,            // max 280 chars
  timestamp: string,
  since: number | null,           // epoch seconds, when the event began
  metadata?: { failedAttempts?: number, bytesFreed?: number,
               actions?: number, reason?: string, actionId?: CleanAction },
  requestId: string | null }      // when tied to a dispatched command
```

### `DailyCleanupConfig` (Phase 5)
```ts
{ enabled: boolean,
  trigger: 'AFTER_OWNER_LOGIN',   // the only trigger
  categories: CleanAction[],      // re-filtered to the daily-safe subset
  trashEnabled: boolean,          // opt-in; never implied by categories
  logRetentionDays: number }      // 1–365
```

## Storage analysis is read-only

Phase 4A only measures. Nothing in the contract, the routes, the hub or either
agent is able to delete, move, rename or write a file: the backend exposes a
`GET` route only, the `storage` frame is agent-to-server, and the collector
walks with `os.scandir` + `stat(follow_symlinks=False)` without ever opening a
file for writing. A symlinked or reparse-pointed root is refused rather than
followed, so a hostile tree cannot redirect the walk; a directory the agent
cannot read marks the bucket partial instead of failing or being guessed at.

## Privacy

No file name, no path and no content crosses the wire — the payload is byte
totals and item counts per category. The agent never opens `.env`, credential
files, SSH keys, databases or source files; it does not read file contents at
all, only `stat` results.
