# VIGILIA — progress

## Done

### Phase 11B — Local AI Deployment & Model Packaging — COMPLETE

Production runs official Qwen2.5-3B-Instruct Q4_K_M GGUF through official
llama.cpp as non-root loopback-only `vigilia-llama.service`. Artifact format,
size and trusted SHA-256 validate before runtime start; backend requires both
validated local artifact and runtime alias `Qwen2.5-3B-Instruct` for `READY`.
Real paired production Guardian inference passed with Qwen output. Missing or
invalid model remains deterministic local Guardian fallback. Focused local
provider tests, Phase 1-10D regression, typecheck, lint and build passed.

### Phase 11A — Production Service & Autostart — COMPLETE

Linux deployment installs backend as dedicated non-root system user and agent
as graphical user service, preserving Wayland portal access. Installer performs
workspace production dependency installation, Prisma generation, non-destructive
migration deploy and runtime connection preflight before service enablement.
Agent token and non-secret device-id sidecar persist mode 0600 under the agent
user config directory, allowing authenticated restart without re-registration.
System service recovery, backend health, agent WebSocket/telemetry and Phase
1-10D regression passed. Windows Task Scheduler XML remains host-native only.

### Phase 10F — Local Model UX — COMPLETE

Guardian supports local llama.cpp/OpenAI-compatible SSE generation through the
existing paired-token, device-bound chat route. The browser streams bounded
tokens only when local model status is `READY`, offers cancellation, and can
clear device-bound conversation context. Unavailable, timed-out or cancelled
local generation leaves deterministic Guardian fallback active. Context remains
secret-redacted. Phase 9 E2E covers model-ready chat, stream and context clear.
Real GNOME portal retry passed Phase 7 capture/stream/reconnect; Phase 8, 9 and
10D E2E also passed. A parallel Phase 9/10D run can collide on mock port 8090;
sequential execution passes.

### Phase 10E — Native Hardware Controls — COMPLETE

Linux brightness is the only native mutation enabled. Capability is true only
for writable sysfs `/sys/class/backlight` entries with valid max brightness.
The paired-token backend dispatches request-bound `BRIGHTNESS` 0-100 only to
an advertising agent; authenticated matching result updates audit. All other
hardware controls, Windows controls, missing backlights and permission-denied
paths remain explicit `UNSUPPORTED`. No shell, subprocess or privilege
escalation exists.

### Phase 10D — Guardian Observability — COMPLETE

Guardian now exposes paired-token, device-bound audit history, alert history
and health/security notification preferences. Identical device/severity/category
conditions deduplicate for five minutes. Preferences suppress only mobile
broadcast, never server evaluation or audit. Read-only reconnect replays ten
recent Guardian alerts through existing authenticated WebSocket flow.

### Phase 10C — Guardian Policy Workflow — COMPLETE

Guardian action intents map only to existing fixed OS commands: Lock, Sleep,
Hibernate, Restart and Shutdown. Each request previews fixed action, request
ID, expiry and reason before approval. Confirmation is paired-token and
device-bound, expires after five minutes and records every decision. Approval
calls existing `dispatchCommand`; the authenticated agent's existing result
path remains execution verification. Ambiguous, file, cleanup, application and
unsupported requests dispatch nothing.

### Phase 10B — Evaluated Anomaly Pipeline — COMPLETE

Phase 10B adds an offline redacted telemetry export and Isolation Forest
artifact pipeline. Only complete numeric CPU/RAM/storage/temperature/latency/
packet-loss vectors train. Artifacts carry version, exact features, training
and evaluation metadata, fallback calibration and SHA-256 integrity. Production
rejects absent, invalid, tampered or incomplete artifacts and retains real
threshold fallback. No synthetic labels, cloud training or runtime model
deserialization is used. Core validation tests pass; sklearn-dependent fitting
tests are skipped on hosts without the optional local dependency.

### Phase 10A — Local AI Runtime Deployment — COMPLETE

llama.cpp is VIGILIA's documented local runtime profile for quantized
Qwen2.5-3B-Instruct GGUF artifacts. Models are external deployment files. The
profile binds loopback only, defaults to four CPU threads, 2048-token context
and one parallel request. Guardian checks `/v1/models` using bounded health
timeout and uses bounded inference timeout; unavailable, wrong-model or timed
out runtime fails back to deterministic Guardian behavior. Focused tests cover
ready, unavailable, timeout and post-restart recovery.

### Phase 9 — Local AI Guardian — COMPLETE

VIGILIA Guardian is local-first. Qwen2.5-3B-Instruct runs through a configured
local OpenAI-compatible runtime; no paid provider or 9Router dependency exists.
`/api/guardian/model-status` reports `READY` only when the configured model is
present. A missing runtime falls back to deterministic, real telemetry-based
Guardian analysis without disrupting monitoring or security.

- Guardian tools are registered read-only inspections of existing telemetry,
  security and cleanup-policy state. No shell, arbitrary execution, raw files,
  credentials or biometric material reaches the model.
- Local conversation memory is device-bound, capped to eight messages and
  redacts secret-like terms before persistence or inference.
- Health/security alerts are persisted and broadcast to mobile sockets from
  real telemetry and owner-state thresholds.
- Sensitive requests create a paired-token, device-bound five-minute
  confirmation record. Approve/deny is audited; Guardian dispatches no
  destructive action.
- An optional local Isolation Forest artifact can flag complete telemetry
  vectors. Missing/invalid artifact or incomplete metrics use honest threshold
  fallback; no anomaly is fabricated.
- Daily cleanup is only read as existing owner/once-per-day/safe-category
  policy. Guardian cannot bypass or trigger a separate deletion path.
- Verification: local model ready/unavailable tests, anomaly tests, backend
  36, Linux 145, common 162, Windows 81 with four expected native skips, and
  Phase 1-9 E2E passed; typecheck, lint and build passed.

### Phase 8 — Hardware + Network Intelligence — COMPLETE

Phase 8 extends existing authenticated telemetry with bounded hardware and
network detail. Linux reads `/proc`, `/sys`, resolver state, sockets and
optional `psutil`; Windows uses existing Win32/optional `psutil` paths.
Unavailable metrics remain absent with capability flags false.

- Hardware: CPU load, available RAM, storage, battery state, uptime, and sysfs
  CPU temperature/fan speed where exposed. GPU/VRAM/GPU temperature remain
  unavailable without a native metric source.
- Network: connection state, interface/type, IPv4, gateway, DNS and throughput.
  One bounded TCP DNS-port probe provides latency and packet-loss result.
