/**
 * CORS. The mobile UI is served from the same origin, but a dev run may load
 * it from a different port, so the allowed origin list is explicit.
 */
import type { FastifyPluginCallback } from 'fastify';
import cors from '@fastify/cors';
import { config } from '../config/env.js';

export const corsPlugin: FastifyPluginCallback = (app, _opts, done) => {
  const origins = config.corsOrigin
    .split(',')
    .map((o) => o.trim())
    .filter(Boolean);

  app.register(cors, {
    origin: origins,
    methods: ['GET', 'POST'],
    allowedHeaders: ['Content-Type', 'x-vigilia-command-token', 'x-vigilia-device-id', 'Authorization'],
    credentials: false,
  });
  done();
};
