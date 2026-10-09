/**
 * VIGILIA — zod validation schemas
 * Every network input is validated before it touches the domain.
 */

import { z } from 'zod';
import {
  APPLICATION_COMMANDS,
  APPLICATION_ID_MAX_LENGTH,
  APPLICATION_LABEL_MAX_LENGTH,
  CLEAN_ACTIONS,
  CLEAN_PLATFORMS,
  CLEAN_REASON_MAX_LENGTH,
  DEVICE_COMMANDS,
  DAILY_CLEANUP_SKIP_REASONS,
  DAILY_CLEANUP_TRIGGERS,
  PROCESS_HARD_CAP,
  PROCESS_STATUSES,
  SECURITY_EVENT_TYPES,
  SECURITY_METADATA_KEYS,
  STORAGE_CATEGORY_CAP,
  STORAGE_CATEGORY_IDS,
  STORAGE_CATEGORY_LABEL_MAX_LENGTH,
  OWNER_STATES,
  FILE_OPERATIONS,
  FILE_ROOT_IDS,
  FILE_TRANSFER_DIRECTIONS,
  REMOTE_INPUT_TYPES,
  REMOTE_KEYS,
  REMOTE_SESSION_STATUSES,
  HARDWARE_CONTROLS,
  AI_RISK_LEVELS,
  AI_TOOLS,
} from './types.js';

const percent = z.number().min(0).max(100);

export const deviceOsSchema = z.enum(['ubuntu', 'windows', 'linux', 'unknown']);

export const registerDeviceSchema = z.object({
  name: z.string().trim().min(1).max(120),
  os: deviceOsSchema,
  hostname: z.string().trim().min(1).max(253),
  agentVersion: z.string().trim().max(64).optional(),
  protocolVersion: z.number().int().min(1).max(65_535),
  systemInfo: z
    .object({
      os: z.string().max(120).optional(),
      osVersion: z.string().max(120).optional(),
      kernelVersion: z.string().max(120).optional(),
      cpuModel: z.string().max(180).optional(),
      cpuCores: z.number().int().min(1).max(1024).optional(),
      totalMemoryMb: z.number().int().min(0).optional(),
      totalStorageGb: z.number().int().min(0).optional(),
      agentVersion: z.string().max(64).optional(),
      platform: z.string().max(120).optional(),
      hostname: z.string().max(253).optional(),
    })
    .optional(),
});

export const deviceStatusSchema = z.enum(['online', 'offline', 'standby']);

export const networkStatusSchema = z.object({
  connected: z.boolean(),
  ssid: z.string().max(120).optional(),
  downloadMbps: z.number().min(0).optional(),
  uploadMbps: z.number().min(0).optional(),
  interface: z.string().max(64).optional(),
});
export const hardwareCapabilitiesSchema = z.object({ gpu: z.boolean(), gpuVram: z.boolean(), cpuTemperature: z.boolean(), gpuTemperature: z.boolean(), fanSpeed: z.boolean(), battery: z.boolean(), charging: z.boolean() }).strict();
export const hardwareTelemetrySchema = z.object({
  capabilities: hardwareCapabilitiesSchema, cpuLoad1: z.number().min(0).max(4096).optional(), ramAvailableMb: z.number().int().min(0).max(16_777_216).optional(),
  gpuUsage: percent.optional(), gpuVramUsedMb: z.number().int().min(0).max(16_777_216).optional(), gpuVramTotalMb: z.number().int().min(0).max(16_777_216).optional(),
  cpuTemperatureC: z.number().min(-40).max(200).optional(), gpuTemperatureC: z.number().min(-40).max(200).optional(), fanRpm: z.number().int().min(0).max(100_000).optional(),
  batteryStatus: z.enum(['charging', 'discharging', 'full', 'unknown']).optional(),
}).strict();
export const networkCapabilitiesSchema = z.object({ gateway: z.boolean(), dns: z.boolean(), wifiSignal: z.boolean(), latency: z.boolean(), packetLoss: z.boolean(), throughput: z.boolean() }).strict();
export const networkTelemetrySchema = z.object({
  capabilities: networkCapabilitiesSchema, connected: z.boolean(), interface: z.string().max(64).optional(), interfaceType: z.enum(['wifi', 'ethernet', 'cellular', 'loopback', 'unknown']).optional(),
  ipAddress: z.string().ip().optional(), gateway: z.string().ip().optional(), dns: z.array(z.string().ip()).max(4).optional(), wifiSignalPercent: percent.optional(), latencyMs: z.number().min(0).max(60_000).optional(), packetLossPercent: percent.optional(), downloadMbps: z.number().min(0).max(100_000).optional(), uploadMbps: z.number().min(0).max(100_000).optional(),
}).strict();