- Controls: paired-token requests are strict and range-bounded. Native mutation
  is unavailable, so accepted requests return audited `UNSUPPORTED`.
- Verification: Linux 145, common 162, backend 34, Windows 81 with four
  expected host-native skips; Phase 1-8 E2E, typecheck, lint and build passed.

### Phase 7 — Consented Linux Wayland Remote Desktop — COMPLETE

Linux GNOME Wayland remote viewing is real, consented, bounded and paired.
The agent creates an `xdg-desktop-portal` ScreenCast session, requests source
selection through the system portal UI, opens its PipeWire remote FD, and runs
`pipewiresrc -> videoconvert/videoscale/videorate -> jpegenc -> appsink`.
There is no X11 path, privileged capture, fake frame, or consent bypass.

- Capture: source-selection consent is required per session. Capture becomes
  active only after first real PipeWire JPEG. Session close stops GStreamer and
  closes the portal session.
- Bounds: JPEG only, 1280x720 maximum, 5 FPS maximum, 900 KiB maximum frame,
  one-buffer leaky GStreamer queue and newest-frame delivery.
- Transport: agent media uses authenticated binary WebSocket envelopes. Backend
  validates active session, JPEG marker and frame cap before relaying only to
  read-only subscribers for same device. Browser reconnect resumes live frames.
- Capability: availability reports conservative bounds before consent;
  `screenshot` and `streaming` become true only after first real frame.
- Input: mouse and keyboard remain fail-closed. No EIS/libei injection exists.
- Verification: Linux 143, common 162, backend 32; Phase 1-7 E2E, typecheck,
  lint and build passed. `mobile/e2e-phase7.mjs` runs real isolated agent,
  portal consent, PipeWire capture, binary browser relay and reconnect.

### Phase 6 — Remote File Manager — COMPLETE

Phase 6 ships paired, structured remote file management for Linux and Windows.
The UI receives agent-issued opaque handles, never filesystem paths. Safe roots
are Home, Documents, Downloads, Pictures, Videos, Desktop and Projects (only
below Home). Traversal, symlink/reparse traversal, external Projects roots,
VIGILIA state/config, `.env`, key/certificate and credential material are
denied.

- Browser: list, stat, bounded name search, folder navigation, breadcrumb,
  upload/download progress and cancellation, rename, move picker, and strict
  file/empty-folder delete confirmation.
- Transfer: 64 KiB chunks, 10 MiB cap, request/device-bound sessions, ordered
  chunks, expiry, cancellation cleanup, and streamed backend downloads.
- Audit: `FilesystemAudit` records request/device/operation/status/timestamps,
  bounded reason, and only a SHA-256 hash of the opaque target handle.
- Verification: backend 31, common 162, Linux 139, Windows 81 with 4
  host-native skips; Phase 1–6 E2E, typecheck, lint and build all passed.
  `mobile/e2e-phase6.mjs` starts a real isolated Linux agent/backend/Chromium
  stack and disposable fixture. Windows native junction E2E was not run on
  this Linux host; Windows policy tests cover the portable contract.

### Phase 5 — security & owner protection — COMPLETE

The whole Phase 5 surface is live and rendered from the device's own reports:
the owner-presence state, the security event timeline, intrusion detection,
the guest monitoring view and the Daily Auto Cleanup settings.

**The bug this phase ended on.** The Security screen painted nothing. Clicking
`[data-nav="security"]` navigated correctly — `s-security` became active —
but `#secStateText` stayed on the placeholder forever, and `SEC_STATE` was
undefined. Navigation was not the problem, and neither was the API: a direct
`GET /api/security/:deviceId` answered the real state.

The chain was broken by a **missing closing brace**. `paintSecurityEvents` in
`app.js` opened `if (list) {` and never closed it. The function's own closing
`}` therefore closed the `if` instead, and every definition after it —
`loadSecurity`, `screenHooks.security`, the Daily Auto Cleanup config
renderer, the notification table and every `[data-sec-switch]` handler — was
swallowed into the function body. A duplicated `};` at the end of the block
kept the file parseable, which is why this was a runtime dead end and not a
syntax error.

Two consequences, and the second is the one that made it look like a
lifecycle bug:

1. `loadSecurity` was never hoisted, so `BACKEND.start()` threw
   `ReferenceError: loadSecurity is not defined` at boot and never registered
   the WebSocket listener. The security card on Home stayed on the placeholder
   too, and the splash never handed off.
2. `screenHooks.security` kept the `null` it was initialised with. `navTo`
   guards with `typeof hook === 'function'`, so the hook was silently never
   called — the screen opened and painted nothing, exactly the reported
   symptom.

Fix: one `}` closing the `if (list)` block, and deletion of the stray `};`.
The screen now loads through the real chain:

```
click Security → navTo → screenHooks.security → Vigilia.security(deviceId)
  → GET /api/security/:deviceId → SEC_STATE → the security UI repaints
```

**What the e2e run pins** (`npm run e2e:phase5`, `mobile/e2e-phase5.mjs`,
53 assertions, live agent + real backend + headless Chromium, nothing
mocked):

- the splash is the initial screen, and the hook is not called before the
  screen is opened
- the click makes `s-security` active, the hook runs exactly once,
  `Vigilia.security()` is called, the REST view answers 200 with the shape
  `{presence, events, dailyCleanup, deviceId}`, and the state the browser
  renders is the state the direct API call returns
- the state line reports a real trust state — never the placeholder, never
  the fabricated `Owner Mode · Protected`
- the Daily Auto Cleanup card renders the device's own config: the switch
  mirrors `dailyCleanup.config.enabled`, the category list is the device's
  own, the Trash row appears only when the device opted into it, and the
  last-run date is the device's own (or `Never`)
- the Daily Auto Cleanup gate end to end: opening the screen does not advance
  the durable state and records no cleanup event; the switch is a real write
  through the paired command token that re-renders from the stored answer;
  and arming the cleanup on a device that is not the owner does not run one
- the switch is restored to what the device had before the run, so the test
  leaves no residue
- a `security.state` frame from the agent reaches the app's WS listener and
  carries the reported state
- the security screen has no free-text field, no shell command is rendered
  anywhere, and `SEC_STATE` is the single state object (not duplicated on
  `window`)

