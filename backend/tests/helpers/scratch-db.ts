/**
 * Test harness: a scratch SQLite database for the security service tests.
 *
 * The service module resolves its own `PrismaClient` from `config.databaseUrl`,
 * so the tests point that env var at a temp file before the module is imported
 * and reset the singleton. A schema-holding test DB is created by the runner
 * (`scripts/backend-test.sh`) or by `prisma db push` against the temp URL.
 */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PrismaClient } from '@prisma/client';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '../../..');

export interface ScratchDb {
  url: string;
  prisma: PrismaClient;
  cleanup: () => Promise<void>;
}

/**
 * Create a scratch database with the current schema and return a client.
 *
 * `db push` is used rather than `migrate deploy` because the temp file has no
 * migration history and the schema is what the tests need.
 */
export function makeScratchDb(): ScratchDb {
  const dir = mkdtempSync(join(tmpdir(), 'vigilia-test-'));
  const url = `file:${join(dir, 'test.db')}`;
  const schema = path.resolve(REPO_ROOT, 'backend/prisma/schema.prisma');

  execFileSync(
    process.execPath,
    [path.join(REPO_ROOT, 'node_modules/prisma/build/index.js'), 'db', 'push', '--force-reset', '--skip-generate', `--schema=${schema}`],
    {
      cwd: REPO_ROOT,
      stdio: ['ignore', 'ignore', 'pipe'],
      env: { ...process.env, DATABASE_URL: url },
      timeout: 120_000,
    },
  );

  const prisma = new PrismaClient({ datasources: { db: { url } } });
  const cleanup = async () => {
    await prisma.$disconnect();
    rmSync(dir, { recursive: true, force: true });
  };
  return { url, prisma, cleanup };
}
