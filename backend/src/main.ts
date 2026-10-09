/**
 * VIGILIA backend — HTTP + WebSocket entry point.
 *
 * Surface (all under `/api` except the UI and `/ws`):
 *
 *   GET  /api/health                          liveness, no token
 *   POST /api/devices/register                agent introduces itself (agent token)
 *   POST /api/auth/verify                     is this agent token valid?
 *   POST /api/devices/:id/pair                issue the command token (agent token)
 *   GET  /api/devices                         device list, read-only
 *   GET  /api/devices/:id                     one device, read-only
 *   GET  /api/monitoring/:id/latest|history   telemetry, read-only
 *   GET  /api/processes/:id                   process list, read-only (3A)
 *   GET  /api/applications/:id                application catalog, read-only (3B)
 *   GET  /api/storage/:deviceId               read-only disk breakdown (4A)
 *   GET  /api/cleanup/:deviceId               cleanup actions + AI recommendations (4B/4C)
 *   GET  /api/cleanup/:deviceId/preview       dry-run estimate (4B)
 *   GET  /api/security/:deviceId              owner state + security events (5A–5H)
 *   GET  /api/security/:deviceId/state        owner-presence state only (5A/5B)
 *   GET  /api/security/:deviceId/events       security event timeline (5D/5H)
 *   PUT  /api/security/:deviceId/daily-cleanup Daily Auto Cleanup config (5F)
 *   PUT  /api/security/:deviceId/settings     security settings (5F)
 *   POST /api/devices/:id/commands            dispatch (command token)
 *   GET  /api/devices/:id/commands            audit history (command token)
 *   WS   /ws                                  agent + read-only mobile stream
 *
 * The mobile UI is served from the repo root, so only this process has to run.
 */
import Fastify from 'fastify';
import websocket from '@fastify/websocket';
import { config } from './config/env.js';
import { logger } from './config/logger.js';
import { prisma } from './config/db.js';
import { healthRoutes } from './modules/health/routes.js';
import { authRoutes } from './modules/auth/routes.js';
import { deviceRoutes } from './modules/devices/routes.js';
import { registerDevice } from './modules/devices/register.js';
import { pairDevice } from './modules/devices/pair.js';
import { monitoringRoutes } from './modules/monitoring/routes.js';
import { processRoutes } from './modules/processes/routes.js';
import { applicationRoutes } from './modules/applications/routes.js';
import { storageRoutes } from './modules/storage/routes.js';
import { cleanupRoutes } from './modules/cleanup/routes.js';
import { securityRoutes } from './modules/security/routes.js';
import { commandRoutes } from './modules/commands/routes.js';
import { fileRoutes } from './modules/files/routes.js';
import { remoteRoutes } from './modules/remote/routes.js';
import { hardwareRoutes } from './modules/hardware/routes.js';
import { guardianRoutes } from './modules/guardian/routes.js';
import { wsRoutes } from './ws/routes.js';
import { corsPlugin } from './plugins/cors.js';
import { errorHandlerPlugin } from './plugins/errorHandler.js';
import { requestLoggingPlugin } from './plugins/requestLogging.js';
import { staticPlugin } from './plugins/static.js';

async function main(): Promise<void> {
  await prisma.$connect();
  logger.info('database connected');

  const app = Fastify({
    logger: false,
    bodyLimit: 256 * 1024,
  });
  // Transfer chunks are binary and capped before they reach route handlers.
  app.addContentTypeParser('application/octet-stream', { parseAs: 'buffer', bodyLimit: 64 * 1024 }, (_request, body, done) => done(null, body));

  await app.register(corsPlugin);
  await app.register(requestLoggingPlugin);
  await app.register(errorHandlerPlugin);
  await app.register(websocket);
  await app.register(wsRoutes);
  await app.register(staticPlugin);

  // Registration and pairing live outside the device routes because they take
  // the *agent* token, and the mobile client never holds that.
  app.post('/api/devices/register', async (request, reply) =>
    registerDevice(
      request.headers as Record<string, string | string[] | undefined>,
      request.body,
      reply,
    ),
  );
  app.post('/api/devices/:id/pair', async (request, reply) => {
    const { id } = request.params as { id: string };
    return pairDevice(
      request.headers as Record<string, string | string[] | undefined>,
      id,
      request.body,
      reply,
    );
  });

  app.register(healthRoutes, { prefix: '/api' });
  app.register(authRoutes, { prefix: '/api' });
  app.register(deviceRoutes, { prefix: '/api' });
  app.register(monitoringRoutes, { prefix: '/api' });
  app.register(processRoutes, { prefix: '/api/processes' });
  app.register(applicationRoutes, { prefix: '/api/applications' });
  app.register(storageRoutes, { prefix: '/api/storage' });
  app.register(cleanupRoutes, { prefix: '/api/cleanup' });
  app.register(securityRoutes, { prefix: '/api/security' });
  app.register(commandRoutes, { prefix: '/api' });
  app.register(fileRoutes, { prefix: '/api' });
  app.register(remoteRoutes, { prefix: '/api' });
  app.register(hardwareRoutes, { prefix: '/api' });
  app.register(guardianRoutes, { prefix: '/api' });

  app.get('/api', async () => ({
    service: 'vigilia-backend',
    docs: ['health', 'devices', 'monitoring', 'processes', 'applications', 'storage', 'cleanup', 'commands'],
  }));

  const url = await app.listen({ host: config.host, port: config.port });
  logger.info({ url, nodeEnv: config.nodeEnv }, 'VIGILIA backend listening');

  const shutdown = async (signal: string) => {
    logger.info({ signal }, 'shutting down');
    await app.close();
    await prisma.$disconnect();
    process.exit(0);
  };
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
  process.on('SIGINT', () => void shutdown('SIGINT'));
}

main().catch((err) => {
  logger.error({ err }, 'backend failed to start');
  process.exit(1);
});