**The Daily Auto Cleanup rule, unchanged and still enforced in code**
(`agents/common/daily_cleanup.py`): runs only after an authentication that
concluded `OWNER`, never on boot, never because the Security screen was
opened, at most once per calendar day (the counter is a durable file the
agent owns, so a reboot does not reset it), safe structured categories only
(filtered through the daily-safe subset of the Phase 4 allowlist), through
the same `Cleaner.execute()` a manual dispatch uses. `UNKNOWN`, `GUEST` and
`LOCKED` all skip with `NOT_AUTHENTICATED`. The trigger is
`consume_authentication()` — cleared on read, so a second tick the same
session cannot start a second run, and a boot-time `OWNER` state cannot set
it.

**Mock data removed.** The security surfaces now derive everything from the
device's own report:

- the guest screen's topbar badge was a static `Active`; it now renders from
  the same presence the banner does (`Waiting` until the device reports)
- the menu card's security label was a static `Protected`; it now derives
  from the presence, so a device that never reported shows `Waiting…`
- the guest screen's CPU/RAM/network sparklines kept the mockup's hardcoded
  waveforms; they are now drawn from the real history series and dropped when
  there is none, the same rule the home cards already followed

**Verification (node v22, host: anyeong-Vivobook-Go-E1404FA):**

```
npm test                                    # 26 tests, all pass
npm run typecheck --workspaces              # clean
npm run lint --workspaces                   # clean
npm run build --workspaces                  # clean
npm run agent:linux:test                    # 139 tests, OK
npm run agent:windows:test                  # 77 tests (3 host-native skips)
python3 -m unittest discover -s agents/common  # 155 tests, OK
npm run e2e:ui                              # 10 assertions
npm run e2e:realtime                        # 6 assertions
npm run e2e:applications                    # 7 assertions
npm run e2e:activity                        # 5 assertions
npm run e2e:storage                         # 12 assertions
npm run e2e:cleaner                         # 21 assertions
npm run e2e:security                        # 32 assertions
npm run e2e:phase5                          # 53 assertions
node mobile/ui-check-phase5.mjs             # 15 assertions
```

Two stale test expectations were found and fixed while the suite ran, both
app bugs rather than test bugs:

- `e2e-cleaner` asserted exactly five allowlisted actions; the agent now
  offers seven (`CLEAN_OLD_LOGS` and `CLEAN_THUMBNAIL_CACHE` were added in
  Phase 4). The assertion now compares against the device's own plan and
  separately checks the full allowlist, so a device that gains or drops an
  action is not a test failure and a non-allowlisted action still is one.
- `ui-check-phase5` counted `#guestEvents .ev`, which includes the empty
  placeholder. It now counts `.ev-body` rows, so an empty timeline is zero
  and not one.

### Phase 4E — real end-to-end + security regression — COMPLETE

A single run (`npm run e2e:security`, `mobile/e2e-security.mjs`) against the
real Linux agent, the real backend and a real browser, asserting the Phase 4
contract and the security invariants that have no unit test — because they
are ordering and authorization properties, and both only hold when the whole
chain is live.

It registers a second device to be the target of cross-device probes, uses an
application from the live agent's own catalog, and never mocks a socket, a
route or a process.

What the run found, and the fixes it forced:

- **The command → catalog race** (`app.js`). `command.result` and the
  application catalog both reach the phone, and the catalog's `running` flag
  is derived from the agent's *previous* process snapshot, so a REST reply or
  a WS push still in flight when the agent answers can describe a state that
  predates the command. The catalog then un-flips the row the agent just
  settled. Fix: the agent's own confirmation is remembered
  (`settledCommand`) and replayed over every catalog until one agrees with
  it, expiring after 60 s so the catalog stays the authority. The backend
  strips `timestamp` from the stored catalog, so no server clock could order
  them.
- **The catalog was frozen at agent boot** (`agents/linux/main.py`,
  `agents/windows/main.py`). `_send_applications` re-sent
  `self.applications.catalog` — the object built once in `__init__` — so the
  `running` flags never refreshed and a freshly-started application reported
  `running: false` forever. Discovery is re-run on every interval now; it is
  read-only (`shutil.which` plus the process name list).
- **The catalog was not pushed on connect.** A reconnect makes the backend
  replay the last *stored* catalog to every mobile socket, and that copy is
  only as fresh as the previous interval. The agent sends it on socket open
  too, so the replay cannot be minutes stale.
- **An application command did not refresh the catalog.** The agent answered
  `closed`, then left the phone holding a catalog that still said `running`
  until the next 120 s interval. The agent re-pushes immediately after an
  `APPLICATION_*` command settles — the catalog is the authority the UI
  reconciles on, so it has to be current.
- **`/proc/<pid>/comm` is truncated at 15 characters** by the kernel
  (`TASK_COMM_LEN`), so `gnome-calculator` read as `gnome-calculato` and
  never matched its own application id — the catalog reported a running
  application as stopped, but only on hosts without `psutil`. The stdlib
  fallback now reads `argv[0]` and uses `comm` only when it is empty.
- **`os` was never imported in `agents/common/applications.py`** yet was used
  by `_process_names`. It resolved only because another module in the same
  process had imported it; importing the module alone raised `NameError`.
  Imported explicitly.
- **Frames sent in the WebSocket handshake gap were dropped silently.** The
  message listener was registered *after* `await authenticate()` finished its
  database round-trip, so an agent sending in the same TCP flight as its
  `Upgrade` hit no listener: no error, no warning, no audit row. A comment
  claimed the opposite. The listener is now attached before the await; a frame
  that arrives mid-auth sees the read-only default and is answered `FORBIDDEN`
  rather than vanishing. This is also what made the cross-device assertion in
  this suite flaky until it was fixed.
- **A mismatched `deviceId` was silently rebound, not refused**
  (`backend/src/ws/routes.ts`). `cleanup` and `command.result` answered
  `FORBIDDEN`, but `telemetry`, `heartbeat`, `processes`, `applications` and
  `storage` stored the frame as the *sender's* own data with no error — so an
  agent could publish an arbitrary breakdown under a mismatched claim and
  learn nothing about whether the lie was noticed. The check now runs once
  for every frame, before any branch: the frame is dropped, the sender is
  told, nothing is written.
- **The command schema silently dropped unknown fields**
  (`shared/src/schemas.ts`). Zod objects strip by default, so
  `{ command: "CLEAN_TEMP", path: "/home" }` was accepted as 202 and the
  `path` discarded. Harmless — the command is still the allowlisted id — but
  a probe learned nothing, and a future field would be ignored the same way.
  `commandRequestSchema` and `cleanupRequestSchema` are now
  `z.strictObject`, so an unknown key is a 400.

Assertions (all pass against the live agent):

