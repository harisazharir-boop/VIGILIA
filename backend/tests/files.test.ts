import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import { PrismaClient } from '@prisma/client';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '../..');
const dir = mkdtempSync(join(tmpdir(), 'vigilia-files-'));
const url = `file:${join(dir, 'files.db')}`;
process.env.DATABASE_URL = url;
execFileSync(process.execPath, [path.join(root, 'node_modules/prisma/build/index.js'), 'db', 'push', '--force-reset', '--skip-generate', `--schema=${path.join(root, 'backend/prisma/schema.prisma')}`], { cwd: root, env: { ...process.env, DATABASE_URL: url }, stdio: 'ignore' });

let prisma: PrismaClient;
let app: ReturnType<typeof Fastify>;
const first = 'cmphase6filedevicea';
const second = 'cmphase6filedeviceb';
const token = 'vc_phase6_file_token';

before(async () => {
  prisma = new PrismaClient({ datasources: { db: { url } } }); await prisma.$connect();
  const { fileRoutes } = await import('../src/modules/files/routes.js');
  app = Fastify(); await app.register(fileRoutes, { prefix: '/api' }); await app.ready();
  await prisma.device.createMany({ data: [
    { id: first, name: 'A', os: 'linux', hostname: 'a', commandToken: token },
    { id: second, name: 'B', os: 'linux', hostname: 'b', commandToken: 'vc_other' },
  ] });
});
after(async () => { await app.close(); await prisma.$disconnect(); rmSync(dir, { recursive: true, force: true }); });

describe('phase 6 filesystem audit', { concurrency: false }, () => {
  test('accepts a valid token request and persists only a hashed target', async () => {
    const response = await app.inject({ method: 'POST', url: `/api/files/${first}/requests`, headers: { 'x-vigilia-command-token': token }, payload: { operation: 'FILE_LIST', rootId: 'DOCUMENTS' } });
    assert.equal(response.statusCode, 202); const requestId = response.json().requestId;
    const audit = await prisma.filesystemAudit.findUnique({ where: { requestId } });
    assert.equal(audit?.deviceId, first); assert.equal(audit?.operation, 'FILE_LIST'); assert.equal(audit?.status, 'sent'); assert.match(audit?.targetHash ?? '', /^[a-f0-9]{64}$/);
  });
  test('rejects missing or wrong-device token without audit', async () => {
    const missing = await app.inject({ method: 'POST', url: `/api/files/${first}/requests`, payload: { operation: 'FILE_LIST', rootId: 'HOME' } });
    const cross = await app.inject({ method: 'POST', url: `/api/files/${first}/requests`, headers: { 'x-vigilia-command-token': 'vc_other' }, payload: { operation: 'FILE_LIST', rootId: 'HOME' } });
    assert.equal(missing.statusCode, 401); assert.equal(cross.statusCode, 401);
  });
  test('rejects unknown path-like fields before audit', async () => {
    const response = await app.inject({ method: 'POST', url: `/api/files/${first}/requests`, headers: { 'x-vigilia-command-token': token }, payload: { operation: 'FILE_LIST', rootId: 'HOME', path: '../etc' } });
    assert.equal(response.statusCode, 400);
  });
  test('settles matching result but refuses foreign device and operation', async () => {
    const { dispatchFileRequest, applyFileResult } = await import('../src/modules/files/service.js');
    const queued = await dispatchFileRequest(first, { operation: 'FILE_DELETE', entryId: 'f_aaaaaaaaaaaaaaaa' });
    await applyFileResult(second, { requestId: queued.requestId, operation: 'FILE_DELETE', status: 'SUCCEEDED' });
    assert.equal((await prisma.filesystemAudit.findUnique({ where: { requestId: queued.requestId } }))?.status, 'sent');
    await applyFileResult(first, { requestId: queued.requestId, operation: 'FILE_MOVE', status: 'SUCCEEDED' });
    assert.equal((await prisma.filesystemAudit.findUnique({ where: { requestId: queued.requestId } }))?.status, 'sent');
    await applyFileResult(first, { requestId: queued.requestId, operation: 'FILE_DELETE', status: 'NOT_ALLOWED', reason: 'protected' });
    const audit = await prisma.filesystemAudit.findUnique({ where: { requestId: queued.requestId } }); assert.equal(audit?.status, 'denied'); assert.equal(audit?.reason, 'protected'); assert.ok(audit?.completedAt);
  });
  test('audit endpoint is device scoped and token protected', async () => {
    const denied = await app.inject({ method: 'GET', url: `/api/files/${first}/audit`, headers: { 'x-vigilia-command-token': 'vc_other' } });
    const allowed = await app.inject({ method: 'GET', url: `/api/files/${first}/audit`, headers: { 'x-vigilia-command-token': token } });
    assert.equal(denied.statusCode, 401); assert.equal(allowed.statusCode, 200); assert.equal(allowed.json().deviceId, first);
  });
});
