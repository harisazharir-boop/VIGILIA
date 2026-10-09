/**
 * VIGILIA — shared domain types
 * Single source of truth for backend, agents and mobile clients.
 */

/** Operating systems supported by VIGILIA agents. */
export type DeviceOs = 'ubuntu' | 'windows' | 'linux' | 'unknown';

export const DEVICE_STATUSES = ['online', 'offline', 'standby'] as const;
export type DeviceStatus = (typeof DEVICE_STATUSES)[number];

/** Identifying information an agent reports on first boot. */
export interface SystemInfo {
  os: string;
  hostname: string;
  osVersion?: string;
  kernelVersion?: string;
  cpuModel?: string;
  cpuCores?: number;
  totalMemoryMb?: number;
  totalStorageGb?: number;
  agentVersion: string;
  platform: string;
}

/** Registration request body — POST /devices/register */
export interface RegisterDeviceRequest {
  name: string;
  os: DeviceOs;
  hostname: string;
  agentVersion?: string;
  protocolVersion?: number;
  systemInfo?: Partial<SystemInfo>;
}

/** Registration response. */
export interface RegisterDeviceResponse {
  deviceId: string;
  token: string;
  /** Epoch seconds. */
  expiresAt: number;
}

/** Heartbeat pushed by an agent over WebSocket. */
export interface HeartbeatPayload {
  deviceId: string;
  status: DeviceStatus;
  timestamp: string;
}

/** Telemetry sample pushed by an agent over WebSocket. */
export interface TelemetryPayload {
  deviceId: string;
  cpu?: number;
  ram?: number;
  storage?: number;
  battery?: number;
  os?: string;
  hostname?: string;
  uptime?: number;
  gpu?: number;
  temp?: number;
  /** Down/up link state, if known. */
  network?: NetworkStatus;
  hardware?: HardwareTelemetry;
  networkTelemetry?: NetworkTelemetry;
  timestamp: string;
}

export interface NetworkStatus {
  connected: boolean;
  ssid?: string;
  /** Downstream rate in Mbps, if known. */
  downloadMbps?: number;
  /** Upstream rate in Mbps, if known. */
  uploadMbps?: number;
  interface?: string;
}

export interface HardwareCapabilities {
  gpu: boolean; gpuVram: boolean; cpuTemperature: boolean; gpuTemperature: boolean;
  fanSpeed: boolean; battery: boolean; charging: boolean;
}
export interface HardwareTelemetry {
  capabilities: HardwareCapabilities;
  cpuLoad1?: number; ramAvailableMb?: number; gpuUsage?: number; gpuVramUsedMb?: number;
  gpuVramTotalMb?: number; cpuTemperatureC?: number; gpuTemperatureC?: number;
  fanRpm?: number; batteryStatus?: 'charging' | 'discharging' | 'full' | 'unknown';
}
export interface NetworkCapabilities { gateway: boolean; dns: boolean; wifiSignal: boolean; latency: boolean; packetLoss: boolean; throughput: boolean; }
export interface NetworkTelemetry {
  capabilities: NetworkCapabilities; connected: boolean; interface?: string;
  interfaceType?: 'wifi' | 'ethernet' | 'cellular' | 'loopback' | 'unknown'; ipAddress?: string;
  gateway?: string; dns?: string[]; wifiSignalPercent?: number; latencyMs?: number;
  packetLossPercent?: number; downloadMbps?: number; uploadMbps?: number;
}
export const HARDWARE_CONTROLS = ['VOLUME', 'BRIGHTNESS', 'WIFI', 'BLUETOOTH', 'MICROPHONE', 'CAMERA', 'MEDIA_PLAYBACK'] as const;
export type HardwareControl = (typeof HARDWARE_CONTROLS)[number];
export interface HardwareControlResult { control: HardwareControl; status: 'SUCCEEDED' | 'UNSUPPORTED' | 'FAILED'; value?: number | boolean | 'play' | 'pause'; reason?: string; }
export const AI_RISK_LEVELS = ['NORMAL', 'WARNING', 'HIGH', 'CRITICAL'] as const;
export type AiRiskLevel = (typeof AI_RISK_LEVELS)[number];
export const AI_TOOLS = ['INSPECT_HEALTH', 'INSPECT_SECURITY', 'INSPECT_NETWORK', 'RECOMMEND_CLEANUP'] as const;
export type AiTool = (typeof AI_TOOLS)[number];
export interface AiAlert { id: string; deviceId: string; severity: AiRiskLevel; category: 'health' | 'security' | 'network'; message: string; createdAt: string; }
export interface AiResponse { requestId: string; risk: AiRiskLevel; message: string; tools: { tool: AiTool; status: 'SUCCEEDED' | 'UNSUPPORTED'; reason?: string }[]; alerts: AiAlert[]; }
export interface AiActionPreview { requestId: string; action: 'LOCK' | 'SLEEP' | 'HIBERNATE' | 'RESTART' | 'SHUTDOWN' | 'UNSUPPORTED'; expiresAt: string; confirmationRequired: boolean; reason: string; }
export interface LocalModelStatus { status: 'READY' | 'UNAVAILABLE'; model: string; runtime: string; }

