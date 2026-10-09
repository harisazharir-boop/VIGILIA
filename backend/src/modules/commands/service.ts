/**
 * Command service — the Phase 2 dispatch and audit surface.
 *
 * A command is dispatched to exactly one device over its authenticated socket,
 * and every hop is recorded in one `CommandLog` row keyed by `requestId`:
 *
 *   queued  → the mobile (or API) request was accepted and authorised
 *   sent    → the frame left on the agent's socket
 *   succeeded / failed → the agent answered
 *   expired → no answer inside the TTL
 *
 * The `target` column exists only for `APPLICATION_*` commands and holds an
 * application id — never an executable path and never a command line. A
 * `CLEAN_*` action stores no target at all: the action id is the whole
 * request and the agent resolves it to fixed roots.
 */
import { prisma } from '../../config/db.js';
import { wsHub } from '../../ws/hub.js';
import { logger } from '../../config/logger.js';
import { COMMAND_STATUSES } from '@vigilia/shared';
import type { CommandRecord, CommandStatus } from '@vigilia/shared';

/** A request id is a correlation id across the audit row, the frame and the reply. */
export function newRequestId(): string {
  const bytes = new Uint8Array(12);
  globalThis.crypto.getRandomValues(bytes);
  return `req_${Buffer.from(bytes).toString('base64url')}`;
}

export function commandLogToRecord(row: {
  id: string;
  deviceId: string;
  command: string;
  status: string;
  note: string | null;
  target: string | null;
  result: string | null;
  createdAt: Date;
  updatedAt: Date;
  expiresAt: Date | null;
}): CommandRecord {
  return {
    id: row.id,
    deviceId: row.deviceId,
    // The column is a string; the union is the protocol's view of it. A value
    // outside the union (only reachable from a hand-written row) is reported
    // as `failed` below rather than breaking the read.
    command: row.command as CommandRecord['command'],
    status: (COMMAND_STATUSES as readonly string[]).includes(row.status)
      ? (row.status as CommandStatus)
      : 'failed',
    note: row.note,
    target: row.target,
    result: row.result,
    createdAt: row.createdAt.toISOString(),
    updatedAt: row.updatedAt.toISOString(),
    expiresAt: row.expiresAt ? row.expiresAt.toISOString() : null,
  };
}

/**
 * Queue a command for a device and deliver it immediately if the agent's
 * socket is open. Returns the audit row the caller returns to the client.
 */
export async function dispatchCommand(
  device: { id: string },
  command: CommandRecord['command'],
  note: string | undefined,
  target: string | undefined,
  ttlSeconds = 120,
): Promise<CommandRecord> {
  const requestId = newRequestId();
  const expiresAt = new Date(Date.now() + ttlSeconds * 1000);

  const row = await prisma.commandLog.create({
    data: {
      deviceId: device.id,
      command,
      status: 'queued',
      note: note?.slice(0, 280) || null,
      // Only an application command carries a target, and only an application
      // id can be in it — the schema rejected anything else before this point.
      target: target ? target.slice(0, 128) : null,
      requestId,
      expiresAt,
    },
  });

  const record = commandLogToRecord(row);

  // Deliver on the agent socket. A queued row with no socket is still a valid
  // row: the mobile client sees `queued` and the agent answers when it
  // reconnects, at which point the replay path picks it up.
  wsHub.sendToAgent(device.id, 'command.dispatch', {
    requestId,
    command: row.command,
    ...(target ? { target } : {}),
    ...(note ? { note } : {}),
    deadline: Math.floor(expiresAt.getTime() / 1000),
  });

  await prisma.commandLog
    .updateMany({ where: { id: row.id }, data: { status: 'sent' } })
    .catch((err) => logger.warn({ err, requestId }, 'could not mark the command as sent'));

  logger.info({ deviceId: device.id, command: row.command, requestId, target: target ?? null }, 'command dispatched');
  return { ...record, status: 'sent' };
}

/**
 * Apply an agent's answer to the audit row.
 *
 * `requestId` is rebound to the authenticated connection's device by the ws
 * handler before this runs, so an agent cannot settle another device's
 * command. An unknown or expired id is a no-op: the row may have been pruned,
 * and the mobile client still learns the outcome from the broadcast.
 */
export async function applyCommandResult(
  deviceId: string,
  requestId: string | undefined,
  status: 'succeeded' | 'failed',
  result: string | undefined,
  target: string | undefined,
): Promise<void> {
  if (!requestId) return;

  const row = await prisma.commandLog.findUnique({ where: { requestId } });
  if (!row || row.deviceId !== deviceId) {
    logger.warn({ requestId, deviceId }, 'command result for an unknown or foreign request');
    return;
  }

  await prisma.commandLog.update({
    where: { requestId },
    data: {
      status,
      result: (result ?? '').slice(0, 1000) || null,
      target: target ?? row.target,
    },
  });

  wsHub.broadcastToDevice(deviceId, 'command.result', {
    requestId,
    deviceId,
    command: row.command,
    status,
    result: (result ?? '').slice(0, 1000) || null,
    target: target ?? row.target,
  });

  logger.info({ requestId, deviceId, command: row.command, status }, 'command result applied');
}

/**
 * Mark every command that outlived its TTL as `expired`, so the mobile client
 * can stop waiting and say so instead of hanging on `sent`.
 */
export async function expireStaleCommands(now = new Date()): Promise<number> {
  const result = await prisma.commandLog.updateMany({
    where: {
      status: { in: ['queued', 'sent'] },
      expiresAt: { lt: now },
    },
    data: { status: 'expired' },
  });
  return result.count;
}
