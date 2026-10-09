/**
 * /cleanup — storage cleanup surface (Phase 4B/4C).
 *
 *   GET  /cleanup/:deviceId/preview?actionId=CLEAN_TEMP   dry-run estimate
 *   GET  /cleanup/:deviceId                               plan + AI recommendations
 *
 * Both are **public and read-only**, like the process, application and
 * storage routes: the mobile dashboard carries no token, and neither route
 * can modify a file.
 *
 * There is no POST, no PUT and no DELETE here, and there is no route anywhere
 * in the backend that accepts a path. Execution of a cleanup goes through the
 * Phase 2 command surface:
 *
 *   POST /api/devices/:id/commands   { command: "CLEAN_TEMP" }
 *
 * with the paired command token, the allowlist check, the audit row and the
 * agent's own structured result. This module never dispatches anything.
 */
import type { FastifyPluginCallback } from 'fastify';
import { cleanupPreviewRequestSchema } from '@vigilia/shared';
import { prisma } from '../../config/db.js';
import { isPlanFresh, planRowToPublic, previewCleanup } from './service.js';
import { sendError } from '../../utils/errors.js';

const previewQuerySchema = cleanupPreviewRequestSchema;

export const cleanupRoutes: FastifyPluginCallback = (app, _opts, done) => {
  /** Public: dry-run preview for one structured cleanup action. */
  app.get('/:deviceId/preview', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const parsed = previewQuerySchema.safeParse(request.query);
      if (!parsed.success) {
        return reply.status(400).send({
          error: {
            code: 'VALIDATION_FAILED',
            message: 'Request validation failed',
            issues: parsed.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })),
          },
        });
      }

      const decision = await previewCleanup(deviceId, parsed.data.actionId);
      if (!decision.ok) {
        return reply.status(decision.status).send({
          error: { code: decision.code, message: decision.message },
        });
      }

      return reply.send({
        deviceId,
        actionId: parsed.data.actionId,
        preview: decision.preview,
        // The preview is the honest statement of what would happen: it is a
        // dry run, and the UI must say so.
        dryRun: true,
        storage: decision.storage,
      });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  /** Public: the device's cleanup plan plus the AI recommendations. */
  app.get('/:deviceId', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const row = await prisma.cleanupPlan.findUnique({ where: { deviceId } });
      if (!row) {
        return reply.send({ actions: [], recommendations: [], fresh: false });
      }
      const plan = planRowToPublic(row, null);
      return reply.send({ ...plan, fresh: isPlanFresh(row) });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  done();
};