```
— read-only socket:
  a read-only socket that pushes is answered FORBIDDEN
  the read-only socket never saw its own telemetry echoed back
  the read-only push never reached the stored telemetry
  the read-only socket never wrote a storage breakdown
— cross-device frames:
  a frame naming another device is refused with FORBIDDEN
  the mismatched payload was never stored or broadcast
  the live device's breakdown was not overwritten by a foreign frame
— path and command injection:  11 probes, all 400
  no rejected probe reached the audit trail
— token scope:
  the live device's command token cannot command another device (401)
  the live device's token cannot read another device's audit (401)
  a bad command token is rejected (401)
— the command/catalog race:
  the agent confirmed the open and the row flipped
  the live agent settled the close
  a stale catalog delivered before the agent's push does not un-flip the settled row
  a catalog that agrees with the agent renders unchanged
  once the override expires, the catalog is authoritative again
— the screens:
  #s-storage / #s-cleaner / #s-activity have no free-text field
  the control screen's only input is the catalog search box
  no shell command is rendered anywhere in the UI
```

The race reproduction is deterministic, which is the part worth noting: the
agent's own catalog re-push (which now follows an application command) would
mask a stale push arriving later, so the stale one is delivered *first* —
through the same `setAppCatalog` the WS replay and the REST refresh both call,
at the moment `command.result` is processed.

### Phase 4D — storage and cleaner screens — COMPLETE

Both Phase 4 surfaces rendered from the device's own reports, with the
confirmation sheet the cleanup contract requires. See
`mobile/e2e-storage.mjs` (13 assertions) and `mobile/e2e-cleaner.mjs`
(23 assertions).

### Phase 4A — storage analyzer — COMPLETE

Read-only disk analysis. The agent measures how much space a fixed list of
safe, well-known locations occupy — Downloads, the user cache, the temp
directory, the trash, the package cache and the browser cache — and reports
the breakdown plus the filesystem totals. It is a census of named
directories, never a filesystem scan, and it is **analysis only**: no delete,
no unlink, no rmdir, no rename, no cleanup verb anywhere in this phase.

The store follows the Phase 3A pattern exactly — one upserted
`StorageReport` row per device, replaced wholesale on every report, broadcast
as `storage.latest` and replayed to a mobile socket on connect — so no new
architecture was needed, only a new model, frame and route.

- **Contract** (`shared/src/types.ts`, `shared/src/schemas.ts`):
  `StorageSummary` (deviceId, totalBytes, usedBytes, freeBytes, usagePercent,
  categories, timestamp) and `StorageCategory` (categoryId, label, bytes,
  itemCount, safeToAnalyze). `categoryId` is a fixed enum, the categories
  array is capped at 32 and every byte total is a non-negative integer, so a
  hostile agent cannot push an arbitrary bucket or flood a frame. Python
  mirror in `agents/common/storage.py`, which also validates locally before
  sending, as the other collectors do.
- **Linux collector** (`agents/linux/src/storage.py`): stdlib `os.scandir` +
  `stat(follow_symlinks=False)` walk. A symlinked root is refused, a symlink
  inside the tree counts as one item and is never followed — so a hostile
  tree cannot redirect the walk into `/etc` or a network mount. A
  `PermissionError` mid-walk marks the bucket `safeToAnalyze: False` and
  keeps the partial total: an honest lower bound, never an estimate and never
  a failure. Every walk is capped on entries (200 000) and bytes (8 GiB).
- **Windows collector** (`agents/windows/src/storage.py`): the same walk,
  with folders resolved through `SHGetKnownFolderPath` (Win32 shell API via
  ctypes) and a stdlib fallback. No PowerShell, no `cmd /c`, no WMI pipe, no
  subprocess of any kind. Off-Windows `is_supported()` is the gate: the
  collector reports an honest empty breakdown rather than fabricating Windows
  paths — only an injected `user_dirs` test double makes the walk run on a
  Linux host, which is exactly how the suite exercises the calculation.
- **Backend** (`backend/src/modules/storage/`): `GET /api/storage/:deviceId`
  is public like the process and application routes (the mobile dashboard is
  read-only and carries no token). There is no POST, no PUT and no DELETE —
  the only writer is the device's own agent over its authenticated socket,
  where `deviceId` is rebound to the connection so an agent cannot publish a
  breakdown for another machine. Privacy holds by construction: the payload
  is byte totals and item counts only. No file name, no path and no content
  is ever sent — the collector never opens a file, it only `stat`s it.

**Final verification (node v22, host: anyeong-Vivobook-Go-E1404FA):**

```
npm test                                    # 107 tests, all pass
npm run typecheck --workspaces              # clean
npm run lint --workspaces                   # clean
npm run build --workspaces                  # clean
```

Agent suites (the storage tests are platform-independent where they can be;
host-native behaviour skips where it cannot be):

```
npm run agent:linux:test                    # 102 tests (14 new)
npm run agent:windows:test                  # 65 tests (12 new)
agents/common/test_storage.py               # 11 contract tests
```

Verified against the real Linux agent on this host, streaming to a live
backend:

```
GET /api/storage/:deviceId
154 GB total · 22 GB used · 14.68 %
  downloads      Downloads              81 MB   items=2     safe=True
  user-cache     User cache             86 MB   items=770   safe=True
  tmp            Temporary files        27 MB   items=1564  safe=False
  trash          Trash                   0 MB   items=3     safe=True
  package-cache  Package cache         370 MB   items=219   safe=False
  browser-cache  Browser cache           0 MB   items=0     safe=False
```

The two `safe=False` rows are the point of the flag: `/tmp` and the apt
archive cache contain entries this user cannot read, so those buckets are
partial lower bounds and are labelled as such rather than presented as exact.
The agent also never followed a symlink in testing — the suite asserts it
directly against a tree built for the purpose.

Security properties the suite pins rather than asserts:

- a `storage` frame naming another device is answered `FORBIDDEN` and never
  stored as that device's breakdown;
- a mobile read-only socket cannot push one, and there is no endpoint a
  client can write a breakdown through (`POST`/`PUT`/`DELETE` all 404);
- a permission-denied directory never raises and never fails the agent;
- the module sources contain no `os.remove`, `os.unlink`, `os.rmdir`,
  `shutil.rmtree`, `os.rename` or `subprocess` — checked by a test that reads
  its own source, so the absence is enforced rather than hoped for.

### Phase 3C — audit & activity — COMPLETE