export const heartbeatSchema = z.object({
  deviceId: z.string().trim().min(1).max(64),
  status: deviceStatusSchema.default('online'),
  timestamp: z.string().datetime(),
});

export const telemetrySchema = z.object({
  deviceId: z.string().trim().min(1).max(64),
  cpu: percent.optional(),
  ram: percent.optional(),
  storage: percent.optional(),
  battery: percent.optional(),
  os: z.string().max(120).optional(),
  hostname: z.string().max(253).optional(),
  uptime: z.number().int().min(0).optional(),
  gpu: percent.optional(),
  temp: z.number().min(-40).max(200).optional(),
  network: networkStatusSchema.optional(),
  hardware: hardwareTelemetrySchema.optional(),
  networkTelemetry: networkTelemetrySchema.optional(),
  timestamp: z.string().datetime(),
});
export const hardwareControlRequestSchema = z.object({ control: z.enum(HARDWARE_CONTROLS), value: z.union([z.number().int().min(0).max(100), z.boolean(), z.enum(['play', 'pause'])]).optional() }).strict().superRefine((value, context) => {
  if (['VOLUME', 'BRIGHTNESS'].includes(value.control) && typeof value.value !== 'number') context.addIssue({ code: z.ZodIssueCode.custom, message: 'numeric value required' });
  if (['WIFI', 'BLUETOOTH', 'MICROPHONE', 'CAMERA'].includes(value.control) && typeof value.value !== 'boolean') context.addIssue({ code: z.ZodIssueCode.custom, message: 'boolean value required' });
  if (value.control === 'MEDIA_PLAYBACK' && !['play', 'pause'].includes(String(value.value))) context.addIssue({ code: z.ZodIssueCode.custom, message: 'play or pause required' });
});
export const hardwareControlCapabilitiesSchema = z.object({ BRIGHTNESS: z.boolean(), VOLUME: z.boolean(), WIFI: z.boolean(), BLUETOOTH: z.boolean(), MICROPHONE: z.boolean(), CAMERA: z.boolean(), MEDIA_PLAYBACK: z.boolean() }).strict();
export const hardwareControlResultSchema = z.object({ requestId: z.string().regex(/^req_[A-Za-z0-9_-]{8,64}$/), control: z.literal('BRIGHTNESS'), status: z.enum(['SUCCEEDED', 'UNSUPPORTED', 'FAILED']), value: z.number().int().min(0).max(100).optional(), reason: z.string().max(280).optional() }).strict();
export const aiChatRequestSchema = z.object({ message: z.string().trim().min(1).max(500) }).strict();
export const aiConfirmationSchema = z.object({ requestId: z.string().regex(/^req_[A-Za-z0-9_-]{8,64}$/), approved: z.boolean() }).strict();
export const guardianPreferencesSchema = z.object({ healthAlerts: z.boolean(), securityAlerts: z.boolean() }).strict();
export const aiRiskSchema = z.enum(AI_RISK_LEVELS);
export const aiToolSchema = z.enum(AI_TOOLS);
export const agentHelloSchema = z.object({ agentVersion: z.string().trim().min(1).max(64), protocolVersion: z.number().int() }).strict();

// ---------------- process telemetry (Phase 3A) ----------------

export const processStatusSchema = z.enum(PROCESS_STATUSES);

export const processInfoSchema = z.object({
  pid: z.number().int().min(-1).max(4_194_303),
  name: z.string().trim().min(1).max(512),
  cpu: z.number().min(0).max(100),
  memoryMb: z.number().min(0).optional(),
  memoryPercent: z.number().min(0).max(100).optional(),
  threads: z.number().int().min(0).optional(),
  parentPid: z.number().int().min(0).optional(),
  status: processStatusSchema.optional(),
  executablePath: z.string().max(2048).optional(),
  username: z.string().max(256).optional(),
  startedAt: z.string().datetime().optional(),
  commandLine: z.string().max(8192).optional(),
});

