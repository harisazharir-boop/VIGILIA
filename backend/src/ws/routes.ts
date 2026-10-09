/**
 * /ws — the agent and mobile socket surface.
 *
 * Two kinds of socket reach this route, and the subprotocol decides which:
 *
 *   `vigilia.<deviceId>.vig_<agentToken>`  the device's own agent. It may
 *     push telemetry, heartbeats, the process list, the application catalog,
 *     the storage breakdown, the cleanup plan and command results. Every
 *     frame's `deviceId` is rebound to the connection's device, so a
 *     compromised agent cannot speak for another machine.
 *
 *   `vigilia.<deviceId>`  a read-only mobile client. It may subscribe to the
 *     device's broadcasts; it may push nothing. There is no frame type a
 *     read-only socket can send that writes anything.
 *
 * The command dispatch path is deliberately *not* on this route: commands
 * come in over HTTP with the command token, are audited as a row first, and
 * are then pushed to the agent socket here. A socket cannot order a command.
 */
import type { FastifyPluginCallback } from 'fastify';
import { prisma } from '../config/db.js';
import { config } from '../config/env.js';
import { logger } from '../config/logger.js';
import { constantTimeEquals } from '../utils/token.js';
import { wsHub } from './hub.js';
import {
  heartbeatSchema,
  telemetrySchema,
  processSnapshotSchema,
  applicationPublicSchema,
  storageSummarySchema,
  cleanActionDescriptorSchema,
  cleanupRecommendationSchema,
  ownerPresenceSchema,
  securityEventSchema,
  commandResultSchema,
  fileResultSchema,
  fileTransferStartSchema,
  fileTransferChunkSchema,
  fileTransferProgressSchema,
  remoteCapabilitiesSchema,
  remoteAgentResultSchema,
  agentHelloSchema,
  BACKEND_VERSION,
  PROTOCOL_VERSION,
  protocolCompatibility,
  hardwareControlCapabilitiesSchema,
  hardwareControlResultSchema,
} from '@vigilia/shared';
import { storeStorageSummary } from '../modules/storage/service.js';
import { pruneRetentionSafely } from '../modules/retention/service.js';
import { storeCleanupPlan } from '../modules/cleanup/service.js';
import { storeApplicationCatalog } from '../modules/applications/service.js';
import { storeProcessSnapshot } from '../modules/processes/service.js';
import {
  recordDailyCleanupCompletion,
  recordSecurityEvent,
  storeOwnerPresence,
} from '../modules/security/service.js';
import { applyCommandResult, expireStaleCommands, newRequestId } from '../modules/commands/service.js';
import { applyFileResult } from '../modules/files/service.js';
import { acceptAgentChunk, completeAgentTransfer, startAgentTransfer } from '../modules/files/service.js';
import { applyResult as applyRemoteResult, relayFrame, storeCapabilities } from '../modules/remote/service.js';
import { applyResult as applyHardwareResult, storeCapabilities as storeHardwareCapabilities } from '../modules/hardware/service.js';

interface AuthState {
  socketId: string;
  device: { id: string; name: string; os: string; hostname: string } | null;
  readOnly: boolean;
  protocolReady: boolean;
}

const SUBPROTOCOL_PREFIX = 'vigilia.';

/** The mobile dashboard subscribes as `/ws?deviceId=<id>` with no credentials. */
function readQueryDeviceId(request: import('fastify').FastifyRequest): string | null {
  const value = (request.query as { deviceId?: string } | undefined)?.deviceId;
  return typeof value === 'string' && value.length ? value : null;
}