/** Public device view — safe for mobile clients. Never contains tokens. */
export interface DevicePublic {
  id: string;
  name: string;
  os: string;
  hostname: string;
  status: DeviceStatus;
  lastSeen: string;
  agentVersion: string;
  createdAt: string;
  updatedAt: string;
  /** Latest telemetry sample, if any. */
  telemetry?: TelemetryPayload | null;
}

/** Envelope for every WebSocket frame on /ws. */
export const WS_MESSAGE_TYPES = [
  'heartbeat',
  'telemetry',
  'processes',
  'device.status',
  'device.registered',
  'telemetry.latest',
  'processes.latest',
  'applications',
  'applications.latest',
  'storage',
  'storage.latest',
  'cleanup.actions',
  'cleanup.latest',
  'command.dispatch',
  'command.result',
  'clean.result',
  'security.state',
  'security.event',
  'security.latest',
  'error',
] as const;
export type WsMessageType = (typeof WS_MESSAGE_TYPES)[number];

export interface WsMessage<T = unknown> {
  type: WsMessageType;
  deviceId?: string;
  data?: T;
  /** ISO timestamp the server accepted the frame. */
  timestamp: string;
}

/**
 * Commands an agent is allowed to execute.
 *
 * This list is the entire remote-control surface. It is an allowlist, not a
 * hint: anything not listed here is rejected at validation before it reaches
 * a socket. The backend never synthesises argv from a string and the agent
 * never passes a command line to a shell — each command resolves to a fixed
 * platform-specific action.
 */
/**
 * Application & process telemetry — Phase 3A.
 *
 * A *snapshot* of the running processes on a device, reported by the agent
 * and streamed to mobile clients. The shape is deliberately narrow:
 *
 *  - `pid`, `name` and `cpu` are always present. Everything else is optional
 *    and absent when the platform cannot read it (e.g. the executable path
 *    of a process owned by another user).
 *  - `status` is a *normalised* enum, because `R`/`S`/`D` (Linux) and
 *    `Running`/`Suspended` (Windows) describe the same states in different
 *    alphabets. A platform that cannot determine it omits the field rather
 *    than inventing a value.
 *  - `memoryMb`/`memoryPercent` are alternatives, not complements: Windows
 *    reports RSS reliably, Linux reports a percentage of physical memory.
 *    A consumer should prefer whichever is set.
 *
 * This contract is read-only telemetry. It does not carry any control
 * capability — there is no kill/terminate verb here. Process *control* is a
 * separate allowlist (`APPLICATION_OPEN` / `APPLICATION_CLOSE` /
 * `APPLICATION_RESTART`) that Phase 3B adds on top of the command surface
 * Phase 2 already established.
 */
export const PROCESS_STATUSES = [
  'running',
  'sleeping',
  'stopped',
  'zombie',
  'idle',
  'unknown',
] as const;
export type ProcessStatus = (typeof PROCESS_STATUSES)[number];

/**
 * Absolute ceiling the schema accepts, independent of the configurable
 * `VIGILIA_PROCESS_LIMIT`. A frame above this is rejected rather than
 * truncated, so a misbehaving or compromised agent cannot push a megabyte
 * of process rows through a validated frame.
 */
export const PROCESS_HARD_CAP = 512;

/** One process, as seen by an agent. */
export interface ProcessInfo {
  /** OS process id. `-1` marks an entry whose pid could not be read. */
  pid: number;
  /** Process or application name. Never empty; an unreadable name is `unknown`. */
  name: string;
  /** CPU utilisation, percentage of one core in [0, 100]. */
  cpu: number;
  /** Resident memory in MiB, when the platform reports RSS. */
  memoryMb?: number;
  /** Share of physical memory in [0, 100], when the platform reports a ratio. */
  memoryPercent?: number;
  /** Thread count, when known. */
  threads?: number;
  /** Parent process id, when known. */
  parentPid?: number;
  /** Normalised execution state. Absent when the platform cannot tell. */
  status?: ProcessStatus;
  /** Absolute path to the executable, only when the agent may read it. */
  executablePath?: string;
  /** Owner of the process, when known. */
  username?: string;
  /** When the process started, ISO-8601 UTC. */
  startedAt?: string;
  /**
   * Command line, only when the agent may read it AND `VIGILIA_PROCESS_ARGS`
   * is enabled. Off by default: a command line can carry secrets, and the
   * dashboard has no use for it in Phase 3A.
   */
  commandLine?: string;
}

/**
 * The process-list payload an agent pushes.
 *
 * Processes are ordered by resource use, most expensive first, and the list
 * is capped (default 64) so a busy machine cannot flood a mobile socket.
 */
export interface ProcessSnapshotPayload {
  deviceId: string;
  processes: ProcessInfo[];
  /** How many processes were enumerated before the cap applied. */
  totalCount: number;
  /** Cap the agent applied (`VIGILIA_PROCESS_LIMIT`, default 64). */
  limit: number;
  timestamp: string;
}