export const processSnapshotSchema = z.object({
  deviceId: z.string().trim().min(1).max(64),
  processes: z.array(processInfoSchema).max(PROCESS_HARD_CAP),
  totalCount: z.number().int().min(0).max(1_000_000),
  limit: z.number().int().min(1).max(PROCESS_HARD_CAP),
  timestamp: z.string().datetime(),
});

// ---------------- storage cleanup (Phase 4B) ----------------

/**
 * The cleanup action allowlist.
 *
 * An `actionId` is the only thing a mobile client may send to ask for
 * cleanup, and it must be exactly one of these five constants. The enum is
 * the whole protection: no path, glob, pattern or free-form string can pass
 * it, so an arbitrary-deletion request is a validation error rather than a
 * policy decision.
 */
export const cleanActionSchema = z.enum(CLEAN_ACTIONS);

export const cleanActionDescriptorSchema = z.object({
  actionId: cleanActionSchema,
  label: z.string().trim().min(1).max(STORAGE_CATEGORY_LABEL_MAX_LENGTH),
  description: z.string().trim().min(1).max(280),
  estimatedBytes: z.number().int().min(0),
  itemCount: z.number().int().min(0),
  platforms: z.array(z.enum(CLEAN_PLATFORMS)).min(1),
  dryRunSupported: z.boolean(),
  estimateReliable: z.boolean(),
});

export const cleanResultSchema = z.object({
  actionId: cleanActionSchema,
  status: z.enum(['SUCCESS', 'PARTIAL', 'FAILED', 'NOT_SUPPORTED']),
  itemsRemoved: z.number().int().min(0),
  bytesFreed: z.number().int().min(0),
  errors: z.number().int().min(0),
  detail: z.string().trim().max(1000).optional(),
});

/**
 * The result frame an agent answers a `CLEAN_*` dispatch with.
 *
 * `requestId` ties it back to the audited `CLEAN_*` command row, and the
 * backend rebinds it to the authenticated connection like every other agent
 * frame, so an agent cannot settle another device's cleanup.
 */
export const cleanResultFrameSchema = cleanResultSchema.extend({
  requestId: z.string().trim().min(1).max(64),
});

/**
 * One AI cleanup recommendation (Phase 4C).
 *
 * `reason` is capped and must be plain text; there is no field here that
 * could hold a path or a command. The backend re-validates `actionId`
 * against `CLEAN_ACTIONS` before storing a recommendation, so AI output that
 * names an unknown action — or smuggles a path where an id belongs — is
 * dropped rather than shown to the operator.
 */
export const cleanupRecommendationSchema = z.object({
  actionId: cleanActionSchema,
  reason: z.string().trim().min(1).max(CLEAN_REASON_MAX_LENGTH),
  estimatedBytes: z.number().int().min(0),
});

export const cleanupRecommendationsSchema = z.object({
  recommendations: z.array(cleanupRecommendationSchema).max(STORAGE_CATEGORY_CAP),
});

/**
 * A mobile preview request: name one structured cleanup action.
 *
 * `actionId` is the whole body. There is no path field, no glob field, no
 * target field and no options field — the only thing a client can ask is
 * "what would CLEAN_TEMP free?", and the answer comes from the device's own
 * measurement.
 */
export const cleanupPreviewRequestSchema = z.object({
  actionId: cleanActionSchema,
});

// ---------------- security & owner protection (Phase 5) ----------------

export const ownerStateSchema = z.enum(OWNER_STATES);

/**
 * The device's owner-presence claim. `strict` because this frame drives the
 * dashboard's trust indicator: an extra key is a probe, not a hint.
 *
 * Nothing in this schema can carry biometric data. There is no field for an
 * image, a template, an embedding or a confidence score — the device reports
 * the *outcome* of an authentication and nothing more.
 */
export const securityCapabilitiesSchema = z
  .strictObject({
    faceRecognition: z.boolean(),
    liveness: z.boolean(),
    guestMode: z.boolean(),
    intrusionDetection: z.boolean(),
    dailyCleanup: z.boolean(),
  });

