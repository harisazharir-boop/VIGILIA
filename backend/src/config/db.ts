/**
 * Prisma singleton. One client per process: the connection pool is not
 * per-request, and a hot-reloading dev server must not orphan handles.
 */
import { PrismaClient } from '@prisma/client';
import { config } from './env.js';

declare global {
  var __vigiliaPrisma: PrismaClient | undefined;
}

export const prisma =
  globalThis.__vigiliaPrisma ??
  new PrismaClient({
    datasources: { db: { url: config.databaseUrl } },
    log: config.nodeEnv === 'development' ? ['warn', 'error'] : ['error'],
  });

if (config.nodeEnv !== 'production') {
  globalThis.__vigiliaPrisma = prisma;
}