/** Public snapshot view — safe for mobile clients. */
export interface ProcessSnapshotPublic {
  deviceId: string;
  processes: ProcessInfo[];
  totalCount: number;
  limit: number;
  timestamp: string;
}

/**
 * OS-level power commands. `RESTART` here restarts the *operating system*;
 * restarting a single application is `APPLICATION_RESTART` — the two are
 * different commands on purpose and never interchangeable.
 */
export const OS_COMMANDS = ['LOCK', 'SLEEP', 'HIBERNATE', 'RESTART', 'SHUTDOWN'] as const;

/** Application-level commands added in Phase 3B. */
export const APPLICATION_COMMANDS = [
  'APPLICATION_OPEN',
  'APPLICATION_CLOSE',
  'APPLICATION_RESTART',
] as const;

export const DEVICE_COMMANDS = [...OS_COMMANDS, ...APPLICATION_COMMANDS] as const;
export type DeviceCommand = (typeof DEVICE_COMMANDS)[number];
export type OsCommand = (typeof OS_COMMANDS)[number];
export type ApplicationCommand = (typeof APPLICATION_COMMANDS)[number];


export const APPLICATION_COMMAND_NAMES = APPLICATION_COMMANDS;

/**
 * Storage cleanup actions (Phase 4B).
 *
 * This is the *cleanup* allowlist, and it is deliberately not part of
 * `DEVICE_COMMANDS`: a cleanup action is not a command the agent executes
 * blindly. It names a **category of pre-approved, well-known locations** —
 * never a path, never a glob, never a file. The agent resolves the id to its
 * own fixed roots on the device it runs on; the mobile client has no say in
 * what "CLEAN_TEMP" means, and there is no field anywhere in the protocol
 * that could carry an arbitrary path.
 *
 * Only categories the Phase 4A analyser already measures exist here, and only
 * the ones that are genuinely safe to empty. `downloads` is deliberately
 * absent: deleting a user's downloads is not a safe cleanup, so there is no
 * action id for it and it cannot be requested.
 *
 * Phase 5 adds two more safe categories: `CLEAN_THUMBNAIL_CACHE` (the file
 * manager's thumbnail cache, regenerable) and `CLEAN_OLD_LOGS` (log files
 * older than an explicit age, never a whole log tree). Both follow the same
 * rule: the agent resolves the id to fixed roots and an explicit age, and the
 * client cannot name a path or an age.
 */
export const CLEAN_ACTIONS = [
  'CLEAN_TEMP',
  'CLEAN_BROWSER_CACHE',
  'CLEAN_TRASH',
  'CLEAN_PACKAGE_CACHE',
  'CLEAN_USER_CACHE',
  'CLEAN_THUMBNAIL_CACHE',
  'CLEAN_OLD_LOGS',
] as const;
export type CleanAction = (typeof CLEAN_ACTIONS)[number];

/** Platforms a cleanup action is supported on. */
export const CLEAN_PLATFORMS = ['linux', 'windows'] as const;
export type CleanPlatform = (typeof CLEAN_PLATFORMS)[number];

export const CLEAN_REASON_MAX_LENGTH = 280;

/**
 * The storage category (Phase 4A) each action cleans. Fixed, so an action id
 * can only ever be priced from a category the analyser measures — an action
 * the device never reported a category for has no estimate and no
 * recommendation.
 */
export const CLEAN_ACTION_CATEGORIES: Record<CleanAction, StorageCategoryId> = {
  CLEAN_TEMP: 'tmp',
  CLEAN_BROWSER_CACHE: 'browser-cache',
  CLEAN_TRASH: 'trash',
  CLEAN_PACKAGE_CACHE: 'package-cache',
  CLEAN_USER_CACHE: 'user-cache',
  CLEAN_THUMBNAIL_CACHE: 'thumbnail-cache',
  CLEAN_OLD_LOGS: 'old-logs',
};

/** Dashboard labels, one per action id. */
export const CLEAN_ACTION_LABELS: Record<CleanAction, string> = {
  CLEAN_TEMP: 'Temporary Files',
  CLEAN_BROWSER_CACHE: 'Browser Cache',
  CLEAN_TRASH: 'Trash',
  CLEAN_PACKAGE_CACHE: 'Package Cache',
  CLEAN_USER_CACHE: 'User Cache',
  CLEAN_THUMBNAIL_CACHE: 'Thumbnail Cache',
  CLEAN_OLD_LOGS: 'Old Logs',
};

/**
 * Plain-language description of what each action removes. This is what the
 * mobile confirmation sheet shows, so the operator knows what they are
 * agreeing to before anything is deleted.
 */
export const CLEAN_ACTION_DESCRIPTIONS: Record<CleanAction, string> = {
  CLEAN_TEMP: 'Removes cached temporary files from the predefined safe temp location.',
  CLEAN_BROWSER_CACHE: 'Removes cached web data from the predefined safe browser cache location.',
  CLEAN_TRASH: 'Empties the trash, deleting files already marked as deleted.',
  CLEAN_PACKAGE_CACHE: 'Removes cached downloaded packages from the package-manager cache.',
  CLEAN_USER_CACHE: 'Removes the user application cache directory.',
  CLEAN_THUMBNAIL_CACHE: 'Removes the file manager thumbnail cache, which regenerates on demand.',
  CLEAN_OLD_LOGS: 'Removes rotated log files older than the configured age. Active logs are untouched.',
};

