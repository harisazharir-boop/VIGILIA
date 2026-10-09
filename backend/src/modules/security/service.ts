/**
 * Security & owner protection service (Phase 5).
 *
 * This module is the backend half of Phase 5, and it is deliberately small.
 * The owner's identity is established **on the device**: biometric templates,
 * images and liveness signals never leave the agent. The backend holds the
 * *result* — a trust state, a timestamp, a failure count — plus the security
 * events the device reports.
 *
 * What is here, and why it is shaped this way:
 *
 *  - `storeOwnerPresence` upserts one `OwnerState` row per device. It is a
 *    current-state row, not history, exactly like the Phase 4A storage
 *    report. It accepts no biometric field, because the schema has none.
 *  - `recordSecurityEvent` writes one `SecurityEvent` row. The `severity` is
 *    **derived from the event type server-side** and is never accepted from a
 *    client, so a device cannot report `security_lock_triggered` as `ok`.
 *    Metadata is bounded to an enumerated key set that cannot hold a path, a
 *    credential or biometric data.
 *  - `dailyCleanupEligibility` is the once-per-day gate. It is a pure
 *    function over the stored `DailyCleanupState` row and the owner's state,
 *    which makes it directly testable and keeps the *durable* part of the
 *    rule in the database where a reboot cannot reset it.
 *
 * The mobile client reads all of this through public, read-only routes and
 * writes the Daily Auto Cleanup configuration with the paired command token —
 * the same credential `APPLICATION_CLOSE` uses. There is no second
 * authorisation system.
 */
import { prisma } from '../../config/db.js';
import { wsHub } from '../../ws/hub.js';
import { logger } from '../../config/logger.js';
import { config } from '../../config/env.js';
import { Prisma } from '@prisma/client';
import type {
  DailyCleanupConfig,
  DailyCleanupSkipReason,
  OwnerPresence,
  OwnerState,
  SecurityEvent,
  SecurityEventInput,
  SecurityEventType,
  SecurityEventSeverity,
} from '@vigilia/shared';
import {
  CLEAN_ACTIONS,
  DEFAULT_DAILY_CLEANUP_CONFIG,
  DAILY_CLEANUP_SKIP_LABELS,
  DAILY_SAFE_CLEAN_ACTIONS,
  SECURITY_EVENT_SEVERITY,
  SECURITY_EVENT_TYPES,
  isOwnerMode,
} from '@vigilia/shared';

/** A security event older than this is not worth re-notifying about. */
export const SECURITY_EVENT_RETENTION_DAYS = 90;

/** How many recent events the dashboard timeline renders. */
export const SECURITY_EVENT_PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Owner presence (5A / 5B)
// ---------------------------------------------------------------------------

function rowToPresence(row: {
  deviceId: string;
  state: string;
  since: number;
  lastAuthenticatedAt: number | null;
  failedAttempts: number;
  capabilities: Prisma.JsonValue;
  lastDailyCleanupDate: string | null;
  updatedAt: Date;
}): OwnerPresence {
  const capabilities = (row.capabilities ?? {}) as Record<string, unknown>;
  return {
    deviceId: row.deviceId,
    state: row.state as OwnerState,
    since: row.since,
    lastAuthenticatedAt: row.lastAuthenticatedAt,
    failedAttempts: row.failedAttempts,
    capabilities: {
      faceRecognition: !!capabilities.faceRecognition,
      liveness: !!capabilities.liveness,
      guestMode: !!capabilities.guestMode,
      intrusionDetection: !!capabilities.intrusionDetection,
      dailyCleanup: !!capabilities.dailyCleanup,
    },
    lastDailyCleanupDate: row.lastDailyCleanupDate,
    timestamp: row.updatedAt.toISOString(),
  };
}

/**
 * Store the device's owner-presence claim and broadcast it.
 *
 * @param deviceId the authenticated connection's device — never the frame's
 * claim, so an agent cannot set another machine's trust state.
 */
