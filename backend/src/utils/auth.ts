/**
 * Shared authorization for the two routes that can change a device's state:
 * the command dispatch and the command audit history.
 *
 * Both take the *paired command token* — never the agent token — and the
 * token is only ever issued for one device, so a phone paired to the laptop
 * cannot command the desktop.
 */
import type { FastifyReply } from 'fastify';
import { prisma } from '../config/db.js';
import { config } from '../config/env.js';
import { constantTimeEquals } from '../utils/token.js';
import { readCommandToken } from '../utils/headers.js';
import { errorBody } from './errors.js';

export const COMMAND_TTL_SECONDS = config.commandTtlSeconds;

export interface AuthorizedCommandContext {
  id: string;
  name: string;
  os: string;
  hostname: string;
}

/**
 * Verify the paired command token for `deviceId`. Throws a `Unauthorized`
 * error the caller converts into a 401; a wrong device's token is the same
 * error so a probe learns nothing about which devices exist.
 */
export async function authorizeCommandRequest(
  headers: Record<string, string | string[] | undefined>,
  deviceId: string,
): Promise<AuthorizedCommandContext> {
  const token = readCommandToken(headers);
  if (!token) throw new Unauthorized('a command token is required');

  const device = await prisma.device.findUnique({ where: { id: deviceId } });
  if (!device || !device.commandToken) throw new Unauthorized('device is not paired');

  if (!constantTimeEquals(token, device.commandToken)) {
    throw new Unauthorized('command token is not valid for this device');
  }

  return { id: device.id, name: device.name, os: device.os, hostname: device.hostname };
}

export class Unauthorized extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'UNAUTHORIZED';
  }
}

export function rejectUnauthorized(reply: FastifyReply, message: string): FastifyReply {
  return reply.status(401).send(errorBody('UNAUTHORIZED', message));
}