export const ownerPresenceSchema = z.strictObject({
  deviceId: z.string().trim().min(1).max(64),
  state: ownerStateSchema,
  since: z.number().int().min(0),
  lastAuthenticatedAt: z.number().int().min(0).nullable().optional(),
  failedAttempts: z.number().int().min(0).max(999),
  capabilities: securityCapabilitiesSchema,
  lastDailyCleanupDate: z
    .string()
    .trim()
    .regex(/^\d{4}-\d{2}-\d{2}$/, 'must be a YYYY-MM-DD date')
    .nullable()
    .optional(),
  timestamp: z.string().datetime(),
});

/**
 * Safe metadata only. Every key is enumerated and every value is bounded, so
 * a hostile agent cannot log a path, a credential or biometric material
 * through a security event — the schema has no field that could hold one.
 */
export const securityEventMetadataSchema = z
  .strictObject({
    failedAttempts: z.number().int().min(0).max(999).optional(),
    bytesFreed: z.number().int().min(0).optional(),
    actions: z.number().int().min(0).max(64).optional(),
    reason: z.string().trim().max(64).optional(),
    actionId: cleanActionSchema.optional(),
  })
  // Re-state the allowlist as a check, so a value smuggled under a key the
  // object does not name is still rejected rather than silently dropped.
  .refine((value) => Object.keys(value).every((k) => (SECURITY_METADATA_KEYS as readonly string[]).includes(k)), {
    message: 'unknown metadata key',
  });

export const securityEventTypeSchema = z.enum(SECURITY_EVENT_TYPES);

/**
 * One security event pushed by the device's own agent.
 *
 * `severity` is *not* accepted here: the backend derives it from the event
 * type, so a device cannot report `owner.authenticated` with severity `bad`
 * and a probe cannot downgrade a lock to a warning.
 */
export const securityEventSchema = z.strictObject({
  deviceId: z.string().trim().min(1).max(64),
  eventType: securityEventTypeSchema,
  description: z.string().trim().min(1).max(280),
  since: z.number().int().min(0).nullable().optional(),
  metadata: securityEventMetadataSchema.optional(),
  requestId: z.string().trim().min(1).max(64).nullable().optional(),
  timestamp: z.string().datetime().optional(),
});

/** Daily Auto Cleanup configuration (Phase 5F). */
export const dailyCleanupConfigSchema = z.strictObject({
  enabled: z.boolean(),
  trigger: z.enum(DAILY_CLEANUP_TRIGGERS),
  categories: z.array(cleanActionSchema).max(CLEAN_ACTIONS.length),
  trashEnabled: z.boolean(),
  logRetentionDays: z.number().int().min(1).max(365),
});

/** A mobile request to change the Daily Auto Cleanup configuration. */
export const dailyCleanupConfigRequestSchema = z.strictObject({
  config: dailyCleanupConfigSchema,
});

/** A mobile request to change the security settings (Phase 5F). */
export const securitySettingsRequestSchema = z.strictObject({
  settings: z
    .strictObject({
      intrusionDetection: z.boolean().optional(),
      guestMode: z.boolean().optional(),
      autoLock: z.boolean().optional(),
    })
    .refine((value) => Object.keys(value).length > 0, {
      message: 'at least one setting is required',
    }),
});

export const dailyCleanupSkipReasonSchema = z.enum(DAILY_CLEANUP_SKIP_REASONS);

// ---------------- remote commands ----------------

export const deviceCommandSchema = z.enum(DEVICE_COMMANDS);

/**
 * Application identifier a mobile client may name.
 *
 * The charset is deliberately narrow: lowercase letters, digits, `-`, `_`
 * and `.`. No path separators, no spaces, no shell metacharacters can appear
 * in it, so even a hostile client cannot smuggle anything that looks like a
 * path or an argument through this field.
 */
export const applicationIdSchema = z
  .string()
  .trim()
  .toLowerCase()
  .min(1)
  .max(APPLICATION_ID_MAX_LENGTH)
  .regex(/^[a-z0-9][a-z0-9._-]*$/, 'must be a lowercase application id');