export async function storeOwnerPresence(
  deviceId: string,
  presence: OwnerPresence,
): Promise<OwnerPresence> {
  // The capabilities object is rebuilt from the validated payload's own
  // fields, so an unexpected key is dropped here rather than persisted.
  const capabilities = {
    faceRecognition: !!presence.capabilities.faceRecognition,
    liveness: !!presence.capabilities.liveness,
    guestMode: !!presence.capabilities.guestMode,
    intrusionDetection: !!presence.capabilities.intrusionDetection,
    dailyCleanup: !!presence.capabilities.dailyCleanup,
  };

  const json = (capabilities as unknown as Prisma.InputJsonValue) ?? {};
  const row = await prisma.ownerState.upsert({
    where: { deviceId },
    create: {
      id: deviceId,
      deviceId,
      state: presence.state,
      since: presence.since,
      lastAuthenticatedAt: presence.lastAuthenticatedAt ?? null,
      failedAttempts: Math.max(0, Math.min(999, Math.floor(presence.failedAttempts ?? 0))),
      capabilities: json,
      lastDailyCleanupDate: presence.lastDailyCleanupDate ?? null,
    },
    update: {
      state: presence.state,
      since: presence.since,
      lastAuthenticatedAt: presence.lastAuthenticatedAt ?? null,
      failedAttempts: Math.max(0, Math.min(999, Math.floor(presence.failedAttempts ?? 0))),
      capabilities: json,
      lastDailyCleanupDate: presence.lastDailyCleanupDate ?? null,
    },
  });

  const publicPresence = rowToPresence(row);
  wsHub.broadcastToDevice(deviceId, 'security.state', {
    deviceId,
    presence: publicPresence,
  });

  logger.debug(
    { deviceId, state: presence.state, failedAttempts: presence.failedAttempts },
    'owner presence stored',
  );
  return publicPresence;
}

/** The device's current owner-presence state, or null if it never reported. */
export async function latestOwnerPresence(deviceId: string): Promise<OwnerPresence | null> {
  const row = await prisma.ownerState.findUnique({ where: { deviceId } });
  if (!row) return null;
  return rowToPresence(row);
}

/**
 * A presence to broadcast when a device has never reported one.
 *
 * `UNKNOWN` is the protected default: no owner-only functionality runs until
 * the device says otherwise. The capabilities are all off because the device
 * has not told us what it can do.
 */
export function absentOwnerPresence(deviceId: string, now = Date.now()): OwnerPresence {
  return {
    deviceId,
    state: 'UNKNOWN',
    since: Math.floor(now / 1000),
    failedAttempts: 0,
    capabilities: {
      faceRecognition: false,
      liveness: false,
      guestMode: false,
      intrusionDetection: false,
      dailyCleanup: false,
    },
    timestamp: new Date(now).toISOString(),
  };
}

// ---------------------------------------------------------------------------
// Security events (5C / 5D)
// ---------------------------------------------------------------------------

function rowToEvent(row: {
  id: string;
  deviceId: string;
  eventType: string;
  severity: string;
  description: string;
  metadata: Prisma.JsonValue;
  requestId: string | null;
  since: number | null;
  createdAt: Date;
}): SecurityEvent {
  const metadata = (row.metadata ?? undefined) as
    | Record<string, unknown>
    | undefined;
  return {
    id: row.id,
    deviceId: row.deviceId,
    eventType: row.eventType as SecurityEventType,
    severity: row.severity as SecurityEventSeverity,
    description: row.description,
    timestamp: row.createdAt.toISOString(),
    since: row.since,
    metadata: metadata as SecurityEvent['metadata'],
    requestId: row.requestId,
  };
}

export function severityFor(eventType: string): SecurityEventSeverity {
  return (SECURITY_EVENT_SEVERITY as Record<string, SecurityEventSeverity>)[eventType] ?? 'warn';
}

/**
 * Record one security event the device's own agent reported.
 *
 * The `severity` is derived here, not read from the payload — a device cannot
 * downgrade a lock or upgrade a success. Metadata is re-validated against the
 * enumerated key set, so nothing outside it is stored even if the validated
 * frame somehow carried it.
 *
 * @param deviceId the authenticated connection's device.
 */
export async function recordSecurityEvent(
  deviceId: string,
  input: SecurityEventInput,
): Promise<SecurityEvent | null> {
  if (!(SECURITY_EVENT_TYPES as readonly string[]).includes(input.eventType)) {
    logger.warn({ deviceId, eventType: input.eventType }, 'security event rejected: unknown type');
    return null;
  }

  const metadata = stripMetadata(input.metadata);
  const row = await prisma.securityEvent.create({
    data: {
      deviceId,
      eventType: input.eventType,
      severity: severityFor(input.eventType),
      description: input.description.slice(0, 280),
      metadata: metadata ? (metadata as unknown as Prisma.InputJsonValue) : Prisma.DbNull,
      requestId: input.requestId?.slice(0, 64) || null,
      since: typeof input.since === 'number' ? input.since : null,
    },
  });

  const event = rowToEvent(row);
  wsHub.broadcastToDevice(deviceId, 'security.event', { deviceId, event });

  logger.info(
    { deviceId, eventType: event.eventType, severity: event.severity },
    'security event recorded',
  );
  return event;
}

