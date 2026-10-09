/**
 * /api/storage/:deviceId — the read-only disk breakdown (Phase 4A).
 *
 * Public like the process and application routes: the mobile dashboard is
 * read-only and carries no token. There is no POST, no PUT and no DELETE
 * here — the only writer is the device's own agent over its authenticated
 * socket, where `deviceId` is rebound to the connection.
 *
 * Privacy holds by construction: the payload is byte totals and item counts.
 * No file name, no path and no content is ever sent.
 */
import type { FastifyPluginCallback } from 'fastify';
import { latestStorageSummary } from './service.js';
import { sendError } from '../../utils/errors.js';

export const storageRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.get('/:deviceId', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const summary = await latestStorageSummary(deviceId);
      if (!summary) {
        return reply.status(404).send({
          error: { code: 'NOT_FOUND', message: 'no storage report yet; the agent has not reported' },
        });
      }
      return reply.send({ deviceId, summary, timestamp: summary.timestamp ?? null });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  done();
};
