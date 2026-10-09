/**
 * Request logging: one line per request with the duration, so a slow route is
 * visible without adding a second observability system.
 */
import type { FastifyPluginCallback } from 'fastify';
import { logger } from '../config/logger.js';

export const requestLoggingPlugin: FastifyPluginCallback = (app, _opts, done) => {
  app.addHook('onResponse', (request, reply, done) => {
    logger.info(
      {
        method: request.method,
        url: request.url,
        statusCode: reply.statusCode,
        durationMs: Math.round(reply.elapsedTime),
      },
      'request completed',
    );
    done();
  });
  done();
};