/**
 * Cleanup actions that are safe to run automatically as part of Daily Auto
 * Cleanup. This is a *subset* of `CLEAN_ACTIONS`, not a second allowlist: the
 * ids are the same, and a daily cleanup request is validated against
 * `CLEAN_ACTIONS` first and against this set second.
 *
 * `CLEAN_USER_CACHE` is deliberately absent: emptying the whole user cache
 * directory daily is more disruptive than the other categories and it is not
 * an obvious default, so it stays a manual-only action. Nothing here is
 * unsafe to run manually — the split is about what runs without the owner
 * watching the confirmation sheet.
 */
export const DAILY_SAFE_CLEAN_ACTIONS: readonly CleanAction[] = [
  'CLEAN_TEMP',
  'CLEAN_BROWSER_CACHE',
  'CLEAN_THUMBNAIL_CACHE',
  'CLEAN_PACKAGE_CACHE',
  'CLEAN_OLD_LOGS',
  'CLEAN_TRASH',
];

/**
 * One structured cleanup action the mobile client may preview and confirm.
 *
 * Every field is derived from the device's own storage report and the
 * platform the agent reported: the backend computes the list, and the client
 * can only pick from it.
 */
export interface CleanActionDescriptor {
  /** Structured action id from `CLEAN_ACTIONS`. */
  actionId: CleanAction;
  /** Dashboard label, e.g. `Browser Cache`. */
  label: string;
  /** What the action removes, in plain language. */
  description: string;
  /** Measured bytes the action would free, from the last storage report. */
  estimatedBytes: number;
  /** Items the analyser counted in the category. */
  itemCount: number;
  /** Platforms this action is supported on. */
  platforms: CleanPlatform[];
  /** Always true: preview never touches the filesystem. */
  dryRunSupported: boolean;
  /**
   * False when the analyser could not fully read the category
   * (`safeToAnalyze: false`), or when the device reported no measurement at
   * all. The UI shows the estimate as a lower bound rather than exact.
   */
  estimateReliable: boolean;
}

/** Result of one executed cleanup action, reported by the agent. */
export interface CleanResultPayload {
  actionId: CleanAction;
  /** `NOT_SUPPORTED` when the action cannot run on this platform/host. */
  status: 'SUCCESS' | 'PARTIAL' | 'FAILED' | 'NOT_SUPPORTED';
  itemsRemoved: number;
  bytesFreed: number;
  errors: number;
  /** Free-form detail; never a path, never file contents. */
  detail?: string;
}

/**
 * One AI cleanup recommendation (Phase 4C).
 *
 * AI is advisory only. It may name an `actionId` from the cleanup allowlist
 * and give a reason; it may not name a path, a shell command, a glob or a
 * file. The backend validates the id against `CLEAN_ACTIONS` before the
 * recommendation is ever stored or shown, so a malformed or hostile AI output
 * is rejected rather than surfaced.
 */
export interface CleanupRecommendation {
  actionId: CleanAction;
  reason: string;
  estimatedBytes: number;
}

/** Lifecycle of a command from the mobile request to the agent's reply. */
export const COMMAND_STATUSES = ['queued', 'sent', 'succeeded', 'failed', 'expired'] as const;
export type CommandStatus = (typeof COMMAND_STATUSES)[number];

/**
 * Command request issued by a mobile client.
 *
 * `requestId` is a server-generated correlation id: it appears in the audit
 * row, in the `command.dispatch` frame to the agent and in the
 * `command.result` frame back to the client, so every hop is traceable.
 *
 * `target` is required by the three `APPLICATION_*` commands and names the
 * application to act on. It is an `applicationId` — never an executable path
 * and never a command line. The agent resolves it against its own registry,
 * so a mobile client cannot choose what runs.
 */
export interface CommandRequest {
  command: DeviceCommand | CleanAction;
  /** Optional human note, e.g. why the device was locked. */
  note?: string;
  /** Application target, required for `APPLICATION_*` commands. */
  target?: string;
}

export interface CommandRecord {
  id: string;
  deviceId: string;
  command: DeviceCommand | CleanAction;
  status: CommandStatus;
  note?: string | null;
  /** Application target, stored only for `APPLICATION_*` commands. */
  target?: string | null;
  /** Agent-side detail — an error message on failure, output on success. */
  result?: string | null;
  createdAt: string;
  updatedAt: string;
  /** Epoch seconds after which an un-answered command is `expired`. */
  expiresAt?: string | null;
}

/** Pairing response: the only time a command token crosses the wire. */
export interface PairResponse {
  deviceId: string;
  commandToken: string;
  /** Epoch seconds. */
  expiresAt: number;
}

