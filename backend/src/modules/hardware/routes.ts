import type { FastifyPluginCallback } from 'fastify';
import { hardwareControlRequestSchema } from '@vigilia/shared';
import { prisma } from '../../config/db.js';
import { authorizeCommandRequest } from '../../utils/auth.js';
import { sendError } from '../../utils/errors.js';
import * as hardware from './service.js';

/** Phase 8 read surface plus fail-closed controls until native agents support them. */
export const hardwareRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.get('/hardware/:id', async (request, reply) => {
    const { id } = request.params as { id: string }; const row = await prisma.telemetry.findUnique({ where: { deviceId: id } });
    if (!row) return reply.status(404).send({ error: { code: 'NOT_FOUND', message: 'no telemetry yet' } });
    const sample = row.sample as { hardware?: unknown; networkTelemetry?: unknown };
    return reply.send({ deviceId: id, hardware: sample.hardware ?? null, network: sample.networkTelemetry ?? null, timestamp: row.updatedAt.toISOString() });
  });
  app.get('/network/:id/diagnostics', async (request, reply) => {
    const { id } = request.params as { id: string }; const row = await prisma.telemetry.findUnique({ where: { deviceId: id } });
    if (!row) return reply.status(404).send({ error: { code: 'NOT_FOUND', message: 'no telemetry yet' } });
    const network = (row.sample as { networkTelemetry?: Record<string, unknown> }).networkTelemetry ?? null;
    return reply.send({ deviceId: id, diagnostic: network, timestamp: row.updatedAt.toISOString() });
  });
  app.post('/hardware/:id/controls', async (request, reply) => {
    try {
      const parsed = hardwareControlRequestSchema.safeParse(request.body); if (!parsed.success) return reply.status(400).send({ error: { code: 'VALIDATION_FAILED', message: 'invalid hardware control' } });
      const { id } = request.params as { id: string }; await authorizeCommandRequest(request.headers as Record<string, string | string[] | undefined>, id);
      if (parsed.data.control !== 'BRIGHTNESS') { const result = { control: parsed.data.control, status: 'UNSUPPORTED' as const, reason: 'native control unavailable' }; await prisma.hardwareAudit.create({ data: { deviceId: id, control: result.control, status: result.status, reason: result.reason } }); return reply.status(501).send({ deviceId: id, result }); }
      const result = await hardware.dispatchBrightness(id, parsed.data.value as number);
      return reply.status(result.status === 'UNSUPPORTED' ? 501 : 202).send({ deviceId: id, result });
    } catch (error) { return sendError(reply, error); }
  });
  done();
};
