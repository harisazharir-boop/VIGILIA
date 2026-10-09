/**
 * /api/applications/:deviceId — the application catalog (Phase 3B).
 *
 * Public and read-only. The catalog lists what the device's agent found on the
 * machine; the mobile client picks an id from it and the *agent* resolves that
 * id to a fixed argv. There is no path anywhere in this surface, and no route
 * here can launch anything — dispatch goes through the command-token surface.
 */
import type { FastifyPluginCallback } from 'fastify';
import { latestApplicationCatalog } from './service.js';
import { sendError } from '../../utils/errors.js';

export const applicationRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.get('/:deviceId', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const applications = await latestApplicationCatalog(deviceId);
      if (!applications) {
        return reply.status(404).send({
          error: { code: 'NOT_FOUND', message: 'no application catalog yet' },
        });
      }
      return reply.send({ deviceId, applications });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  done();
};
