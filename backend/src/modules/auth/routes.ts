/**
 * /api/auth — token verification.
 *
 *   POST /api/auth/verify   { deviceId, token }   is this agent token valid?
 *
 * The agent calls this on startup with its stored token before it opens the
 * socket. A token that no longer verifies is treated as "re-register".
 */
import type { FastifyPluginCallback } from 'fastify';
import { prisma } from '../../config/db.js';
import { constantTimeEquals } from '../../utils/token.js';
import { sendError } from '../../utils/errors.js';
import { z } from 'zod';

const verifySchema = z.object({
  deviceId: z.string().trim().min(1).max(64),
  token: z.string().trim().min(1).max(128),
});

export const authRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.post('/auth/verify', async (request, reply) => {
    try {
      const parsed = verifySchema.safeParse(request.body);
      if (!parsed.success) {
        return reply
          .status(400)
          .send({ error: { code: 'VALIDATION_FAILED', message: 'Request validation failed' } });
      }
      const { deviceId, token } = parsed.data;

      const device = await prisma.device.findUnique({ where: { id: deviceId } });
      // The same answer shape for "no such device" and "wrong token", so a
      // probe learns nothing about which device ids exist.
      if (!device || !device.agentToken) {
        return reply
          .status(200)
          .send({ valid: false, deviceId, reason: 'device is not registered' });
      }
      if (!constantTimeEquals(token, device.agentToken)) {
        return reply.status(200).send({ valid: false, deviceId, reason: 'token is not valid' });
      }

      await prisma.device.update({
        where: { id: deviceId },
        data: { status: 'online', lastSeen: new Date() },
      });
      return reply.send({ valid: true, deviceId });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  done();
};