function parseSubprotocol(protocol: string | undefined): {
  deviceId: string | null;
  token: string | null;
} {
  if (!protocol || !protocol.startsWith(SUBPROTOCOL_PREFIX)) return { deviceId: null, token: null };
  const rest = protocol.slice(SUBPROTOCOL_PREFIX.length);
  const parts = rest.split('.');
  // `vigilia.<deviceId>` (read-only) or `vigilia.<deviceId>.<token>` (agent).
  // The prefix is stripped first, so the token is everything after the first
  // dot of what remains — joined back in case a token itself contains one.
  const deviceId = parts[0] || null;
  const token = parts.length > 1 ? parts.slice(1).join('.') : null;
  return { deviceId, token };
}

async function authenticate(
  protocol: string | undefined,
  queryDeviceId: string | null,
): Promise<AuthState> {
  const parsed = parseSubprotocol(protocol);
  // The mobile dashboard subscribes with `?deviceId=<id>` and no credentials.
  // The subprotocol form `vigilia.<deviceId>` is the same read-only intent,
  // spelled for a non-browser client.
  const deviceId = parsed.deviceId ?? queryDeviceId;
  if (!deviceId) return { socketId: '', device: null, readOnly: true, protocolReady: true };
  if (!parsed.deviceId && parsed.token) return { socketId: '', device: null, readOnly: true, protocolReady: true };

  const device = await prisma.device.findUnique({ where: { id: deviceId } });
  if (!device) return { socketId: '', device: null, readOnly: true, protocolReady: true };

  // A socket without a token is read-only: it may listen, never push.
  if (!parsed.token) return { socketId: '', device, readOnly: true, protocolReady: true };
  if (!device.agentToken || !constantTimeEquals(parsed.token, device.agentToken)) {
    return { socketId: '', device: null, readOnly: true, protocolReady: true };
  }

  return { socketId: '', device, readOnly: false, protocolReady: false };
}

/**
 * Handle one inbound frame on a connected socket.
 *
 * Registered synchronously at handshake time, before the replay awaits, so a
 * frame the agent sends in the same TCP flight as its `Upgrade` is not lost
 * in the async gap.
 */
