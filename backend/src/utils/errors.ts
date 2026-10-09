/**
 * Uniform `{ error: { code, message } }` bodies. Every route answers with a
 * code the mobile client can switch on, never a raw stack trace.
 */
import type { FastifyReply } from 'fastify';
import { logger } from '../config/logger.js';

export interface ApiError {
  code: string;
  message: string;
  issues?: unknown;
}

export function errorBody(code: string, message: string, issues?: unknown): { error: ApiError } {
  return { error: { code, message, ...(issues ? { issues } : {}) } };
}

export function sendError(reply: FastifyReply, err: unknown): FastifyReply {
  const code = err instanceof Error ? err.name : 'INTERNAL_ERROR';
  const message = err instanceof Error ? err.message : 'Internal server error';
  const status = statusFor(code);
  logger.error({ err }, 'request failed');
  return reply.status(status).send(errorBody(code, message));
}

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