The activity feed is the audit trail of every remote command. It turned out
the store was already there from Phase 2/3B — `CommandLog` carries
`id` (= `requestId`), `deviceId`, `command`, `target`, `status`, `result`,
`createdAt`, `updatedAt`, `expiresAt`, and the lifecycle (create → dispatch →
settle / expire) wrote all four states already. So Phase 3C is an integration
and a correctness pass, not a new audit system: no second table, no new enum,
no new authorization layer, no migration.

What actually changed:

- **Ordering** (`backend/src/modules/commands/routes.ts`): the history
  endpoint returned oldest-first; an activity feed reads from the top, so it
  now returns **newest-first**. One line — the `.reverse()` that undid the
  `orderBy: desc` is gone.
- **Activity screen** (`index.html`, `app.js`): the screen was a static
  mockup (Owner login · Face ID, Chrome opened, File downloaded …). It is now
  wired to `GET /api/devices/:id/commands` through the existing
  `Vigilia.commandHistory()` client and renders real audit rows only. No row
  is invented client-side; a backend that cannot be reached leaves the list
  empty and logs the reason.
- **Filters** (`index.html`): the mock categories `System/Security/AI` are
  `All/Success/Failed/Pending`, which map onto `CommandStatus`
  (`succeeded` → Success, `failed`/`expired` → Failed, `queued`/`sent` →
  Pending). The filter toggle infrastructure was already there and is reused.
- **Live refresh**: a `command.result` frame that settles a pending command
  re-fetches the feed if the Activity screen is open, so the row flips to its
  final status while the user is looking at it.

**Final verification (node v22, host: anyeong-Vivobook-Go-E1404FA):**

```
npm test                                    # 98 tests, all pass
npm run typecheck --workspaces              # clean
npm run lint --workspaces                   # clean
npm run build --workspaces                  # clean
npm run agent:linux:test                    # 88 tests, OK
npm run agent:windows:test                  # 53 tests, OK (3 host-native skips)
npm run e2e:activity                        # 5/5 checks
```

Verified against the real Linux agent with `gnome-calculator`, going through
the mobile API end to end:

```
mobile → backend → agent → application → command.result → audit → activity
APPLICATION_OPEN      gnome-calculator   succeeded  gnome-calculator launched
APPLICATION_RESTART   gnome-calculator   succeeded  gnome-calculator restarted
APPLICATION_CLOSE     gnome-calculator   succeeded  gnome-calculator: 1 process(es) closed
APPLICATION_CLOSE     gnome-calculator   succeeded  gnome-calculator is not running
LOCK                  (no session)       failed     no active login session to lock
```

The last two rows are the point: a close on an already-stopped application
reports the honest state, and a command the platform cannot perform shows up
as `failed` with the platform's own reason. Both were confirmed in the UI: a
headless Chromium load of the Activity screen rendered 21 real rows
newest-first, `gnome-calculator → CLOSE` as Success and `LOCK` as Failed, and
the Failed filter hid every non-failed row. No static mockup row remained.

The audit binding also has tests rather than assertions: `audit.test.ts`
covers the created row, the `succeeded`/`failed`/`expired` settlements, the
`requestId` spanning the REST response, the dispatch frame and the result, the
newest-first ordering and the `limit`, an agent being unable to settle another
device's command, a foreign command token reading another device's audit
(401), no secret appearing in the response, and the read-only mobile socket
being answered `FORBIDDEN` when it tries to send anything.

### Phase 3B — application control — COMPLETE

Limited, allowlisted control of applications from the mobile client: `OPEN`,
`CLOSE` and `RESTART` — three new entries in the Phase 2 command surface,
not a new command system. No `/exec`, no `/shell`, no arbitrary argv.

**Final verification (node v22, host: anyeong-Vivobook-Go-E1404FA):**

```
npm test                                    # 87 tests, all pass
npm run typecheck --workspaces              # clean
npm run lint --workspaces                   # clean
npm run build --workspaces                  # clean
npm run agent:linux:test                    # 88 tests, OK
npm run agent:windows:test                  # 53 tests, OK (3 host-native skips)
```

Backend suite = 9 files (the 18 Phase 3B tests live in
`application-commands.test.ts` + `applications.test.ts`). Verified against
the source: the catalog broadcast (`applications.latest`), the on-connect
replay, the `NOT_ALLOWED` path for an unregistered target, the
`FORBIDDEN` answer to a read-only mobile socket that tries to dispatch, and
the `queued → sent → succeeded | expired` audit lifecycle are all covered
by named tests. `docs/API.md` was re-checked against the implementation and
is accurate — no duplication, balanced code fences, no ragged tables.

- **Contract** (`shared/src/types.ts`, `shared/src/schemas.ts`):
  `APPLICATION_OPEN | APPLICATION_CLOSE | APPLICATION_RESTART` extend
  `DEVICE_COMMANDS`. Every one of them takes a `target` that must be an
  `applicationId` — a lowercase identifier with the charset `[a-z0-9._-]`, so
  a path or a shell metacharacter cannot pass the schema at all. `RESTART`
  (the OS) and `APPLICATION_RESTART` (one application) are distinct commands
  and stay that way. Python mirror in `agents/common/telemetry.py`
  (`CommandDispatch.is_valid` — an application command without a target is an
  invalid frame before any execution).
- **Application identification** (`agents/common/applications.py`): the agent
  holds a registry of the applications this machine will open or close.
  `applicationId` is the process `name` the Phase 3A collector reports, so a
  row in the process list binds to a controllable application without a
  second namespace. Discovery probes with `shutil.which` only — nothing is
  launched to test it. A mobile client never chooses the executable: it picks
  from the catalog the device pushed, and an id the registry does not know is
  refused before anything runs.
- **Linux control** (`agents/linux/src/applications.py`): OPEN launches the
  fixed argv from the registry (no shell, `start_new_session` so the child
  outlives the agent). CLOSE matches the process by **name** — never by pid,
  because pids recycle — and sends SIGTERM, then polls until the process is
  gone. SIGTERM, not SIGKILL: an application may save its state, and a
  process that ignores the signal is reported as still running rather than
  silently escalated to something destructive. RESTART is close-then-open
  through the same two paths.
- **Windows control** (`agents/windows/src/applications.py`): the same three
  commands through `ShellExecuteExW` (open) and `OpenProcess` +
  `TerminateProcess` (close). No PowerShell, no `cmd.exe`, no `tasklist`, no
  WMI pipe. Off-Windows the default path reports `failed` with the reason;
  only an injected test double can make it "run" on a non-Windows host.