/**
 * Backend → agent: execute this command.
 *
 * `command` is one allowlisted id — an `OsCommand`, an `ApplicationCommand`
 * or a Phase 4B `CleanAction`. `target` is present only for
 * `APPLICATION_*` commands and is always an `applicationId` from
 * `ApplicationDescriptor.applicationId`. The agent re-validates both against
 * its own registries before doing anything.
 *
 * A cleanup dispatch carries no target and no path: the agent resolves the
 * action id to its own fixed roots, exactly as it resolves an application id
 * to a fixed argv.
 */
export interface CommandDispatchPayload {
  requestId: string;
  command: DeviceCommand | CleanAction;
  note?: string;
  /** Application target for `APPLICATION_*` commands. */
  target?: string;
  /** Epoch seconds the agent must answer by. */
  deadline: number;
}

/** Agent → backend: what happened. */
export interface CommandResultPayload {
  requestId: string;
  status: 'succeeded' | 'failed';
  /** Error text on failure, optional confirmation on success. */
  result?: string;
  durationMs?: number;
}

/** Phase 6 file manager. References are opaque agent-issued handles, never paths. */
export const FILE_OPERATIONS = [
  'FILE_LIST', 'FILE_STAT', 'FILE_SEARCH', 'FILE_DOWNLOAD', 'FILE_UPLOAD',
  'FILE_RENAME', 'FILE_MOVE', 'FILE_DELETE',
] as const;
export type FileOperation = (typeof FILE_OPERATIONS)[number];
export const FILE_ROOT_IDS = ['HOME', 'DOCUMENTS', 'DOWNLOADS', 'PICTURES', 'VIDEOS', 'DESKTOP', 'PROJECTS'] as const;
export type FileRootId = (typeof FILE_ROOT_IDS)[number];
export type FileEntryType = 'file' | 'directory';
export interface FileEntry {
  id: string;
  name: string;
  type: FileEntryType;
  size: number;
  modifiedAt: string;
  permissions?: string;
}
export interface FileRequest {
  operation: FileOperation;
  rootId?: FileRootId;
  entryId?: string;
  destinationId?: string;
  name?: string;
  query?: string;
  limit?: number;
  size?: number;
}
export interface FileResult {
  requestId: string;
  operation: FileOperation;
  status: 'SUCCEEDED' | 'FAILED' | 'NOT_ALLOWED';
  code?: string;
  reason?: string;
  entries?: FileEntry[];
  entry?: FileEntry;
  transfer?: { direction: FileTransferDirection; name: string; size: number };
  bytes?: number;
}
export interface FileDispatchPayload extends FileRequest { requestId: string; deadline: number; }
export const FILE_TRANSFER_DIRECTIONS = ['download', 'upload'] as const;
export type FileTransferDirection = (typeof FILE_TRANSFER_DIRECTIONS)[number];
export interface FileTransferStart { requestId: string; direction: FileTransferDirection; name: string; size: number; }
export interface FileTransferChunk { requestId: string; sequence: number; dataBase64: string; }
export interface FileTransferProgress { requestId: string; transferred: number; total: number; }

/** Phase 7 Remote Desktop. Control metadata only; screen pixels never enter audit. */
export const REMOTE_SESSION_STATUSES = ['REQUESTED', 'ACTIVE', 'PAUSED', 'STOPPING', 'ENDED', 'EXPIRED', 'FAILED'] as const;
export type RemoteSessionStatus = (typeof REMOTE_SESSION_STATUSES)[number];
export const REMOTE_OPERATIONS = ['REMOTE_DESKTOP_START', 'REMOTE_DESKTOP_STOP', 'REMOTE_DESKTOP_STATUS', 'REMOTE_SCREENSHOT'] as const;
export type RemoteOperation = (typeof REMOTE_OPERATIONS)[number];
export const REMOTE_INPUT_TYPES = ['MOUSE_MOVE', 'MOUSE_LEFT_DOWN', 'MOUSE_LEFT_UP', 'MOUSE_RIGHT_DOWN', 'MOUSE_RIGHT_UP', 'MOUSE_MIDDLE_DOWN', 'MOUSE_MIDDLE_UP', 'MOUSE_SCROLL', 'KEY_DOWN', 'KEY_UP'] as const;
export type RemoteInputType = (typeof REMOTE_INPUT_TYPES)[number];
export const REMOTE_KEYS = ['CTRL', 'ALT', 'SHIFT', 'META', 'ENTER', 'ESC', 'TAB', 'BACKSPACE', 'ARROW_UP', 'ARROW_DOWN', 'ARROW_LEFT', 'ARROW_RIGHT', 'SPACE', ...Array.from({ length: 12 }, (_, index) => `F${index + 1}`)] as const;
export type RemoteKey = (typeof REMOTE_KEYS)[number];
export interface RemoteDesktopCapabilities {
  screenshot: boolean; streaming: boolean; mouse: boolean; keyboard: boolean;
  maxWidth: number; maxHeight: number; maxFps: number; supportedFormats: ('jpeg' | 'webp')[];
}
export interface RemoteSession {
  sessionId: string; deviceId: string; status: RemoteSessionStatus; capabilities: RemoteDesktopCapabilities;
  createdAt: string; lastActivityAt: string; expiresAt: string; reason?: string | null;
}
export interface RemoteStartRequest { operation: 'REMOTE_DESKTOP_START'; }
export interface RemoteInput {
  sessionId: string; type: RemoteInputType; x?: number; y?: number; delta?: number; key?: RemoteKey;
}
export interface RemoteAgentResult { sessionId: string; status: RemoteSessionStatus; reason?: string; capabilities?: RemoteDesktopCapabilities; }