// `.strict()` rather than relying on Zod's default of stripping unknown
// keys: a request that carries anything but `command`/`note`/`target` is a
// probe, and silently dropping its extra fields would answer 202 as if the
// request had been understood. The route rejects it with 400 instead.
export const commandRequestSchema = z.strictObject({
  command: z.union([deviceCommandSchema, cleanActionSchema]),
  note: z.string().trim().max(280).optional(),
  target: applicationIdSchema.optional(),
});

/**
 * A cleanup request. `command` is a structured action id from the cleanup
 * allowlist — the only shape a cleanup request may take. There is no `path`,
 * no `target` and no options field, so the request cannot name a file.
 */
export const cleanupRequestSchema = z.strictObject({
  command: cleanActionSchema,
  note: z.string().trim().max(280).optional(),
});

/**
 * The three application commands must name a target. The schema enforces the
 * pairing rather than leaving it to each handler: a frame that reaches the
 * agent already carries a syntactically valid `applicationId`, and the agent
 * only has to decide whether it is in its registry.
 */
export const applicationCommandRequestSchema = commandRequestSchema.extend({
  command: z.enum(APPLICATION_COMMANDS),
  target: applicationIdSchema,
});

export const commandResultSchema = z.object({
  requestId: z.string().trim().min(1).max(64),
  status: z.enum(['succeeded', 'failed']),
  result: z.string().trim().max(1000).optional(),
  durationMs: z.number().int().min(0).max(24 * 60 * 60 * 1000).optional(),
});

const opaqueFileIdSchema = z.string().regex(/^f_[A-Za-z0-9_-]{16,256}$/);
const fileNameSchema = z.string().trim().min(1).max(180)
  .refine((value) => value !== '.' && value !== '..' && !/[\\/]/.test(value) && ![...value].some((char) => char.charCodeAt(0) < 32), 'invalid file name')
  .refine((value) => !/^\.env(?:\.|$)/i.test(value) && !/\.(pem|key|pfx|p12)$/i.test(value), 'protected file name');