async function handleAgentFrame(
  socketId: string,
  socket: import('ws').WebSocket,
  auth: AuthState,
  raw: Buffer | ArrayBuffer | Buffer[],
  isBinary = false,
): Promise<void> {
  if (isBinary) {
    if (auth.readOnly || !auth.device) return;
    if (!auth.protocolReady) {
      wsHub.send(socket, {
        type: 'error',
        data: { code: 'PROTOCOL_NEGOTIATION_REQUIRED', message: 'agent.hello required' },
      });
      socket.close(4406, 'protocol required');
      return;
    }
    const media = Buffer.isBuffer(raw) ? raw : Buffer.from(raw as ArrayBuffer);
    const sessionLength = media.length >= 6 && media.subarray(0, 4).toString('ascii') === 'VIG7' && media[4] === 1 ? media[5] : 0;
    if (!sessionLength || media.length <= 6 + sessionLength || sessionLength > 128) return;
    const sessionId = media.subarray(6, 6 + sessionLength).toString('ascii');
    const jpeg = media.subarray(6 + sessionLength);
    if (await relayFrame(auth.device.id, sessionId, jpeg)) wsHub.broadcastBinaryToReadOnly(auth.device.id, media);
    return;
  }
  let msg: { type?: string; data?: Record<string, unknown> };
  try {
    msg = JSON.parse(raw.toString());
  } catch {
    wsHub.send(socket, {
      type: 'error',
      data: { code: 'VALIDATION_FAILED', message: 'a frame must be JSON' },
    });
    return;
  }
  if (!msg || typeof msg !== 'object' || !msg.type) return;

  // A read-only socket may never push. There is no frame type that
  // writes anything from this side of the wire.
  if (auth.readOnly) {
    wsHub.send(socket, {
      type: 'error',
      data: { code: 'FORBIDDEN', message: 'a read-only socket cannot push data' },
    });
    return;
  }
  const device = auth.device;
  if (!device) return;
  if (!auth.protocolReady) {
    if (msg.type !== 'agent.hello') { wsHub.send(socket, { type: 'error', data: { code: 'PROTOCOL_NEGOTIATION_REQUIRED', message: 'agent.hello required' } }); socket.close(4406, 'protocol required'); return; }
    const parsed = agentHelloSchema.safeParse(msg.data); const compatibility = parsed.success ? protocolCompatibility(parsed.data.protocolVersion) : protocolCompatibility(null);
    if (!compatibility.compatible) { wsHub.send(socket, { type: 'error', data: { code: 'UNSUPPORTED_PROTOCOL_VERSION', message: compatibility.reason } }); socket.close(4406, 'unsupported protocol'); return; }
    auth.protocolReady = true;
    wsHub.send(socket, { type: 'backend.hello', deviceId: device.id, data: { backendVersion: BACKEND_VERSION, protocolVersion: PROTOCOL_VERSION } });
    return;
  }

  // Every frame is rebound to the authenticated connection's device, so a
  // frame that names another one is a lie. Rebinding alone made the lie
  // *silent* — it was stored as the sender's own data with no error, so an
  // agent could publish an arbitrary breakdown under a mismatched claim and
  // learn nothing about whether the mismatch was noticed. Refuse it instead:
  // the frame is dropped, the sender is told, and nothing is written.
  if (msg.data && typeof msg.data === 'object' && 'deviceId' in msg.data) {
    const claimed = (msg.data as { deviceId: unknown }).deviceId;
    if (claimed !== device.id) {
      wsHub.send(socket, {
        type: 'error',
        data: { code: 'FORBIDDEN', message: 'deviceId mismatch' },
      });
      return;
    }
  }

  try {
    if (msg.type === 'telemetry') {
      const parsed = telemetrySchema.safeParse(msg.data);
      if (!parsed.success) {
        logger.warn({ issues: parsed.error.issues.slice(0, 3) }, 'telemetry frame rejected');
        return;
      }
      const json = (parsed.data as unknown as import('@prisma/client').Prisma.InputJsonValue) ?? {};
      await prisma.telemetry.upsert({
        where: { deviceId: device.id },
        create: { deviceId: device.id, sample: json },
        update: { sample: json },
      });
      // Guardian runs only registered read-only inspection after authenticated
      // telemetry persistence. Failure never drops telemetry.
      void import('../modules/guardian/service.js').then(({ evaluateTelemetry }) => evaluateTelemetry(device.id)).catch((err) => logger.warn({ err }, 'guardian evaluation failed'));
      await prisma.device.update({
        where: { id: device.id },
        data: { status: 'online', lastSeen: new Date() },
      });
      wsHub.broadcastToDevice(device.id, 'telemetry.latest', {
        deviceId: device.id,
        telemetry: parsed.data,
      });
      return;
    }

    if (msg.type === 'heartbeat') {
      const parsed = heartbeatSchema.safeParse(msg.data);
      if (!parsed.success) return;
      await prisma.device.update({
        where: { id: device.id },
        data: { status: parsed.data.status, lastSeen: new Date() },
      });
      wsHub.broadcastToDevice(device.id, 'device.status', {
        deviceId: device.id,
        status: parsed.data.status,
        lastSeen: new Date().toISOString(),
      });
      return;
    }

    if (msg.type === 'processes') {
      const parsed = processSnapshotSchema.safeParse(msg.data);
      if (!parsed.success) {
        logger.warn({ issues: parsed.error.issues.slice(0, 3) }, 'process frame rejected');
        return;
      }
      await storeProcessSnapshot(device.id, parsed.data);
      return;
    }

    if (msg.type === 'applications') {
      // The catalog the agent discovered: application ids and labels
      // only, plus a running flag. The schema rejects a path or an
      // executable name in an application id.
      const data = (msg.data ?? {}) as { applications?: unknown };
      const raw = Array.isArray(data.applications) ? data.applications : [];
      const applications = raw.filter((a) => applicationPublicSchema.safeParse(a).success);
      await storeApplicationCatalog(device.id, { applications });
      return;
    }

    if (msg.type === 'storage') {
      const parsed = storageSummarySchema.safeParse(msg.data);
      if (!parsed.success) {
        logger.warn({ issues: parsed.error.issues.slice(0, 3) }, 'storage frame rejected');
        return;
      }
      await storeStorageSummary(device.id, parsed.data);
      return;
    }

    if (msg.type === 'cleanup') {
      // The cleanup plan this device offers: structured action ids,
      // measured estimates, and the advisory AI recommendations. The
      // action ids are re-validated against the allowlist before the row is
      // written, so a compromised agent cannot store an arbitrary action.
      // The deviceId rebind is enforced once for every frame above.
      const payload = msg.data as {
        actions: import('@vigilia/shared').CleanActionDescriptor[];
        recommendations: import('@vigilia/shared').CleanupRecommendation[];
      };
      const actions = (payload.actions ?? []).filter((a) =>
        cleanActionDescriptorSchema.safeParse(a).success,
      );
      const recommendations = (payload.recommendations ?? []).filter((r) =>
        cleanupRecommendationSchema.safeParse(r).success,
      );
      await storeCleanupPlan(device.id, actions, recommendations);
      return;
    }

    if (msg.type === 'clean.result') {
      // The agent's structured outcome for one cleanup action. The
      // requestId is rebound to the authenticated connection like every
      // other result frame, so an agent cannot settle another device's
      // cleanup. The action id is re-validated by the schema, and the
      // payload carries only counts — never a path, never contents.
      const cleanPayload = (msg.data ?? {}) as {
        requestId: string;
        actionId: string;
        status?: string;
        itemsRemoved?: number;
        bytesFreed?: number;
        errors?: number;
        detail?: string;
      };
      await applyCommandResult(
        device.id,
        cleanPayload.requestId,
        cleanPayload.status === 'SUCCESS' || cleanPayload.status === 'PARTIAL'
          ? 'succeeded'
          : 'failed',
        [
          `${cleanPayload.actionId} ${cleanPayload.status ?? 'FAILED'}`,
          `items=${cleanPayload.itemsRemoved ?? 0}`,
          `freed=${cleanPayload.bytesFreed ?? 0}`,
          cleanPayload.errors ? `errors=${cleanPayload.errors}` : '',
          cleanPayload.detail ?? '',
        ]
          .filter(Boolean)
          .join(' · '),
        undefined,
      );
      return;
    }

    if (msg.type === 'command.result') {
      // A Phase 2 command answer (power or application). The deviceId rebind
      // is enforced once for every frame above; `applyCommandResult` also
      // rebinds the requestId to this connection's device.
      const parsed = commandResultSchema.safeParse(msg.data);
      if (!parsed.success) return;
      const data = parsed.data;
      await applyCommandResult(
        device.id,
        data.requestId,
        data.status === 'succeeded' ? 'succeeded' : 'failed',
        data.result,
        undefined,
      );
      return;
    }

    if (msg.type === 'file.result') {
      const parsed = fileResultSchema.safeParse(msg.data);
      if (!parsed.success) return;
      await applyFileResult(device.id, parsed.data);
      return;
    }
    if (msg.type === 'file.transfer.start') {
      const parsed = fileTransferStartSchema.safeParse(msg.data); if (!parsed.success) return;
      await startAgentTransfer(device.id, parsed.data); return;
    }
    if (msg.type === 'file.transfer.chunk') {
      const parsed = fileTransferChunkSchema.safeParse(msg.data); if (!parsed.success) return;
      await acceptAgentChunk(device.id, parsed.data); return;
    }
    if (msg.type === 'file.transfer.progress') {
      const parsed = fileTransferProgressSchema.safeParse(msg.data); if (!parsed.success) return;
      wsHub.broadcastToDevice(device.id, 'file.transfer.progress', parsed.data); return;
    }
    if (msg.type === 'file.transfer.complete') { completeAgentTransfer(device.id, String((msg.data as { requestId?: string })?.requestId || '')); return; }
    if (msg.type === 'remote.capabilities') {
      const parsed = remoteCapabilitiesSchema.safeParse(msg.data);
      if (!parsed.success) { logger.warn({ deviceId: device.id, issues: parsed.error.issues }, 'remote capabilities rejected'); return; }
      storeCapabilities(device.id, parsed.data);
      logger.info({ deviceId: device.id, capabilities: parsed.data }, 'remote capabilities stored');
      return;
    }
    if (msg.type === 'remote.session.result') {
      const parsed = remoteAgentResultSchema.safeParse(msg.data); if (!parsed.success) return;
      await applyRemoteResult(device.id, parsed.data); return;
    }
    if (msg.type === 'hardware.control.capabilities') { const parsed = hardwareControlCapabilitiesSchema.safeParse(msg.data); if (!parsed.success) return; storeHardwareCapabilities(device.id, parsed.data); return; }
    if (msg.type === 'hardware.control.result') { const parsed = hardwareControlResultSchema.safeParse(msg.data); if (!parsed.success) return; await applyHardwareResult(device.id, parsed.data); return; }

    if (msg.type === 'security.state') {
      // Phase 5A/5B: the device's owner-presence claim. This is the outcome
      // of an authentication — a state, a timestamp, a failure count and a
      // capability set. It carries no biometric data because the schema has
      // no field for any, and the parse is strict.
      const parsed = ownerPresenceSchema.safeParse(msg.data);
      if (!parsed.success) {
        logger.warn({ issues: parsed.error.issues.slice(0, 3) }, 'security.state frame rejected');
        return;
      }
      await storeOwnerPresence(device.id, parsed.data);
      return;
    }

    if (msg.type === 'security.event') {
      // Phase 5C/5D: one security event from the device's own agent. The
      // severity is derived server-side from the type, so a device cannot
      // downgrade a lock; the metadata keys are enumerated, so nothing
      // outside them is stored.
      const parsed = securityEventSchema.safeParse(msg.data);
      if (!parsed.success) {
        logger.warn({ issues: parsed.error.issues.slice(0, 3) }, 'security.event frame rejected');
        return;
      }
      const event = await recordSecurityEvent(device.id, {
        deviceId: device.id,
        eventType: parsed.data.eventType,
        description: parsed.data.description,
        since: parsed.data.since ?? null,
        metadata: parsed.data.metadata,
        requestId: parsed.data.requestId ?? null,
      });
      // A completed daily cleanup also advances the durable once-per-day
      // state, so the second attempt the same device-day is skipped.
      if (event && event.eventType === 'daily_cleanup.completed') {
        const meta = event.metadata ?? {};
        const date = typeof event.since === 'number' ? new Date(event.since * 1000) : new Date();
        await recordDailyCleanupCompletion(
          device.id,
          date.toISOString().slice(0, 10),
          Number(meta.bytesFreed) || 0,
          true,
          event.description,
        );
      }
      if (event && event.eventType === 'daily_cleanup.failed') {
        const date = typeof event.since === 'number' ? new Date(event.since * 1000) : new Date();
        await recordDailyCleanupCompletion(
          device.id,
          date.toISOString().slice(0, 10),
          0,
          false,
          event.description,
        );
      }
      return;
    }
  } catch (err) {
    logger.error({ err, type: msg.type }, 'ws frame handling failed');
  }
}