/**
 * Application control (Phase 3B) — how a target is identified.
 *
 * The identifier is never an executable path and never a command line. A
 * mobile client sends `applicationId`; the agent resolves it against the
 * descriptors *it* holds. That keeps the control decision on the device: a
 * mobile request can only choose between applications the operator already
 * registered, so there is no way to ask the agent to launch something
 * arbitrary.
 */
export const APPLICATION_ID_MAX_LENGTH = 64;
export const APPLICATION_LABEL_MAX_LENGTH = 120;

export interface ApplicationDescriptor {
  /**
   * The stable, case-insensitive identifier the mobile client sends.
   * Matches the process `name` the agent reports in Phase 3A telemetry, so a
   * row in the process list can be bound to a controllable application
   * without a second namespace.
   */
  applicationId: string;
  /** Human label for the dashboard. */
  label: string;
}

/** Public view of an application the operator has made controllable. */
export interface ApplicationPublic {
  applicationId: string;
  label: string;
  /** True when the agent's last process snapshot lists it as running. */
  running: boolean;
}

/** Response payload for `GET /api/applications/:deviceId`. */
export interface ApplicationCatalogPublic {
  deviceId: string;
  applications: ApplicationPublic[];
  timestamp: string;
}

/**
 * Storage analysis (Phase 4A) — read-only aggregate disk breakdown.
 *
 * The agent walks a fixed list of *known, safe* locations (Downloads, caches,
 * trash, package cache) and reports how much space each one occupies. It is a
 * census of well-known directories, never a filesystem scan, and it carries
 * no file contents, no names and no absolute paths — only byte totals and
 * item counts per category.
 *
 * Nothing in this contract is deletable. Phase 4A is analysis only; cleanup
 * is a separate decision that has not been built.
 */
export const STORAGE_CATEGORY_IDS = [
  'downloads',
  'user-cache',
  'tmp',
  'trash',
  'package-cache',
  'browser-cache',
  'thumbnail-cache',
  'old-logs',
  'other',
] as const;
export type StorageCategoryId = (typeof STORAGE_CATEGORY_IDS)[number];

/** Ceiling the schema accepts, so a hostile agent cannot flood a frame. */
export const STORAGE_CATEGORY_CAP = 32;
export const STORAGE_CATEGORY_LABEL_MAX_LENGTH = 48;

/** One bucket of disk usage, as the agent measured it on the device. */
export interface StorageCategory {
  /** Which bucket this is; `other` is reserved for a location not in the list. */
  categoryId: StorageCategoryId;
  /** Dashboard label, e.g. `Downloads`. */
  label: string;
  /** Total bytes the directory tree occupies. */
  bytes: number;
  /** Files and directories counted, not a size in any unit. */
  itemCount: number;
  /**
   * False when the agent measured this bucket but could not fully read it
   * (a permission error mid-walk). The total is then a lower bound, and the
   * dashboard should say "partial" rather than present it as exact.
   */
  safeToAnalyze: boolean;
}

/** The whole breakdown plus the filesystem totals it is measured against. */
export interface StorageSummary {
  deviceId: string;
  totalBytes: number;
  usedBytes: number;
  freeBytes: number;
  /** Share of `totalBytes` in use, 0–100. */
  usagePercent: number;
  categories: StorageCategory[];
  timestamp: string;
}

/** Public view for `GET /api/storage/:deviceId` — identical to the input. */
export type StorageSummaryPublic = StorageSummary;

/** Client → server frame payload for the `storage` frame. */
export type StorageSummaryInput = Omit<StorageSummary, 'deviceId'>;

/**
 * Phase 5 — Security & Owner Protection.
 *
 * The owner's identity is established **on the device**. The backend holds the
 * *result* of an authentication (a mode and a timestamp), never a biometric
 * sample, never a template, never an image. There is no field in any of these
 * types that could carry biometric data, and no route that accepts one.
 *
 * Four states cover everything Phase 5 needs. They are deliberately explicit
 * so no code path has to infer trust from a side channel:
 *
 *   `OWNER`     the owner authenticated. Full VIGILIA functionality.
 *   `GUEST`     a known-but-unauthorised user, or an unknown user the
 *               intrusion policy chose to admit as a guest rather than lock.
 *               Monitoring stays on; owner-only actions stay blocked.
 *   `UNKNOWN`   an unauthenticated user in front of the machine. The
 *               protected state: nothing owner-only runs.
 *   `LOCKED`    a security lock triggered by repeated failed authentication.
 *               Stronger than `UNKNOWN`: it will not clear itself, only a
 *               successful owner authentication or an owner PIN.
 */