- **Backend** (`backend/src/modules/applications/`, `…/commands/`): the agent
  pushes an `applications` catalog, stored as one upserted `ApplicationCatalog`
  row per device (current state, not history) and replayed to a mobile socket
  on connect as `applications.latest`. A dispatch re-checks the target against
  that catalog: an id the device never registered is `NOT_ALLOWED` before an
  audit row exists and before any frame reaches the agent. The `target` is
  stored on the `CommandLog` row, so the audit trail says *what* was acted on.
- **What it does not do**: there is still no endpoint that accepts a path, a
  command line or a process list. The mobile request carries an `applicationId`
  and the device decides whether that means anything.

Verified against the real Linux agent on the Vivobook with
`gnome-calculator` as the safe target (installed, harmless, not the browser
or anything the operator is working in):

```
mobile → backend → agent → application → command.result → audit → mobile
APPLICATION_OPEN      gnome-calculator   succeeded  gnome-calculator launched
APPLICATION_RESTART   gnome-calculator   succeeded  gnome-calculator restarted
APPLICATION_CLOSE     gnome-calculator   succeeded  gnome-calculator: 1 process(es) closed
APPLICATION_CLOSE     gnome-calculator   succeeded  gnome-calculator is not running
```

The last row is the point: a second close reports the honest state rather
than faking a second success. `/bin/sh` is refused by the schema; `sh` and
`vim` (installed but not registered) are refused with `NOT_ALLOWED` — the
device's own registry is the limit, not the phone's imagination. A read-only
mobile socket that tries to dispatch gets `FORBIDDEN | read-only connection`.

The UI check (`npm run e2e:applications`, headless Chromium) then proved the
dashboard half of the contract: the Control screen lists the eight real
applications the device reported, an Open dispatches an audited
`APPLICATION_OPEN`, and the row's pill flips to **Running only after** the
agent's `command.result` lands — never on the REST 202 alone.

```
npm test                      # 87 tests (18 new: catalog, application commands)
npm run agent:linux:test      # 88 tests (18 new: registry, control, zombie handling)
npm run agent:windows:test    # 53 tests (15 new: contract, native refusal, honesty)
```

Two real behaviours found while testing, both fixed:

- **Zombies.** A `Popen` child that exits stays as a zombie until the agent
  reaps it, and a zombie still carries the matching process name — so a
  second CLOSE would see a "running" process that was already dead. Matching
  now excludes `status: zombie` on both platforms, and the backend's
  `running` annotation excludes it too.
- **`comm` truncation.** Kernel `comm` is 15 chars, so `gnome-calculator`
  reads as `gnome-calculato` in `ps`. Matching uses the full name psutil
  reports, not `ps`, so the two never disagree.

### Phase 3A — application & process telemetry

Read-only process telemetry: an agent reports what is running, the backend
stores the latest snapshot, and a mobile client sees it live.

- **Contract** (`shared/src/types.ts`, `shared/src/schemas.ts`):
  `ProcessInfo` (pid, name, cpu, optional memoryMb/memoryPercent, threads,
  parentPid, normalised `status`, executablePath, username, startedAt, and a
  commandLine that is off by default) and `ProcessSnapshotPayload`
  (processes ordered by resource use, `totalCount`, `limit`, `timestamp`).
  New frame types `processes` (agent → server) and `processes.latest`
  (server → mobile). `status` is a fixed enum so Linux `R`/`S`/`D`/`Z`,
  psutil spellings and Windows state names all collapse onto the same
  vocabulary; `PROCESS_HARD_CAP = 512` rejects (does not truncate) an
  oversized frame. Python mirror in `agents/common/processes.py`.
- **Linux collector** (`agents/linux/src/collectors.py`): psutil when
  importable, otherwise a pure-stdlib `/proc` walk — `stat` for the name,
  state, parent, start time and CPU jiffies, `status` for VmRSS and the
  owner uid, `exe`/`cmdline` symlinks. CPU% is instantaneous against the
  previous sample per pid, so the first sighting of a pid honestly reports
  `0.0` rather than a fabricated number. A process that dies mid-enumeration
  is skipped, not fatal.
- **Windows collector** (`agents/windows/src/collectors.py`): psutil, then
  native `EnumProcesses` + `GetProcessMemoryInfo` + `GetProcessTimes` +
  `QueryFullProcessImageNameW` + `NtQueryInformationProcess` through ctypes.
  No WMI pipe, no PowerShell, no `tasklist` subprocess. Off-Windows the
  native path returns an empty list because there genuinely is no list to
  report — nothing is faked.
- **Backend** (`backend/src/modules/processes/`): one upserted
  `ProcessSnapshot` row per device, replaced wholesale on every report. A
  process list is the current state, not a time series, so there is no
  history table and no accumulating rows. `GET /api/processes/:deviceId`
  returns it with a `fresh` flag. On a mobile connect the last snapshot is
  replayed as `processes.latest` so the view renders before the agent's next
  push. The `deviceId` is rebound to the authenticated connection, so an
  agent cannot publish a process list for another machine.
- **What it does not do**: there is no kill/terminate verb anywhere in this
  phase. Process *control* will come through the Phase 2 command surface as
  three new allowlist entries — `APPLICATION_OPEN`, `APPLICATION_CLOSE`,
  `APPLICATION_RESTART` — added in Phase 3B. No `/exec` or `/shell` endpoint
  exists or is planned.

Verified against the real Linux agent on the Vivobook: it pushed a 20-entry
snapshot every 3 s, `GET /api/processes/:id` returned pid/name/cpu/memory/
threads/parent/executable/owner/start-time, and a mobile socket received the
`processes.latest` frame live plus a replay on reconnect.

```
npm test                      # 69 tests (14 new)
npm run agent:linux:test      # 70 tests (16 new: contract + /proc fallback)
npm run agent:windows:test    # 38 tests (14 new: native refusal, helpers)
```

### Wake-on-WLAN investigation (ASUS Vivobook Go E1404FA)

- **Suspend → Wake**: works. The machine resumes on a WLAN magic packet.
- **Shutdown → Wake**: does not work on this model. A powered-down state is
  not resumable from the network interface, so remote shutdown makes the
  device unreachable until it is powered on locally.

This is recorded rather than worked around: no further shutdown → wake
experiments are planned, and no hardware or dongle is being added. The
`SHUTDOWN` command remains in the allowlist — it is still correct behaviour
for a machine that is physically reachable, and `SLEEP`/`HIBERNATE` are the
remote options that preserve wake-on-WLAN.

