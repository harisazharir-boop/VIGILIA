/**
 * /api/devices/:id/commands — the dispatch surface (Phase 2), which the
 * cleanup actions (Phase 4B) also ride on.
 *
 *   POST   /api/devices/:id/commands      authorise + queue + dispatch
 *   GET    /api/devices/:id/commands       the audit history
 *
 * Authorization is the *paired command token*, issued once by
 * `/api/devices/:id/pair` (which needs the agent token) and stored on the
 * phone only. The mobile client never holds an agent token.
 *
 * What a request may name:
 *
 *  - a power command from `DEVICE_COMMANDS` (fixed argv on the agent);
 *  - an `APPLICATION_*` command plus an `target` that is an *application id*
 *    the device registered — the agent resolves it, so the client cannot
 *    choose what runs;
 *  - a `CLEAN_*` action id, with no target at all. The action is checked
 *    against the device's own cleanup plan before any audit row exists, so a
 *    cleanup the device does not offer is a client error, not a command.
 *
 * There is no path, no glob and no command string anywhere in the schema, so
 * none of the three can carry one.
 */
import type { FastifyPluginCallback } from 'fastify';
import { z } from 'zod';
import { prisma } from '../../config/db.js';
import { logger } from '../../config/logger.js';
import {
  commandRequestSchema,
  APPLICATION_COMMANDS,
  CLEAN_ACTIONS,
} from '@vigilia/shared';
import type { ApplicationPublic } from '@vigilia/shared';
import { authorizeCommandRequest } from '../../utils/auth.js';
import { dispatchCommand, commandLogToRecord } from './service.js';
import { sendError } from '../../utils/errors.js';

const listSchema = z.object({
  limit: z.coerce.number().int().min(1).max(100).default(25),
});

export const commandRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.post('/devices/:id/commands', async (request, reply) => {
    try {
      const { id } = request.params as { id: string };
      const parsed = commandRequestSchema.safeParse(request.body);
      if (!parsed.success) {
        return reply.status(400).send({
          error: {
            code: 'VALIDATION_FAILED',
            message: 'Request validation failed',
            issues: parsed.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })),
          },
        });
      }

      const device = await authorizeCommandRequest(
        request.headers as Record<string, string | string[] | undefined>,
        id,
      );

      // An application command must name a target the device registered. The
      // check runs after auth but before the audit row: an unknown target is
      // a client error, not a command that should be recorded as dispatched.
      let target: string | undefined;
      if ((APPLICATION_COMMANDS as readonly string[]).includes(parsed.data.command)) {
        const decision = await authorizeApplicationTarget(device, request.body);
        if (!decision.ok) {
          return reply.status(decision.status).send({
            error: { code: decision.code, message: decision.message },
          });
        }
        target = decision.target;
      }

      // A cleanup command names a structured action id the device's own plan
      // must contain. The check runs after auth but before the audit row, so
      // an action the device does not offer is a client error with no audit
      // trail — there was never a command. `target` is never consulted: a
      // cleanup request carries no path, and the schema does not accept one.
      if ((CLEAN_ACTIONS as readonly string[]).includes(parsed.data.command)) {
        const { resolveCleanupAction } = await import('../cleanup/service.js');
        const allowed = await resolveCleanupAction(device.id, parsed.data.command);
        if (!allowed) {
          return reply.status(400).send({
            error: {
              code: 'NOT_ALLOWED',
              message: `${parsed.data.command} is not offered by this device`,
            },
          });
        }
      }

      const record = await dispatchCommand(device, parsed.data.command, parsed.data.note, target);
      return reply.status(202).send({ command: record });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  /** Audit history for a device (same authorization as dispatch). */
  app.get('/devices/:id/commands', async (request, reply) => {
    try {
      const { id } = request.params as { id: string };
      const { limit } = listSchema.parse(request.query);
      await authorizeCommandRequest(
        request.headers as Record<string, string | string[] | undefined>,
        id,
      );

      const rows = await prisma.commandLog.findMany({
        where: { deviceId: id },
        // Newest first: an activity feed is read from the top, so the most
        // recent command must be the first row the mobile client renders.
        orderBy: { createdAt: 'desc' },
        take: limit,
      });
      return reply.send({
        deviceId: id,
        limit,
        commands: rows.map(commandLogToRecord),
      });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  done();
};

/**
 * An application command may only name an application the device's own agent
 * registered. The catalog is the device's, not the client's, so the client
 * cannot name an executable the operator never saw.
 */
async function authorizeApplicationTarget(
  device: { id: string },
  body: unknown,
): Promise<
  | { ok: true; target: string }
  | { ok: false; code: string; message: string; status: number }
> {
  const parsed = commandRequestSchema.safeParse(body);
  if (!parsed.success || !parsed.data.target) {
    return {
      ok: false,
      code: 'VALIDATION_FAILED',
      message: 'this command requires an application target',
      status: 400,
    };
  }
  const target = parsed.data.target;

  const row = await prisma.applicationCatalog.findUnique({ where: { deviceId: device.id } });
  if (!row) {
    return { ok: false, code: 'NOT_ALLOWED', message: 'the device has not reported any applications', status: 400 };
  }
  const catalog = (row.catalog ?? { applications: [] }) as unknown as { applications: ApplicationPublic[] };
  const known = (catalog.applications ?? []).some((a) => a.applicationId === target);
  if (!known) {
    logger.warn({ deviceId: device.id, target }, 'application target is not in the device catalog');
    return {
      ok: false,
      code: 'NOT_ALLOWED',
      message: `${target} is not an application this device reported`,
      status: 400,
    };
  }
  return { ok: true, target };
}