export const OWNER_STATES = ['OWNER', 'GUEST', 'UNKNOWN', 'LOCKED'] as const;
export type OwnerState = (typeof OWNER_STATES)[number];

/** True when the state grants owner-only functionality. Never inferred. */
export function isOwnerMode(state: OwnerState | undefined | null): boolean {
  return state === 'OWNER';
}

/**
 * The owner's presence claim, as the device reports it and the dashboard sees
 * it. Every field is derivable from public state; none of it is biometric.
 */
export interface OwnerPresence {
  deviceId: string;
  state: OwnerState;
  /** Epoch seconds the state was last established. */
  since: number;
  /** Epoch seconds of the last *successful* owner authentication. */
  lastAuthenticatedAt?: number | null;
  /** Consecutive failed authentications since the last success. */
  failedAttempts: number;
  /** Capabilities the device's security policy reports as active. */
  capabilities: SecurityCapabilities;
  /** ISO date of the last daily cleanup (`YYYY-MM-DD`), for the dashboard. */
  lastDailyCleanupDate?: string | null;
  timestamp: string;
}

export interface SecurityCapabilities {
  faceRecognition: boolean;
  liveness: boolean;
  guestMode: boolean;
  intrusionDetection: boolean;
  dailyCleanup: boolean;
}

/**
 * How an authentication attempt concluded. Only the outcome travels; the
 * evidence (the image, the template, the liveness signal) stays on the device.
 */
export const SECURITY_EVENT_TYPES = [
  'owner.authenticated',
  'owner.authentication_failed',
  'owner.locked',
  'unknown_person_detected',
  'guest.mode_activated',
  'guest.mode_deactivated',
  'security_photo_captured',
  'security_lock_triggered',
  'security_lock_cleared',
  'daily_cleanup.started',
  'daily_cleanup.completed',
  'daily_cleanup.skipped',
  'daily_cleanup.failed',
] as const;
export type SecurityEventType = (typeof SECURITY_EVENT_TYPES)[number];

/** Severity for the dashboard timeline. Derived server-side from the type. */
export type SecurityEventSeverity = 'ok' | 'warn' | 'bad';

export const SECURITY_EVENT_SEVERITY: Record<SecurityEventType, SecurityEventSeverity> = {
  'owner.authenticated': 'ok',
  'owner.authentication_failed': 'warn',
  'owner.locked': 'bad',
  'unknown_person_detected': 'warn',
  'guest.mode_activated': 'warn',
  'guest.mode_deactivated': 'ok',
  'security_photo_captured': 'warn',
  'security_lock_triggered': 'bad',
  'security_lock_cleared': 'ok',
  'daily_cleanup.started': 'ok',
  'daily_cleanup.completed': 'ok',
  'daily_cleanup.skipped': 'warn',
  'daily_cleanup.failed': 'bad',
};

/** Events that must never be created by anything but the device's own agent. */
export const AGENT_ONLY_SECURITY_EVENTS: readonly SecurityEventType[] = SECURITY_EVENT_TYPES;

/**
 * One security event, integrated into the same audit surface as
 * `CommandLog` — the Activity screen renders both, newest-first.
 *
 * `metadata` is bounded to a handful of safe, enumerated keys. It carries
 * counts and outcomes only: there is deliberately no field for a photo, a
 * template, a path or a credential, so one cannot be logged by accident.
 */
export interface SecurityEvent {
  id: string;
  deviceId: string;
  eventType: SecurityEventType;
  /** `ok | warn | bad`, derived from the type at write time. */
  severity: SecurityEventSeverity;
  /** Safe human-readable description, never a secret. */
  description: string;
  timestamp: string;
  /** Epoch seconds. */
  since?: number | null;
  /** Safe, whitelisted metadata only. */
  metadata?: SecurityEventMetadata;
  /** Present when the event was caused by a dispatched command. */
  requestId?: string | null;
}

export interface SecurityEventMetadata {
  failedAttempts?: number;
  bytesFreed?: number;
  actions?: number;
  /** A cleanup or auth reason code (`NOT_AUTHENTICATED`, `ALREADY_DONE` …). */
  reason?: string;
  /** A cleanup action id from the allowlist, nothing else. */
  actionId?: CleanAction;
}

/** The safe metadata keys, enforced at validation. */
export const SECURITY_METADATA_KEYS = [
  'failedAttempts',
  'bytesFreed',
  'actions',
  'reason',
  'actionId',
] as const;
export type SecurityMetadataKey = (typeof SECURITY_METADATA_KEYS)[number];

/**
 * Daily Auto Cleanup configuration (Phase 5F).
 *
 * `categories` is a subset of `CLEAN_ACTIONS` — the same structured allowlist
 * the manual cleaner uses, re-validated on write. `CLEAN_TRASH` is opt-in
 * (`trashEnabled`) and defaults off, because emptying the trash is the one
 * safe action with irreversible consequences.
 *
 * The defaults are conservative: cleanup off until the owner turns it on, no
 * trash, no old-logs pruning without the category enabled.
 */
export const DAILY_CLEANUP_TRIGGERS = ['AFTER_OWNER_LOGIN'] as const;
export type DailyCleanupTrigger = (typeof DAILY_CLEANUP_TRIGGERS)[number];

