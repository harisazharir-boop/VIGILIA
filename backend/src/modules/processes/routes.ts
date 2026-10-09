/**
 * /api/processes/:deviceId — the process list (Phase 3A).
 *
 * Read-only. The mobile dashboard carries no token, and this route cannot
 * touch a process: there is no POST, no PUT, no DELETE and no kill verb
 * anywhere in the backend.
 */
import type { FastifyPluginCallback } from 'fastify';
import { latestProcessSnapshot } from './service.js';
import { sendError } from '../../utils/errors.js';

export const processRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.get('/:deviceId', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const snapshot = await latestProcessSnapshot(deviceId);
      if (!snapshot) {
        return reply
          .status(404)
          .send({ error: { code: 'NOT_FOUND', message: 'no process snapshot yet' } });
      }
      return reply.send({ deviceId, snapshot });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  done();
};
