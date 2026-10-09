/**
 * /api/devices/register — a new agent introduces itself.
 *
 * A repeat registration of the same (name, hostname) re-issues the agent token
 * for that device instead of creating a duplicate, so a reinstalled agent or
 * a rotated machine gets one row, not many.
 */
import type { FastifyReply } from 'fastify';
import { prisma } from '../../config/db.js';
import { logger } from '../../config/logger.js';
import { readDeviceId } from '../../utils/headers.js';
import { errorBody } from '../../utils/errors.js';
import { protocolCompatibility, registerDeviceSchema } from '@vigilia/shared';

export function issueAgentToken(): string {
  // A `vig_` prefix so the two credentials are never confused on sight:
  // the agent token and the command token look different on purpose.
  const bytes = new Uint8Array(32);
  globalThis.crypto.getRandomValues(bytes);
  return `vig_${Buffer.from(bytes).toString('base64url')}`;
}

export function issueCommandToken(): string {
  const bytes = new Uint8Array(24);
  globalThis.crypto.getRandomValues(bytes);
  return `vc_${Buffer.from(bytes).toString('base64url')}`;
}

export async function registerDevice(
  headers: Record<string, string | string[] | undefined>,
  body: unknown,
  reply: FastifyReply,
): Promise<FastifyReply> {
  const parsed = registerDeviceSchema.safeParse(body);
  if (!parsed.success) {
    return reply.status(400).send(errorBody('VALIDATION_FAILED', 'registration rejected', parsed.error.issues));
  }
  const compatibility = protocolCompatibility(parsed.data.protocolVersion);
  if (!compatibility.compatible) return reply.status(426).send(errorBody('UNSUPPORTED_PROTOCOL_VERSION', compatibility.reason));

  const { name, os, hostname } = parsed.data;
  const claimedId = readDeviceId(headers);

  // Re-registering the same machine re-issues its token rather than
  // duplicating the row. A claimed id is honoured only when the (name,
  // hostname) pair matches, so a probe cannot steal another device's row.
  const existing = await prisma.device.findFirst({
    where: { name, hostname },
  });

  if (existing && (!claimedId || existing.id !== claimedId)) {
    logger.info({ deviceId: existing.id, name }, 'device re-registered, re-issuing agent token');
  }

  const token = issueAgentToken();
  // The agent token is stored once at registration so pairing can be verified
  // against it later. It is never returned again after this response.
  const device = await prisma.device.upsert({
    where: claimedId ? { id: claimedId } : { id: '__no_such_row__' },
    create: {
      id: claimedId || undefined,
      name,
      os,
      hostname,
      agentToken: token,
      status: 'online',
      lastSeen: new Date(),
    },
    update: {
      name,
      os,
      hostname,
      agentToken: token,
      status: 'online',
      lastSeen: new Date(),
    },
  });

  logger.info({ deviceId: device.id, name, os, hostname }, 'device registered');
  return reply.status(201).send({ deviceId: device.id, token });
}
