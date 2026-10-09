/**
 * /api/security — the Phase 5 surface (5D/5F/5H).
 *
 *   GET  /api/security/:deviceId                      owner state + recent events
 *   GET  /api/security/:deviceId/state                owner-presence state only
 *   GET  /api/security/:deviceId/events               security event timeline
 *   PUT  /api/security/:deviceId/daily-cleanup        Daily Auto Cleanup config
 *   PUT  /api/security/:deviceId/settings             security settings
 *
 * Reads are public and read-only, exactly like the process, application,
 * storage and cleanup routes: the mobile dashboard carries no token, and none
 * of these routes can change anything on a device.
 *
 * The two writes are the exception, and they go through the **same paired
 * command token** `APPLICATION_CLOSE` uses — there is no second credential,
 * and no second authorisation system. The token is scoped to one device, so
 * a phone paired to the laptop cannot configure the desktop.
 *
 * What the writes can do is bounded by the schema:
 *  - the daily-cleanup categories must each be an allowlisted `CLEAN_*` id,
 *    and the service re-filters them against the daily-safe subset;
 *  - the settings are three booleans;
 *  - there is no path, no shell, no executable and no biometric field
 *    anywhere in either body, so none can be written.
 */
import type { FastifyPluginCallback } from 'fastify';
import {
  dailyCleanupConfigRequestSchema,
  securitySettingsRequestSchema,
} from '@vigilia/shared';
import type { DailyCleanupConfig, SecuritySettings } from '@vigilia/shared';
import { authorizeCommandRequest } from '../../utils/auth.js';
import { sendError } from '../../utils/errors.js';
import { logger } from '../../config/logger.js';
import {
  absentOwnerPresence,
  dailyCleanupConfigToPublic,
  getDailyCleanupConfig,
  latestOwnerPresence,
  latestSecurityEvents,
  setDailyCleanupConfig,
} from './service.js';
import { applySecuritySettings } from './settings.js';

const listSchema = {
  limit: 25,
};

function parseLimit(raw: unknown): number {
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 1) return listSchema.limit;
  return Math.min(100, Math.floor(n));
}

export const securityRoutes: FastifyPluginCallback = (app, _opts, done) => {
  /** Public: the whole security view for the dashboard. */
  app.get('/:deviceId', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const [presence, events, cleanup] = await Promise.all([
        latestOwnerPresence(deviceId),
        latestSecurityEvents(deviceId, listSchema.limit),
        getDailyCleanupConfig(deviceId),
      ]);
      return reply.send({
        deviceId,
        presence: presence ?? absentOwnerPresence(deviceId),
        events,
        dailyCleanup: {
          config: dailyCleanupConfigToPublic(cleanup.config),
          lastCleanupDate: cleanup.lastCleanupDate,
          lastCleanupAt: cleanup.lastCleanupAt,
          lastResult: cleanup.lastResult,
        },
      });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  /** Public: owner-presence state only (the trust indicator). */
  app.get('/:deviceId/state', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const presence = await latestOwnerPresence(deviceId);
      return reply.send({
        deviceId,
        presence: presence ?? absentOwnerPresence(deviceId),
      });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  /** Public: the security event timeline, newest-first. */
  app.get('/:deviceId/events', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const query = (request.query ?? {}) as { limit?: unknown };
      const events = await latestSecurityEvents(deviceId, parseLimit(query.limit));
      return reply.send({ deviceId, events });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  /**
   * Paired command token: set the Daily Auto Cleanup configuration.
   *
   * Conservative by construction — the service re-filters the categories
   * against the daily-safe allowlist, so a request cannot arm an action that
   * is not safe to run unattended.
   */
  app.put('/:deviceId/daily-cleanup', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const parsed = dailyCleanupConfigRequestSchema.safeParse(request.body);
      if (!parsed.success) {
        return reply.status(400).send({
          error: {
            code: 'VALIDATION_FAILED',
            message: 'Request validation failed',
            issues: parsed.error.issues.map((i) => ({
              path: i.path.join('.'),
              message: i.message,
            })),
          },
        });
      }

      await authorizeCommandRequest(
        request.headers as Record<string, string | string[] | undefined>,
        deviceId,
      );
      const stored = await setDailyCleanupConfig(deviceId, parsed.data.config as DailyCleanupConfig);
      return reply.send({ deviceId, config: dailyCleanupConfigToPublic(stored) });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  /**
   * Paired command token: toggle the security settings the mobile app owns.
   *
   * The three booleans map onto the device's intrusion policy. They are
   * written to the stored owner-state row's capabilities, which the agent
   * re-reads on its next report cycle — so the phone's switch is honoured by
   * the device, and the device stays the authority on what it can do.
   */
  app.put('/:deviceId/settings', async (request, reply) => {
    try {
      const { deviceId } = request.params as { deviceId: string };
      const parsed = securitySettingsRequestSchema.safeParse(request.body);
      if (!parsed.success) {
        return reply.status(400).send({
          error: {
            code: 'VALIDATION_FAILED',
            message: 'Request validation failed',
            issues: parsed.error.issues.map((i) => ({
              path: i.path.join('.'),
              message: i.message,
            })),
          },
        });
      }

      await authorizeCommandRequest(
        request.headers as Record<string, string | string[] | undefined>,
        deviceId,
      );
      const applied = await applySecuritySettings(deviceId, parsed.data.settings as SecuritySettings);
      logger.info({ deviceId, applied }, 'security settings updated');
      return reply.send({ deviceId, settings: applied });
    } catch (err) {
      return sendError(reply, err);
    }
  });

  done();
};
