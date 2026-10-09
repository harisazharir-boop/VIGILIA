/**
 * /api/health — liveness and version. Public; no token, no device data.
 */
import type { FastifyPluginCallback } from 'fastify';
import { config } from '../../config/env.js';
import { BACKEND_VERSION, PROTOCOL_VERSION } from '@vigilia/shared';

export const healthRoutes: FastifyPluginCallback = (app, _opts, done) => {
  app.get('/health', async (_request, reply) => {
    return reply.send({
      status: 'ok',
      service: 'vigilia-backend',
      version: BACKEND_VERSION,
      protocolVersion: PROTOCOL_VERSION,
      uptime: Math.round(process.uptime()),
      timestamp: new Date().toISOString(),
    });
  });

  app.get('/health/version', async (_request, reply) => {
    return reply.send({ version: config.nodeEnv, service: 'vigilia-backend' });
  });

  done();
};