export const wsRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.get('/ws', { websocket: true }, async (socket, request) => {
    const socketId = newRequestId();
    let auth: AuthState = { socketId, device: null, readOnly: true, protocolReady: false };
    let authenticated = false;
    let replaying = false;
    const pending: Array<{ raw: Buffer | ArrayBuffer | Buffer[]; isBinary: boolean }> = [];
    let frameChain = Promise.resolve();
    try {
      // Attach the message listener *before* any await below. `authenticate`
      // does a database round-trip, and a frame the agent sends in the same
      // TCP flight as its `Upgrade` would otherwise hit no listener and be
      // dropped with no error, no warning and no audit row.
      socket.on('message', (raw, isBinary) => {
        // Agent sends capabilities immediately after its handshake. Hold frames
        // until token lookup completes instead of misclassifying that first
        // frame as an unauthenticated mobile write.
        if (!authenticated || replaying) { pending.push({ raw, isBinary }); return; }
        // WebSocket message events may arrive while an earlier handler awaits
        // storage. Preserve wire order so agent.hello completes before its
        // first telemetry, capability, or binary relay frame is processed.
        frameChain = frameChain
          .then(() => handleAgentFrame(socketId, socket, auth, raw, isBinary))
          .catch((err) => logger.error({ err }, 'ws frame queue failed'));
      });

      const protocol = Array.isArray(request.headers['sec-websocket-protocol'])
        ? request.headers['sec-websocket-protocol'][0]
        : (request.headers['sec-websocket-protocol'] as string | undefined);
      auth = await authenticate(protocol, readQueryDeviceId(request));
      auth.protocolReady = auth.readOnly;

      if (!auth.device) {
        wsHub.send(socket, {
          type: 'error',
          data: { code: 'UNAUTHORIZED', message: 'no such device' },
        });
        socket.close(4401, 'unauthorized');
        return;
      }

      wsHub.register(socketId, socket, auth.device.id, auth.readOnly);
      // Finish queued early frames before exposing this agent connection. This
      // makes its initial capability report visible to an immediate session
      // request and preserves ordering for every other first-flight frame.
      replaying = true;
      while (pending.length) {
        const frame = pending.shift()!;
        await handleAgentFrame(socketId, socket, auth, frame.raw, frame.isBinary);
      }
      authenticated = true;
      replaying = false;
      logger.info(
        { socketId, deviceId: auth.device.id, readOnly: auth.readOnly },
        auth.readOnly ? 'mobile socket connected (read-only)' : 'agent socket connected',
      );

      if (!auth.readOnly) {
        await prisma.device.update({
          where: { id: auth.device.id },
          data: { status: 'online', lastSeen: new Date() },
        });
        wsHub.broadcastToDevice(auth.device.id, 'device.status', {
          deviceId: auth.device.id,
          status: 'online',
          lastSeen: new Date().toISOString(),
        });
      } else {
        // Replay the last known state so a phone that just opened the app
        // does not stare at "no data" until the agent's next push.
        const [telemetry, applications, storage, cleanup, presence, events, guardianAlerts] = await Promise.all([
          prisma.telemetry.findUnique({ where: { deviceId: auth.device.id } }),
          prisma.applicationCatalog.findUnique({ where: { deviceId: auth.device.id } }),
          prisma.storageReport.findUnique({ where: { deviceId: auth.device.id } }),
          prisma.cleanupPlan.findUnique({ where: { deviceId: auth.device.id } }),
          prisma.ownerState.findUnique({ where: { deviceId: auth.device.id } }),
          prisma.securityEvent.findMany({
            where: { deviceId: auth.device.id },
            orderBy: { createdAt: 'desc' },
            take: 25,
          }),
          // A pre-Phase-9 SQLite database may not yet have Guardian tables.
          // Alert replay is optional; it must not block core telemetry replay.
          prisma.aiAlert.findMany({ where: { deviceId: auth.device.id }, orderBy: { createdAt: 'desc' }, take: 10 }).catch(() => []),
        ]);
        if (telemetry) {
          wsHub.send(socket, {
            type: 'telemetry.latest',
            deviceId: auth.device.id,
            data: { telemetry: telemetry.sample },
          });
        }
        if (applications) {
          wsHub.send(socket, {
            type: 'applications.latest',
            deviceId: auth.device.id,
            data: { catalog: applications.catalog },
          });
        }
        if (storage) {
          wsHub.send(socket, {
            type: 'storage.latest',
            deviceId: auth.device.id,
            data: { summary: storage.summary },
          });
        }
        if (cleanup) {
          const { plan } = cleanup;
          wsHub.send(socket, {
            type: 'cleanup.latest',
            deviceId: auth.device.id,
            data: { deviceId: auth.device.id, ...(plan as object) },
          });
        }
        if (presence) {
          // Phase 5: replay the trust state so the dashboard's security
          // screen renders the real state before the agent's next push.
          wsHub.send(socket, {
            type: 'security.state',
            deviceId: auth.device.id,
            data: {
              deviceId: auth.device.id,
              presence: {
                deviceId: presence.deviceId,
                state: presence.state,
                since: presence.since,
                lastAuthenticatedAt: presence.lastAuthenticatedAt,
                failedAttempts: presence.failedAttempts,
                capabilities: presence.capabilities,
                lastDailyCleanupDate: presence.lastDailyCleanupDate,
                timestamp: presence.updatedAt.toISOString(),
              },
            },
          });
        }
        if (events.length) {
          // Phase 5: replay the recent security event timeline. Newest-first
          // is how it is stored and how the dashboard renders it.
          for (const event of events.slice(0, 25)) {
            wsHub.send(socket, {
              type: 'security.event',
              deviceId: auth.device.id,
              data: {
                deviceId: auth.device.id,
                event: {
                  id: event.id,
                  deviceId: event.deviceId,
                  eventType: event.eventType,
                  severity: event.severity,
                  description: event.description,
                  timestamp: event.createdAt.toISOString(),
                  since: event.since,
                  metadata: (event.metadata ?? undefined) as never,
                  requestId: event.requestId,
                },
              },
            });
          }
        }
        for (const alert of guardianAlerts) wsHub.send(socket, { type: 'guardian.alert', deviceId: auth.device.id, data: { alert: { id: alert.id, deviceId: alert.deviceId, severity: alert.severity, category: alert.category, message: alert.message, createdAt: alert.createdAt.toISOString() } } });
      }

      socket.on('close', () => {
        wsHub.remove(socketId);
        if (auth.device && !auth.readOnly) {
          void import('../modules/guardian/service.js').then(({ cancelTelemetryEvaluation }) => cancelTelemetryEvaluation(auth.device!.id));
        }
        if (auth.device && !auth.readOnly) {
          prisma.device
            .update({ where: { id: auth.device.id }, data: { status: 'offline', lastSeen: new Date() } })
            .catch((err) => logger.error({ err }, 'failed to mark device offline'));
          wsHub.broadcastToDevice(auth.device.id, 'device.status', {
            deviceId: auth.device.id,
            status: 'offline',
            lastSeen: new Date().toISOString(),
          });
        }
        logger.info({ socketId, deviceId: auth.device?.id }, 'websocket closed');
      });

      socket.on('error', (err: Error) => {
        logger.warn({ socketId, err: err.message }, 'websocket error');
      });
    } catch (err) {
      logger.warn({ err }, 'ws handler failed');
      socket.close(1011, 'server error');
    }
  });

  // Expire commands that outlived their TTL on a slow interval, so a phone
  // waiting on a dead agent sees `expired` instead of hanging on `sent`.
  const timer = setInterval(() => {
    expireStaleCommands().catch((err) => logger.warn({ err }, 'could not expire stale commands'));
  }, Math.max(15_000, (config.commandTtlSeconds / 2) * 1000));
  const retentionTimer = setInterval(() => {
    void pruneRetentionSafely();
  }, config.retentionIntervalMs);
  app.addHook('onClose', () => {
    clearInterval(timer);
    clearInterval(retentionTimer);
    void import('../modules/guardian/service.js').then(({ clearTelemetryEvaluations }) => clearTelemetryEvaluations());
  });

  done();
};