export const fileRequestSchema = z.object({
  operation: z.enum(FILE_OPERATIONS),
  rootId: z.enum(FILE_ROOT_IDS).optional(),
  entryId: opaqueFileIdSchema.optional(),
  destinationId: opaqueFileIdSchema.optional(),
  name: fileNameSchema.optional(),
  query: z.string().trim().min(1).max(120).refine((value) => !/[\\/\0]/.test(value), 'invalid search query').optional(),
  limit: z.number().int().min(1).max(100).optional(),
  size: z.number().int().min(0).max(10 * 1024 * 1024).optional(),
}).strict().superRefine((value, context) => {
  const requireField = (field: keyof typeof value) => {
    if (!value[field]) context.addIssue({ code: z.ZodIssueCode.custom, path: [field], message: 'required for operation' });
  };
  if (value.operation === 'FILE_LIST') requireField('rootId');
  if (value.operation === 'FILE_SEARCH') { requireField('rootId'); requireField('query'); }
  if (['FILE_STAT', 'FILE_DOWNLOAD', 'FILE_DELETE'].includes(value.operation)) requireField('entryId');
  if (value.operation === 'FILE_RENAME') { requireField('entryId'); requireField('name'); }
  if (value.operation === 'FILE_MOVE') { requireField('entryId'); requireField('destinationId'); }
  if (value.operation === 'FILE_UPLOAD') { requireField('destinationId'); requireField('name'); requireField('size'); }
});
export const fileResultSchema = z.object({
  requestId: z.string().trim().min(1).max(64),
  operation: z.enum(FILE_OPERATIONS),
  status: z.enum(['SUCCEEDED', 'FAILED', 'NOT_ALLOWED']),
  code: z.string().trim().max(64).optional(),
  reason: z.string().trim().max(280).optional(),
  entries: z.array(z.object({ id: opaqueFileIdSchema, name: fileNameSchema, type: z.enum(['file', 'directory']), size: z.number().int().min(0), modifiedAt: z.string().datetime(), permissions: z.string().max(16).optional() }).strict()).max(100).optional(),
  entry: z.object({ id: opaqueFileIdSchema, name: fileNameSchema, type: z.enum(['file', 'directory']), size: z.number().int().min(0), modifiedAt: z.string().datetime(), permissions: z.string().max(16).optional() }).strict().optional(),
  transfer: z.object({ direction: z.enum(FILE_TRANSFER_DIRECTIONS), name: fileNameSchema, size: z.number().int().min(0).max(10 * 1024 * 1024) }).strict().optional(),
  bytes: z.number().int().min(0).max(10 * 1024 * 1024).optional(),
}).strict();
export const fileTransferStartSchema = z.object({ requestId: z.string().trim().min(1).max(64), direction: z.enum(FILE_TRANSFER_DIRECTIONS), name: fileNameSchema, size: z.number().int().min(0).max(10 * 1024 * 1024) }).strict();
export const fileTransferChunkSchema = z.object({ requestId: z.string().trim().min(1).max(64), sequence: z.number().int().min(0).max(10000), dataBase64: z.string().regex(/^[A-Za-z0-9+/]+={0,2}$/).min(1).max(96 * 1024) }).strict();
export const fileTransferProgressSchema = z.object({ requestId: z.string().trim().min(1).max(64), transferred: z.number().int().min(0).max(10 * 1024 * 1024), total: z.number().int().min(0).max(10 * 1024 * 1024) }).strict();
const remoteSessionIdSchema = z.string().regex(/^rds_[A-Za-z0-9_-]{16,128}$/);
export const remoteCapabilitiesSchema = z.object({
  screenshot: z.boolean(), streaming: z.boolean(), mouse: z.boolean(), keyboard: z.boolean(),
  maxWidth: z.number().int().min(0).max(3840), maxHeight: z.number().int().min(0).max(2160), maxFps: z.number().int().min(0).max(30),
  supportedFormats: z.array(z.enum(['jpeg', 'webp'])).max(2),
}).strict();
export const remoteStartRequestSchema = z.object({ operation: z.literal('REMOTE_DESKTOP_START') }).strict();
export const remoteInputSchema = z.object({
  sessionId: remoteSessionIdSchema, type: z.enum(REMOTE_INPUT_TYPES), x: z.number().int().min(0).max(3840).optional(), y: z.number().int().min(0).max(2160).optional(),
  delta: z.number().int().min(-1000).max(1000).optional(), key: z.enum(REMOTE_KEYS).optional(),
}).strict().superRefine((value, context) => {
  if (value.type === 'MOUSE_MOVE' && (value.x === undefined || value.y === undefined)) context.addIssue({ code: z.ZodIssueCode.custom, message: 'coordinates required' });
  if (value.type === 'MOUSE_SCROLL' && value.delta === undefined) context.addIssue({ code: z.ZodIssueCode.custom, message: 'delta required' });
  if (value.type === 'KEY_DOWN' || value.type === 'KEY_UP') if (!value.key) context.addIssue({ code: z.ZodIssueCode.custom, message: 'key required' });
});
export const remoteAgentResultSchema = z.object({ sessionId: remoteSessionIdSchema, status: z.enum(REMOTE_SESSION_STATUSES), reason: z.string().trim().max(280).optional(), capabilities: remoteCapabilitiesSchema.optional() }).strict();

export const applicationDescriptorSchema = z.object({
  applicationId: z.string().trim().min(1).max(APPLICATION_ID_MAX_LENGTH),
  label: z.string().trim().min(1).max(APPLICATION_LABEL_MAX_LENGTH),
});

/**
 * Storage breakdown (Phase 4A).
 *
 * Every byte total is a non-negative integer and every category id comes from
 * the fixed `STORAGE_CATEGORY_IDS` list, so an agent cannot push an arbitrary
 * bucket name. The frame is capped at `STORAGE_CATEGORY_CAP` entries.
 */
export const storageCategorySchema = z.object({
  categoryId: z.enum(STORAGE_CATEGORY_IDS),
  label: z.string().trim().min(1).max(STORAGE_CATEGORY_LABEL_MAX_LENGTH),
  bytes: z.number().int().min(0),
  itemCount: z.number().int().min(0),
  safeToAnalyze: z.boolean(),
});

export const storageSummarySchema = z.object({
  totalBytes: z.number().int().min(0),
  usedBytes: z.number().int().min(0),
  freeBytes: z.number().int().min(0),
  usagePercent: z.number().min(0).max(100),
  categories: z.array(storageCategorySchema).max(STORAGE_CATEGORY_CAP),
  timestamp: z.string().datetime(),
});

