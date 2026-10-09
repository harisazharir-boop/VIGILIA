import type { FastifyPluginCallback } from 'fastify';
import { fileRequestSchema } from '@vigilia/shared';
import { authorizeCommandRequest } from '../../utils/auth.js';
import { sendError } from '../../utils/errors.js';
import { dispatchFileRequest, acceptUploadChunk, completeUpload, downloadStream, cancelTransfer } from './service.js';
import { prisma } from '../../config/db.js';
import { z } from 'zod';

const auditQuerySchema = z.object({ limit: z.coerce.number().int().min(1).max(100).default(25) });

export const fileRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.post('/files/:id/requests', async (request, reply) => {
    try {
      const { id } = request.params as { id: string };
      const parsed = fileRequestSchema.safeParse(request.body);
      if (!parsed.success) return reply.status(400).send({ error: { code: 'VALIDATION_FAILED', message: 'File request validation failed' } });
      const device = await authorizeCommandRequest(request.headers as Record<string, string | string[] | undefined>, id);
      const dispatched = await dispatchFileRequest(device.id, parsed.data);
      return reply.status(202).send(dispatched);
    } catch (err) { return sendError(reply, err); }
  });
  app.put('/files/:id/transfers/:requestId/chunks/:sequence', async (request, reply) => {
    try {
      const { id, requestId, sequence } = request.params as { id: string; requestId: string; sequence: string };
      await authorizeCommandRequest(request.headers as Record<string, string | string[] | undefined>, id);
      const body = request.body; if (!Buffer.isBuffer(body)) return reply.status(400).send({ error: { code: 'VALIDATION_FAILED', message: 'binary chunk required' } });
      const progress = await acceptUploadChunk(id, requestId, Number(sequence), body);
      return reply.send(progress);
    } catch { return reply.status(403).send({ error: { code: 'NOT_ALLOWED', message: 'upload chunk denied' } }); }
  });
  app.post('/files/:id/transfers/:requestId/complete', async (request, reply) => {
    try { const { id, requestId } = request.params as { id: string; requestId: string }; await authorizeCommandRequest(request.headers as Record<string, string | string[] | undefined>, id); completeUpload(id, requestId); return reply.status(202).send({ requestId }); }
    catch { return reply.status(403).send({ error: { code: 'NOT_ALLOWED', message: 'upload completion denied' } }); }
  });
  app.get('/files/:id/transfers/:requestId/download', async (request, reply) => {
    try { const { id, requestId } = request.params as { id: string; requestId: string }; await authorizeCommandRequest(request.headers as Record<string, string | string[] | undefined>, id); const stream = downloadStream(id, requestId); if (!stream) return reply.status(404).send({ error: { code: 'NOT_READY', message: 'download not ready' } }); return reply.type('application/octet-stream').send(stream); }
    catch (err) { return sendError(reply, err); }
  });
  app.delete('/files/:id/transfers/:requestId', async (request, reply) => {
    try { const { id, requestId } = request.params as { id: string; requestId: string }; await authorizeCommandRequest(request.headers as Record<string, string | string[] | undefined>, id); cancelTransfer(id, requestId); return reply.status(204).send(); }
    catch (err) { return sendError(reply, err); }
  });
  app.get('/files/:id/audit', async (request, reply) => {
    try {
      const { id } = request.params as { id: string }; const { limit } = auditQuerySchema.parse(request.query);
      await authorizeCommandRequest(request.headers as Record<string, string | string[] | undefined>, id);
      const rows = await prisma.filesystemAudit.findMany({ where: { deviceId: id }, orderBy: { createdAt: 'desc' }, take: limit });
      return reply.send({ deviceId: id, audits: rows.map(({ targetHash: _targetHash, ...row }) => row) });
    } catch (err) { return sendError(reply, err); }
  });
  done();
};