### Phase 2 — remote control foundation

A safe, allowlisted command channel: mobile → backend → agent → result → mobile.

- **Contract** (`shared/src/types.ts`, `shared/src/schemas.ts`): five commands
  `LOCK | SLEEP | HIBERNATE | RESTART | SHUTDOWN`, zod enums so anything else
  is rejected at validation. Python mirror in
  `agents/common/telemetry.py` (`CommandDispatch`, `CommandResult`,
  `COMMAND_ALLOWLIST`).
- **Pairing** (`POST /api/devices/:id/pair`, agent-token authenticated): issues
  one `vc_` command token, stored as `sha256(deviceId:token:serverSecret)`.
  The agent token is never exposed to the mobile client, and the two
  credentials are prefix-separated so neither can be presented as the other.
- **Dispatch** (`POST /api/devices/:id/commands`, command-token authenticated):
  creates an audited `CommandLog` row, then sends a `command.dispatch` frame
  to the device's read/write agent socket only. A device with no live agent
  keeps the row `queued`; a background sweep marks it `expired` after 60 s.
- **Result**: the agent replies `command.result`; the backend rebinds the
  `requestId` to the authenticated connection (an agent cannot close another
  device's command), updates the row and broadcasts the outcome to the
  device's subscribed mobile sockets.
- **Linux execution** (`agents/linux/src/commands.py`): fixed argv per command,
  no shell anywhere. `LOCK` targets each active seat session via `loginctl`,
  power commands use `systemctl`. 15 s timeout, failures always reported.
- **Windows execution** (`agents/windows/src/commands.py`): ctypes into
  `user32`/`powrprof`/`advapi32` — `LockWorkStation`, `SetSuspendState`,
  `InitiateSystemShutdownExW`. No PowerShell, no subprocess. On a non-Windows
  host the command reports `failed` honestly rather than being faked.

Verified against the real Linux agent: pairing → dispatch → the agent executed
`loginctl lock-session` → the audit row shows the platform's own reason
(`no active login session to lock` on this headless host).

```
npm test                      # 55 tests (11 new: pairing, dispatch, expiry, spoofing)
npm run agent:linux:test      # 54 tests (12 new: allowlist, fixed argv, failures)
npm run agent:windows:test    # 24 tests (5 new: dispatch, refusal, Win32 error surfacing)
```

### End-to-end: Linux agent → backend → mobile WebSocket telemetry
Verified with the real Linux agent streaming to a live backend and a
headless Chromium dashboard:

- the dashboard's own socket is open and subscribed to the right device
- two telemetry samples a few seconds apart move the DOM (live, not a REST
  snapshot)
- the backend's latest telemetry for that device is under 30 s old
- a client-side disconnect reconnects on its own and rejoins

```
npm run e2e:ui        # 11 assertions: REST + WS against a registered device
npm run e2e:realtime  #  6 assertions: agent → backend → mobile WS → live DOM
```

### Backend (`backend/`)
Fastify + Prisma (SQLite) + WebSocket hub. REST module map:

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/health`, `/live`, `/ready`, `/version` | liveness, DB readiness, build info |
| POST | `/api/auth/verify` | agent token check |
| POST | `/api/auth/session` | mobile session bootstrap (no tokens) |
| GET | `/api/auth/me` | bearer self-lookup |
| POST | `/api/devices/register` | device registration + token issuance |
| POST | `/api/devices/:id/pair` | issue a mobile command token (agent auth) |
| GET | `/api/devices`, `/api/devices/:id` | public device views |
| PATCH | `/api/devices/:id` | agent metadata update (token) |
| GET | `/api/monitoring/:id/latest`, `/history` | telemetry now + series |
| POST | `/api/monitoring/:id/telemetry` | HTTP telemetry ingest fallback |
| POST | `/api/devices/:id/commands` | dispatch a remote command (paired token) |
| GET | `/api/devices/:id/commands` | command audit history (paired token) |
| WS | `/ws` | realtime telemetry + command channel |

Security model: agents get one opaque token at registration; the DB stores
`sha256(deviceId:token:serverSecret)`. Mobile clients authenticate with
`?deviceId=` only and are read-only.

Tests: `npm run test --workspace backend` (49 tests) — auth, health,
registration, telemetry ingest/validation, WebSocket auth and telemetry.
`npm run typecheck --workspaces` clean.

### Agents (`agents/`)
Shared stdlib-only core in `agents/common/` (config, logger, telemetry
contract, token store, RFC 6455 WebSocket client, platform interface).

- **Linux** (`agents/linux/main.py`) — psutil when available, `/proc` +
  `/sys` fallback. 42 tests pass:
  `npm run agent:linux:test`
- **Windows** (`agents/windows/main.py`) — ctypes into `kernel32`/`psapi`
  (`GlobalMemoryStatusEx`, `GetSystemTimes`, `GetDiskFreeSpaceExW`,
  `GetSystemPowerStatus`, `GetTickCount64`) with a stdlib/psutil fallback.
  Importable and testable on Linux; native paths skip where Win32 is absent.
  19 tests pass (3 host-native skips on Linux):
  `npm run agent:windows:test`

### Mobile UI → real backend
The static mockup at the repo root (`index.html`, `styles.css`, `app.js`) is
served verbatim by the backend and now wired to live data by
`vigilia.js`:

- device identity, OS and status from `/api/devices`
- the eight home stat cards from `/api/monitoring/:id/latest` and the `/ws`
  stream (CPU, RAM, GPU, temp, storage, battery, network, uptime)
- sparklines from `/api/monitoring/:id/history?limit=30`
- a metric the agent does not report shows `—` and `No data` rather than a
  leftover mockup number
- read-only mobile socket with a 25 s ping and automatic reconnect

End-to-end checks (headless Chromium over CDP):

```
npm run e2e:ui          # REST + WS assertions against a registered device
npm run e2e:realtime    # agent WS → backend → mobile WS → DOM moves live
```

### Documentation
- `README.md` — overview, layout, quickstart, command list
- `docs/ARCHITECTURE.md` — components, data flow, trust boundaries, security
  model, and the explicit list of what the agent will never do
- `docs/AGENTS.md` — install/configure/run the Ubuntu and Windows agents,
  systemd unit, troubleshooting
- `docs/API.md` — every REST route, every WebSocket frame, response shapes

## Test totals

| Suite | Command | Result |
| --- | --- | --- |
| Linux agent | `npm run agent:linux:test` | 139 tests, all pass |
| Windows agent | `npm run agent:windows:test` | 77 tests (3 host-native skips off-Windows) |
| agent contract | `python3 -m unittest discover -s agents/common` | 155 tests |
| mobile UI e2e | `npm run e2e:ui` | 10 assertions |
| realtime e2e | `npm run e2e:realtime` | 6 assertions |
| application control e2e | `npm run e2e:applications` | 7 assertions, 4 real dispatches |
| activity e2e | `npm run e2e:activity` | 5 checks, live audit rows + filters |
| storage screen e2e | `npm run e2e:storage` | 12 assertions |
| cleaner screen e2e | `npm run e2e:cleaner` | 21 assertions |
| security e2e | `npm run e2e:security` | 32 assertions, live agent + real browser |
| phase5 e2e | `npm run e2e:phase5` | 53 assertions, the security screen lifecycle + Daily Auto Cleanup |
| phase5 render check | `node mobile/ui-check-phase5.mjs` | 15 assertions |

The backend's own suite is `backend/tests/security.test.ts` (26 tests). It
runs against a scratch SQLite database with the current schema, so the
once-per-day rule, the severity derivation and the token scoping are checked
against real rows, and the routes are exercised through a real Fastify app
with the paired command token.

`npm run typecheck --workspaces` is clean. `npm run lint --workspaces` is
clean (both workspaces migrated from `.eslintrc.cjs` to ESLint 9 flat config).
`npm run build` compiles backend + shared.

## Layout notes

- The SQLite file is `backend/prisma/dev.db`; `.env` sets
  `DATABASE_URL="file:backend/prisma/dev.db"`. The path is resolved against
  the repo root in `backend/src/config/env.ts` and passed explicitly to
  `PrismaClient` in `backend/src/config/db.ts`, because npm workspaces run
  the backend from `backend/` while Prisma resolves a bare relative path
  against the generated client's own directory.
  **The Prisma CLI resolves the same relative path against the schema's
  directory, not the repo root.** So `npx prisma migrate` run from the repo
  root creates `backend/prisma/backend/prisma/dev.db` — a second, shadow
  database that the app never sees. Migrate with an absolute
  `DATABASE_URL` (`DATABASE_URL="file:$PWD/backend/prisma/dev.db"`) to hit
  the file the app actually uses.
- `@fastify/static` enumerates the served directory at boot, so a file added
  to the repo root is not visible until the backend restarts.
- ESLint config is `backend/eslint.config.js` and `shared/eslint.config.js`
  (ESLint 9 flat). Both use the plugin's own `flat/recommended` directly
  because the `typescript-eslint` wrapper is not a dependency; the base
  `no-unused-vars` / `no-redeclare` are turned off for `.ts` since the
  `@typescript-eslint` equivalents handle TS overloads and parameter
  properties correctly.
- **The backend build and typecheck use two tsconfigs.** `backend/tsconfig.json`
  type-checks `src` **and** `tests` with `noEmit` and no `rootDir` constraint
  (the test suite lives outside `src` by design). `backend/tsconfig.build.json`
  extends it, sets `rootDir: ./src`, drops `tests` and emits. Previously a
  single config with `rootDir: ".."` covered both, which emitted the whole
  workspace under `dist/backend/src/…` and left `npm start` pointing at a
  stale flat `dist/main.js`. The `@vigilia/shared` path alias now resolves to
  `shared/dist/index.d.ts`, which is also what Node resolves at runtime via
  the workspace symlink — so typecheck and runtime agree on one package.
- **A build that leaves `.js` files behind in `tests/` is a sign the two
  configs got crossed** (an emit run that still included `tests`). Delete
  them and rebuild with `tsconfig.build.json`; ESLint then lints `.js` files
  it has no globals for, which is how it surfaces.
- `Node16` module resolution requires the exact directory depth in relative
  imports: files under `backend/src/modules/*/` import `../../config/db.js`
  (two levels), unlike `modules/*/routes.ts` neighbours that sit one level
  shallower. A wrong depth fails to compile, not at runtime.

## Known gaps

- `agents/windows/tests/test_main.py::test_telemetry_and_heartbeat_reach_the_backend`
  is timing-sensitive on a loaded host: it passes alone and in most full
  `npm run agent:windows:test` runs, but fails with `StopIteration` when the
  discover run is starved (the heartbeat lands after its 15 s window). It is
  a timing window, not a contract break.
- Prisma CLI build-time advisory GHSA-ggr8-5vv4-36mx (`deepmerge-ts` < 8.0.0 via `@prisma/config`):
  documented as build-only limitation; upstream Prisma patches are monitored.
- The agents do not collect GPU or temperature, so those two cards show
  `No data`.
- `GET /api/health/version` reports `config.nodeEnv` as `version` — it should
  read the package version.
- The mobile UI has no device picker surfaced from `/api/devices`; a device is
  selected via `?device=<id>` or the first online device.
- Command results are broadcast to every mobile socket subscribed to the
  device; there is no per-session scoping yet.
- `e2e:security` and `e2e:phase5` need the Linux agent running and the device
  paired (`VIGILIA_COMMAND_TOKEN`). The first dispatches a real
  `APPLICATION_OPEN` and `APPLICATION_CLOSE` to a real application; the second
  toggles the Daily Auto Cleanup config through the paired token and restores
  it. Neither mocks a side of the wire.
  *Note: Phase 11E/11G introduces `npm run e2e:privileged` which runs all 5 privileged E2E suites automatically against an isolated disposable environment.*

## Phase 11 — Production Deployment, Security & Hardening (Completed)

- **Phase 11A**: Agent/Backend service profiles & systemd units.
- **Phase 11B**: Local AI deployment (Qwen2.5-3B-Instruct GGUF + llama-server).
- **Phase 11C**: Transactional database migration & preflight recovery.
- **Phase 11D**: Protocol version 1 negotiation, `agent.hello` handshake, health metadata.
- **Phase 11E**: Privileged E2E runner (`npm run e2e:privileged`) & restart recovery (`npm run e2e:recovery`).
- **Phase 11F**: Resource hardening: automatic Guardian scheduler/coalescing, model-unavailable backoff, device-indexed WebSocket hub, bounded database retention (90 days).
- **Phase 11G**: Production installer (`deploy/install-linux.sh`), uninstaller (`deploy/uninstall-linux.sh`), Windows Task Scheduler XML, operational guides.
- **Phase 11H**: Final acceptance matrix & Go for Release verification across all suites.
- **Phase 11I**: Release documentation (`docs/DEPLOYMENT.md`, `README.md`).
