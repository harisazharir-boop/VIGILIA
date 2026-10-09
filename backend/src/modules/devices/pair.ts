/**
 * /api/devices/:id/pair — issue the *command token* to the mobile client.
 *
 * Pairing takes the agent token, which the phone never holds, so it is an
 * out-of-band step by design. The command token is the credential the phone
 * keeps; it is bound to exactly one device and cannot command another.
 */
import type { FastifyReply } from 'fastify';
import { z } from 'zod';
import { prisma } from '../../config/db.js';
import { logger } from '../../config/logger.js';
import { constantTimeEquals } from '../../utils/token.js';
import { readBearerToken, readDeviceId } from '../../utils/headers.js';
import { errorBody } from '../../utils/errors.js';
import { issueCommandToken } from './register.js';

// The pair body names the device the token will be bound to. There is nothing
// else to say: pairing takes no options and grants no scope beyond this one
// device.
const pairBodySchema = z.object({
  deviceId: z.string().trim().min(1).max(64),
});

export async function pairDevice(
  headers: Record<string, string | string[] | undefined>,
  deviceId: string,
  body: unknown,
  reply: FastifyReply,
): Promise<FastifyReply> {
  const presented = readBearerToken(headers);
  const device = await prisma.device.findUnique({ where: { id: deviceId } });
  if (!device || !device.agentToken) {
    return reply.status(404).send(errorBody('NOT_FOUND', 'no such device'));
  }
  if (!constantTimeEquals(presented, device.agentToken)) {
    return reply.status(401).send(errorBody('UNAUTHORIZED', 'the agent token is not valid'));
  }

  const parsed = pairBodySchema.safeParse(body);
  if (!parsed.success || parsed.data.deviceId !== deviceId) {
    return reply
      .status(400)
      .send(errorBody('VALIDATION_FAILED', 'the pair request must name this device'));
  }

  const claimed = readDeviceId(headers);
  if (claimed && claimed !== deviceId) {
    return reply.status(400).send(errorBody('VALIDATION_FAILED', 'device id mismatch'));
  }

  const commandToken = issueCommandToken();
  await prisma.device.update({ where: { id: deviceId }, data: { commandToken } });

  logger.info({ deviceId }, 'device paired, command token issued');
  return reply.status(200).send({ deviceId, commandToken });
}