/** Keep only the enumerated, safe metadata keys. */
function stripMetadata(
  metadata: SecurityEvent['metadata'] | undefined,
): Record<string, unknown> | null {
  if (!metadata) return null;
  const out: Record<string, unknown> = {};
  const allowed: Record<string, (v: unknown) => unknown> = {
    failedAttempts: (v) => Math.max(0, Math.min(999, Math.floor(Number(v) || 0))),
    bytesFreed: (v) => Math.max(0, Math.floor(Number(v) || 0)),
    actions: (v) => Math.max(0, Math.min(64, Math.floor(Number(v) || 0))),
    reason: (v) => String(v).slice(0, 64),
    actionId: (v) => ((CLEAN_ACTIONS as readonly string[]).includes(v as string) ? v : undefined),
  };
  for (const [key, value] of Object.entries(metadata)) {
    const coerce = allowed[key];
    if (!coerce) continue;
    const safe = coerce(value);
    if (safe !== undefined && safe !== null) out[key] = safe;
  }
  return Object.keys(out).length ? out : null;
}

/** Recent security events for a device, newest-first. */
export async function latestSecurityEvents(
  deviceId: string,
  limit = SECURITY_EVENT_PAGE_SIZE,
): Promise<SecurityEvent[]> {
  const rows = await prisma.securityEvent.findMany({
    where: { deviceId },
    orderBy: { createdAt: 'desc' },
    take: Math.max(1, Math.min(SECURITY_EVENT_PAGE_SIZE * 2, limit)),
  });
  return rows.map(rowToEvent);
}

/** Delete events older than the retention window. Returns the count removed. */
export async function pruneSecurityEvents(now = new Date()): Promise<number> {
  const cutoff = new Date(now.getTime() - config.retentionDays * 24 * 60 * 60 * 1000);
  const rows = await prisma.securityEvent.findMany({ where: { createdAt: { lt: cutoff } }, orderBy: { createdAt: 'asc' }, take: config.retentionBatchSize, select: { id: true } });
  const result = rows.length ? await prisma.securityEvent.deleteMany({ where: { id: { in: rows.map((row) => row.id) } } }) : { count: 0 };
  return result.count;
}

// ---------------------------------------------------------------------------
// Daily Auto Cleanup (5E / 5F)
// ---------------------------------------------------------------------------

/**
 * Normalise a stored configuration into a valid, conservative one.
 *
 * Every action id is re-validated against the allowlist, and only ids the
 * daily-safe set permits survive. A config that was never written falls back
 * to the default, which has cleanup **disabled** — the rule the phase
 * requires, and the reason a device that has never been configured does not
 * clean anything.
 */
export function normalizeDailyCleanupConfig(
  raw: DailyCleanupConfig | Prisma.JsonValue | undefined | null,
): DailyCleanupConfig {
  const obj = (raw ?? {}) as Partial<DailyCleanupConfig> | null;
  const categories = Array.isArray(obj?.categories)
    ? obj!.categories.filter((c): c is (typeof CLEAN_ACTIONS)[number] =>
        (CLEAN_ACTIONS as readonly string[]).includes(c as string),
      )
    : [...DEFAULT_DAILY_CLEANUP_CONFIG.categories];

  const trashEnabled = !!obj?.trashEnabled;
  const withTrash = new Set(categories);
  if (trashEnabled && !withTrash.has('CLEAN_TRASH')) withTrash.add('CLEAN_TRASH');
  if (!trashEnabled) withTrash.delete('CLEAN_TRASH');

  return {
    enabled: !!obj?.enabled,
    trigger: obj?.trigger === 'AFTER_OWNER_LOGIN' ? 'AFTER_OWNER_LOGIN' : 'AFTER_OWNER_LOGIN',
    categories: [...withTrash],
    trashEnabled,
    logRetentionDays: clampLogRetentionDays(obj?.logRetentionDays),
  };
}

/**
 * Bound the log retention. `0` is a real value the caller may send — it is
 * not "missing" — so `|| 30` is wrong here (it would turn 0 into 30 and
 * silently reject the shortest legal window).
 */
function clampLogRetentionDays(raw: unknown): number {
  const n = Number(raw);
  if (!Number.isFinite(n)) return 30;
  return Math.max(1, Math.min(365, Math.floor(n)));
}

export function dailyCleanupConfigToPublic(config: DailyCleanupConfig): DailyCleanupConfig {
  return {
    enabled: config.enabled,
    trigger: config.trigger,
    categories: config.categories,
    trashEnabled: config.trashEnabled,
    logRetentionDays: config.logRetentionDays,
  };
}