export const applicationPublicSchema = z.object({
  applicationId: z.string().trim().min(1).max(APPLICATION_ID_MAX_LENGTH),
  label: z.string().trim().min(1).max(APPLICATION_LABEL_MAX_LENGTH),
  running: z.boolean(),
});

/** Pairing body: the agent token proves the caller owns the device. */
export const pairSchema = z.object({
  deviceId: z.string().trim().min(1).max(64),
});

export const wsMessageSchema = z.discriminatedUnion('type', [
  z.object({
    type: z.literal('heartbeat'),
    data: heartbeatSchema,
  }),
  z.object({
    type: z.literal('telemetry'),
    data: telemetrySchema,
  }),
  z.object({
    type: z.literal('processes'),
    data: processSnapshotSchema,
  }),
  z.object({
    type: z.literal('command.result'),
    data: commandResultSchema,
  }),
  z.object({ type: z.literal('file.result'), data: fileResultSchema }),
  z.object({ type: z.literal('file.transfer.start'), data: fileTransferStartSchema }),
  z.object({ type: z.literal('file.transfer.chunk'), data: fileTransferChunkSchema }),
  z.object({ type: z.literal('file.transfer.progress'), data: fileTransferProgressSchema }),
  z.object({ type: z.literal('file.transfer.complete'), data: z.object({ requestId: z.string().trim().min(1).max(64) }).strict() }),
  z.object({ type: z.literal('file.transfer.failed'), data: z.object({ requestId: z.string().trim().min(1).max(64), code: z.string().max(64), reason: z.string().max(280) }).strict() }),
  z.object({ type: z.literal('remote.capabilities'), data: remoteCapabilitiesSchema }),
  z.object({ type: z.literal('remote.session.result'), data: remoteAgentResultSchema }),
  z.object({
    type: z.literal('applications'),
    data: z.object({
      deviceId: z.string().trim().min(1).max(64),
      applications: z.array(applicationDescriptorSchema).max(PROCESS_HARD_CAP),
      timestamp: z.string().datetime(),
    }),
  }),
  z.object({
    type: z.literal('storage'),
    data: storageSummarySchema.extend({
      deviceId: z.string().trim().min(1).max(64),
    }),
  }),
  z.object({
    type: z.literal('clean.result'),
    data: cleanResultFrameSchema,
  }),
  z.object({
    type: z.literal('cleanup'),
    data: z.object({
      deviceId: z.string().trim().min(1).max(64),
      actions: z.array(cleanActionDescriptorSchema).max(STORAGE_CATEGORY_CAP),
      recommendations: z.array(cleanupRecommendationSchema).max(STORAGE_CATEGORY_CAP),
      timestamp: z.string().datetime(),
    }),
  }),
  // Phase 5: the device's owner-presence claim and its security events. Both
  // are agent-only frames; the deviceId rebind check in the ws handler runs
  // before either is parsed, and the schemas are strict.
  z.object({
    type: z.literal('security.state'),
    data: ownerPresenceSchema,
  }),
  z.object({
    type: z.literal('security.event'),
    data: securityEventSchema,
  }),
]);

export type RegisterDeviceInput = z.infer<typeof registerDeviceSchema>;
export type HeartbeatInput = z.infer<typeof heartbeatSchema>;
export type TelemetryInput = z.infer<typeof telemetrySchema>;
export type ProcessSnapshotInput = z.infer<typeof processSnapshotSchema>;
export type ProcessInfoInput = z.infer<typeof processInfoSchema>;
export type CommandRequestInput = z.infer<typeof commandRequestSchema>;
export type ApplicationCommandRequestInput = z.infer<typeof applicationCommandRequestSchema>;
export type ApplicationDescriptorInput = z.infer<typeof applicationDescriptorSchema>;
export type StorageCategoryInput = z.infer<typeof storageCategorySchema>;
export type CleanActionInput = z.infer<typeof cleanActionSchema>;
export type CleanActionDescriptorInput = z.infer<typeof cleanActionDescriptorSchema>;
export type CleanResultInput = z.infer<typeof cleanResultSchema>;
export type FileRequestInput = z.infer<typeof fileRequestSchema>;
export type CleanupRecommendationInput = z.infer<typeof cleanupRecommendationSchema>;
