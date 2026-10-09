/**
 * The error handler turns a thrown `Error` into the protocol's uniform
 * `{ error: { code, message } }` body. A client always gets a code to switch
 * on and never a stack trace.
 */
import type { FastifyPluginCallback } from 'fastify';
import { errorBody } from '../utils/errors.js';
import { logger } from '../config/logger.js';

export const errorHandlerPlugin: FastifyPluginCallback = (app, _opts, done) => {
  app.setErrorHandler((err, request, reply) => {
    const code = err instanceof Error ? err.name : 'INTERNAL_ERROR';
    const message = err instanceof Error ? err.message : 'Internal server error';
    const status = statusFor(code);
    if (status >= 500) logger.error({ err, url: request.url }, 'server error');
    else logger.warn({ code, url: request.url, message }, 'request rejected');
    reply.status(status).send(errorBody(code, message));
  });
  done();
};

function statusFor(code: string): number {
  switch (code) {
    case 'VALIDATION_FAILED':
      return 400;
    case 'NOT_ALLOWED':
      return 400;
    case 'UNAUTHORIZED':
      return 401;
    case 'FORBIDDEN':
      return 403;
    case 'NOT_FOUND':
      return 404;
    case 'CONFLICT':
      return 409;
    default:
      return 500;
  }
}
