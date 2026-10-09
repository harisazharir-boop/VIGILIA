import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { makeScratchDb, type ScratchDb } from './helpers/scratch-db.js';

let scratch: ScratchDb;
let prisma: import('@prisma/client').PrismaClient;
let pruneRetention: typeof import('../src/modules/retention/service.js').pruneRetention;
let pruneRetentionSafely: typeof import('../src/modules/retention/service.js').pruneRetentionSafely;
const deviceId = 'cmretentiondevice';
const now = new Date('2026-10-09T00:00:00.000Z');
const old = new Date('2025-01-01T00:00:00.000Z');

before(async () => {
  scratch = makeScratchDb();
  process.env.DATABASE_URL = scratch.url;
  process.env.VIGILIA_RETENTION_DAYS = '90';
  process.env.VIGILIA_RETENTION_BATCH_SIZE = '2';
  prisma = scratch.prisma;
  await prisma.device.create({ data: { id: deviceId, name: 'retention', os: 'linux', hostname: 'retention' } });
  ({ pruneRetention, pruneRetentionSafely } = await import('../src/modules/retention/service.js'));
});

test('retention failure is contained and does not reject caller work', async () => {
  assert.equal(await pruneRetentionSafely(async () => { throw new Error('scratch failure'); }), false);
});
after(async () => scratch.cleanup());

test('retention preserves recent records and active alerts while pruning bounded old Guardian and security history', async () => {
  await prisma.aiMessage.createMany({ data: [
    { deviceId, role: 'user', content: 'old 1', createdAt: old }, { deviceId, role: 'assistant', content: 'old 2', createdAt: old }, { deviceId, role: 'user', content: 'old 3', createdAt: old }, { deviceId, role: 'assistant', content: 'recent', createdAt: now },
  ] });
  await prisma.aiAudit.createMany({ data: [
    { requestId: 'req_retention_1', deviceId, tool: 'INSPECT_HEALTH', status: 'SUCCEEDED', createdAt: old }, { requestId: 'req_retention_2', deviceId, tool: 'INSPECT_HEALTH', status: 'SUCCEEDED', createdAt: old }, { requestId: 'req_retention_3', deviceId, tool: 'INSPECT_HEALTH', status: 'SUCCEEDED', createdAt: old }, { requestId: 'req_retention_recent', deviceId, tool: 'INSPECT_HEALTH', status: 'SUCCEEDED', createdAt: now },
  ] });
  await prisma.securityEvent.createMany({ data: [
    { deviceId, eventType: 'owner.locked', severity: 'bad', description: 'old 1', createdAt: old }, { deviceId, eventType: 'owner.locked', severity: 'bad', description: 'old 2', createdAt: old }, { deviceId, eventType: 'owner.locked', severity: 'bad', description: 'old 3', createdAt: old }, { deviceId, eventType: 'owner.locked', severity: 'bad', description: 'recent', createdAt: now },
  ] });
  await prisma.aiAlert.create({ data: { deviceId, severity: 'CRITICAL', category: 'security', message: 'active alert', createdAt: old } });
  const first = await pruneRetention(now);
  assert.deepEqual(first, { aiMessage: 2, aiAudit: 2, securityEvent: 2 });
  assert.equal(await prisma.aiMessage.count(), 2);
  assert.equal(await prisma.aiAudit.count(), 2);
  assert.equal(await prisma.securityEvent.count(), 2);
  assert.equal(await prisma.aiAlert.count(), 1);
  const second = await pruneRetention(now);
  assert.deepEqual(second, { aiMessage: 1, aiAudit: 1, securityEvent: 1 });
  const third = await pruneRetention(now);
  assert.deepEqual(third, { aiMessage: 0, aiAudit: 0, securityEvent: 0 });
  assert.equal(await prisma.aiMessage.count(), 1);
  assert.equal(await prisma.aiAudit.count(), 1);
  assert.equal(await prisma.securityEvent.count(), 1);
});
