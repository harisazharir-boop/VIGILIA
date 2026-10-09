import { PrismaClient } from '@prisma/client';
import { existsSync, statSync } from 'node:fs';

if (!process.env.DATABASE_URL) throw new Error('DATABASE_URL is not configured');
if (!process.env.DATABASE_URL.startsWith('file:')) throw new Error('only sqlite file database supported');
const path = process.env.DATABASE_URL.slice(5); if (!existsSync(path) || !statSync(path).isFile()) throw new Error('database file missing');
const prisma = new PrismaClient();
try {
  await prisma.$connect();
  await prisma.$queryRawUnsafe('SELECT 1 FROM "Device" LIMIT 1');
  console.log('Prisma runtime preflight passed');
} finally {
  await prisma.$disconnect();
}