/** The three security toggles the mobile app owns (Phase 5F). */
export interface SecuritySettings {
  intrusionDetection: boolean;
  guestMode: boolean;
  autoLock: boolean;
}

export interface DailyCleanupConfig {
  enabled: boolean;
  trigger: DailyCleanupTrigger;
  /** Safe categories only, each one a `CLEAN_ACTIONS` id. */
  categories: CleanAction[];
  /** Trash is opt-in on top of `categories`. */
  trashEnabled: boolean;
  /** Logs older than this many days are removed by `CLEAN_OLD_LOGS`. */
  logRetentionDays: number;
}

export const DEFAULT_DAILY_CLEANUP_CONFIG: Readonly<DailyCleanupConfig> = {
  enabled: false,
  trigger: 'AFTER_OWNER_LOGIN',
  categories: ['CLEAN_TEMP', 'CLEAN_BROWSER_CACHE', 'CLEAN_THUMBNAIL_CACHE', 'CLEAN_PACKAGE_CACHE'],
  trashEnabled: false,
  logRetentionDays: 30,
};

/** Categories the mobile settings UI may offer for daily cleanup. */
export const DAILY_CLEANUP_CATEGORY_ORDER: readonly CleanAction[] = [
  'CLEAN_TEMP',
  'CLEAN_BROWSER_CACHE',
  'CLEAN_THUMBNAIL_CACHE',
  'CLEAN_PACKAGE_CACHE',
  'CLEAN_OLD_LOGS',
  'CLEAN_TRASH',
];

/** Reasons a daily cleanup was skipped, reported in the event + UI. */
export const DAILY_CLEANUP_SKIP_REASONS = [
  'NOT_AUTHENTICATED',
  'ALREADY_DONE',
  'DISABLED',
  'NO_CATEGORIES',
] as const;
export type DailyCleanupSkipReason = (typeof DAILY_CLEANUP_SKIP_REASONS)[number];

export const DAILY_CLEANUP_SKIP_LABELS: Record<DailyCleanupSkipReason, string> = {
  NOT_AUTHENTICATED: 'Owner not authenticated',
  ALREADY_DONE: 'Already completed today',
  DISABLED: 'Daily cleanup is disabled',
  NO_CATEGORIES: 'No safe categories selected',
};

/** Client -> server frames. */
export interface WsClientMessageMap {
  heartbeat: HeartbeatPayload;
  telemetry: TelemetryPayload;
  processes: ProcessSnapshotPayload;
  'command.result': CommandResultPayload;
  'clean.result': CleanResultPayload & { requestId: string };
  'file.result': FileResult;
  'file.transfer.start': FileTransferStart;
  'file.transfer.chunk': FileTransferChunk;
  'file.transfer.progress': FileTransferProgress;
  'file.transfer.complete': { requestId: string };
  'file.transfer.failed': { requestId: string; code: string; reason: string };
  'remote.capabilities': RemoteDesktopCapabilities;
  'remote.session.result': RemoteAgentResult;
  /** Phase 5: the device's owner-presence claim (state, never biometrics). */
  'security.state': OwnerPresence;
  /** Phase 5: one security event from the device's own agent. */
  'security.event': SecurityEventInput;
}

/** What an agent may push as a security event. `severity` is derived, not sent. */
export type SecurityEventInput = Omit<SecurityEvent, 'id' | 'severity' | 'timestamp'> & {
  timestamp?: string;
};

/** Server -> client frames. */
export interface WsServerMessageMap {
  'device.status': { deviceId: string; status: DeviceStatus; lastSeen: string };
  'device.registered': DevicePublic;
  'telemetry.latest': { deviceId: string; telemetry: TelemetryPayload };
  'processes.latest': { deviceId: string; snapshot: ProcessSnapshotPublic };
  'applications.latest': { deviceId: string; catalog: ApplicationCatalogPublic };
  'storage.latest': { deviceId: string; summary: StorageSummaryPublic };
  'cleanup.latest': {
    deviceId: string;
    actions: CleanActionDescriptor[];
    recommendations: CleanupRecommendation[];
  };
  'command.dispatch': CommandDispatchPayload;
  'file.dispatch': FileDispatchPayload;
  'file.transfer.accepted': { requestId: string };
  'file.transfer.chunk': FileTransferChunk;
  'file.transfer.complete': { requestId: string };
  'file.transfer.cancel': { requestId: string };
  'remote.session.start': { sessionId: string; deadline: number };
  'remote.session.stop': { sessionId: string; reason: string };
  'remote.input': RemoteInput;
  'command.result': { requestId: string; deviceId: string; status: CommandStatus; result?: string };
  /** Phase 5: the device's owner-presence state, broadcast on any change. */
  'security.state': { deviceId: string; presence: OwnerPresence };
  /** Phase 5: one security event, for the timeline and the notifications. */
  'security.event': { deviceId: string; event: SecurityEvent };
  'error': { code: string; message: string };
}

export type AnyWsServerMessage = WsMessage<any>;
