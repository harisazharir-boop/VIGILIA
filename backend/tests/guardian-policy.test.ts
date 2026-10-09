import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PrismaClient } from '@prisma/client';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..'); const dir = mkdtempSync(path.join(tmpdir(), 'vigilia-guardian-policy-')); const url = `file:${path.join(dir, 'test.db')}`; process.env.DATABASE_URL = url;
execFileSync(process.execPath, [path.join(root, 'node_modules/prisma/build/index.js'), 'db', 'push', '--force-reset', '--skip-generate', `--schema=${path.join(root, 'backend/prisma/schema.prisma')}`], { cwd: root, env: { ...process.env, DATABASE_URL: url }, stdio: 'ignore' });
const deviceId = 'cmguardianpolicydevice'; const otherId = 'cmguardianpolicyother'; let prisma: PrismaClient; let guardian: typeof import('../src/modules/guardian/service.js');
before(async () => { prisma = new PrismaClient({ datasources: { db: { url } } }); await prisma.$connect(); guardian = await import('../src/modules/guardian/service.js'); await prisma.device.createMany({ data: [{ id: deviceId, name: 'A', os: 'linux', hostname: 'a' }, { id: otherId, name: 'B', os: 'linux', hostname: 'b' }] }); await prisma.telemetry.create({ data: { deviceId, sample: { deviceId, cpu: 10, ram: 10, storage: 10, timestamp: new Date().toISOString() } } }); });
after(async () => { await prisma.$disconnect(); rmSync(dir, { recursive: true, force: true }); });

test('Guardian previews fixed allowed action then dispatches only after approval', async () => {
  const reply = await guardian.guardian(deviceId, 'Please lock my laptop'); assert.equal(reply.preview?.action, 'LOCK'); assert.ok(reply.preview?.confirmationRequired);
  const denied = await guardian.decideConfirmation(deviceId, reply.requestId, false); assert.equal(denied.status, 'DENIED'); assert.equal(await prisma.commandLog.count({ where: { deviceId } }), 0);
  const approved = await guardian.guardian(deviceId, 'lock'); const result = await guardian.decideConfirmation(deviceId, approved.requestId, true); assert.equal(result.status, 'APPROVED'); assert.ok(result.commandRequestId);
  const command = await prisma.commandLog.findFirst({ where: { deviceId, command: 'LOCK' } }); assert.equal(command?.status, 'sent');
});
test('Guardian rejects wrong device, duplicate, expiry and unsupported action without dispatch', async () => {
  const reply = await guardian.guardian(deviceId, 'restart'); await assert.rejects(() => guardian.decideConfirmation(otherId, reply.requestId, true));
  await guardian.decideConfirmation(deviceId, reply.requestId, false); await assert.rejects(() => guardian.decideConfirmation(deviceId, reply.requestId, true));
  const expired = await guardian.guardian(deviceId, 'sleep'); await prisma.aiConfirmation.update({ where: { requestId: expired.requestId }, data: { expiresAt: new Date(0) } }); assert.equal((await guardian.decideConfirmation(deviceId, expired.requestId, true)).status, 'EXPIRED');
  const unsupported = await guardian.guardian(deviceId, 'delete files'); assert.match(unsupported.message, /Unsupported/); assert.equal(unsupported.preview, undefined);
  assert.equal(await prisma.commandLog.count({ where: { deviceId, command: { in: ['SLEEP', 'RESTART', 'SHUTDOWN', 'HIBERNATE'] } } }), 0);
});