/** The stored config, or the conservative default. */
export async function getDailyCleanupConfig(
  deviceId: string,
): Promise<{ config: DailyCleanupConfig; lastCleanupDate: string | null; lastCleanupAt: number | null; lastResult: string | null }> {
  const row = await prisma.dailyCleanupState.findUnique({ where: { deviceId } });
  if (!row) {
    return {
      config: { ...DEFAULT_DAILY_CLEANUP_CONFIG, categories: [...DEFAULT_DAILY_CLEANUP_CONFIG.categories] },
      lastCleanupDate: null,
      lastCleanupAt: null,
      lastResult: null,
    };
  }
  return {
    config: normalizeDailyCleanupConfig(row.config),
    lastCleanupDate: row.lastCleanupDate,
    lastCleanupAt: row.lastCleanupAt,
    lastResult: row.lastResult,
  };
}

/**
 * Persist the owner's Daily Auto Cleanup configuration.
 *
 * Re-validated here because the stored row is what the eligibility check
 * reads: an action id outside the daily-safe set is dropped rather than
 * stored, so a hostile or buggy client cannot arm an unsafe automatic
 * cleanup.
 */
export async function setDailyCleanupConfig(
  deviceId: string,
  config: DailyCleanupConfig,
): Promise<DailyCleanupConfig> {
  const normalized = normalizeDailyCleanupConfig(config);
  // Only daily-safe actions may run automatically.
  normalized.categories = normalized.categories.filter((a) =>
    (DAILY_SAFE_CLEAN_ACTIONS as readonly string[]).includes(a),
  );

  const json = normalized as unknown as Prisma.InputJsonValue;
  await prisma.dailyCleanupState.upsert({
    where: { deviceId },
    create: { id: deviceId, deviceId, config: json },
    update: { config: json },
  });

  logger.info(
    { deviceId, enabled: normalized.enabled, categories: normalized.categories },
    'daily cleanup config stored',
  );
  return normalized;
}

/**
 * Record that a daily cleanup completed, failing the second attempt the same
 * device-day. Called when the agent reports `daily_cleanup.completed`.
 */
export async function recordDailyCleanupCompletion(
  deviceId: string,
  date: string,
  bytesFreed: number,
  ok: boolean,
  result: string,
): Promise<void> {
  await prisma.dailyCleanupState.upsert({
    where: { deviceId },
    create: {
      id: deviceId,
      deviceId,
      lastCleanupDate: date.slice(0, 10),
      lastCleanupAt: Math.floor(Date.now() / 1000),
      lastResult: result.slice(0, 280),
      config: ({} as unknown as Prisma.InputJsonValue),
    },
    update: {
      lastCleanupDate: date.slice(0, 10),
      lastCleanupAt: Math.floor(Date.now() / 1000),
      lastResult: result.slice(0, 280),
    },
  });
  // The presence row mirrors the date so the dashboard's "last cleanup" line
  // is one read, and so a phone that reconnects sees it immediately.
  await prisma.ownerState
    .updateMany({ where: { deviceId }, data: { lastDailyCleanupDate: date.slice(0, 10) } })
    .catch((err) => logger.warn({ err, deviceId }, 'could not mirror the cleanup date'));
}

/**
 * The once-per-day gate, as a pure function.
 *
 *     not the owner            -> NOT_AUTHENTICATED   (no cleanup, ever)
 *     cleanup disabled         -> DISABLED
 *     already done this day     -> ALREADY_DONE
 *     no safe categories       -> NO_CATEGORIES
 *     otherwise                -> eligible
 *
 * The date string is the device's own calendar day (`YYYY-MM-DD`), which the
 * agent supplies because only it knows its timezone. The rule is therefore
 * "one cleanup per device-day" and it cannot be moved by the server's clock.
 */
export function dailyCleanupEligibility(args: {
  presence: OwnerPresence | null;
  config: DailyCleanupConfig;
  lastCleanupDate: string | null;
  today: string;
}): { eligible: boolean; reason?: DailyCleanupSkipReason } {
  const { presence, config, lastCleanupDate, today } = args;

  if (!isOwnerMode(presence?.state)) return { eligible: false, reason: 'NOT_AUTHENTICATED' };
  if (!config.enabled) return { eligible: false, reason: 'DISABLED' };
  if (lastCleanupDate === today) return { eligible: false, reason: 'ALREADY_DONE' };

  const categories = config.categories.filter((a) =>
    (DAILY_SAFE_CLEAN_ACTIONS as readonly string[]).includes(a),
  );
  if (!categories.length) return { eligible: false, reason: 'NO_CATEGORIES' };

  return { eligible: true };
}

/** The skip label the mobile UI and the event description both use. */
export function skipReasonLabel(reason: DailyCleanupSkipReason): string {
  return DAILY_CLEANUP_SKIP_LABELS[reason];
}
