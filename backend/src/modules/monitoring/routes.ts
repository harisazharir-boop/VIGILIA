/**
 * /api/monitoring/:id — telemetry read surface.
 *
 *   GET /api/monitoring/:id/latest    the most recent sample
 *   GET /api/monitoring/:id/history   the last N samples
 *
 * Public and read-only, like the device routes. Telemetry is written only by
 * the device's own agent over its authenticated socket.
 */
import type { FastifyPluginCallback } from 'fastify';
import { prisma } from '../../config/db.js';
import { sendError } from '../../utils/errors.js';
import { z } from 'zod';

const historySchema = z.object({
  limit: z.coerce.number().int().min(1).max(120).default(30),
});

export const monitoringRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.get('/monitoring/:id/latest', async (request, reply) => {
    try {
      const { id } = request.params as { id: string };
      const row = await prisma.telemetry.findUnique({ where: { deviceId: id } });
      if (!row) return reply.status(404).send({ error: { code: 'NOT_FOUND', message: 'no telemetry yet' } });
      return reply.send({
        deviceId: id,
        telemetry: row.sample,
        timestamp: row.updatedAt.toISOString(),
      });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  app.get('/monitoring/:id/history', async (request, reply) => {
    try {
      const { id } = request.params as { id: string };
      const { limit } = historySchema.parse(request.query);

      const latest = await prisma.telemetry.findUnique({ where: { deviceId: id } });
      // SQLite keeps one row per device (the latest); history is the same
      // sample until a process snapshot history exists. The mobile client
      // renders what is there rather than fabricating points.
      const samples = latest ? [latest] : [];
      return reply.send({
        deviceId: id,
        limit,
        samples: samples.map((s) => ({ ...(s.sample as object), timestamp: s.updatedAt.toISOString() })),
      });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  done();
};
