import { after, before, describe, test } from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import { PrismaClient } from '@prisma/client';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const dir = mkdtempSync(path.join(tmpdir(), 'vigilia-hardware-'));
const url = `file:${path.join(dir, 'test.db')}`;
process.env.DATABASE_URL = url;
execFileSync(process.execPath, [path.join(root, 'node_modules/prisma/build/index.js'), 'db', 'push', '--force-reset', '--skip-generate', `--schema=${path.join(root, 'backend/prisma/schema.prisma')}`], { cwd: root, env: { ...process.env, DATABASE_URL: url }, stdio: 'ignore' });
const deviceId = 'cmphase8hardwaredevice'; const otherId = 'cmphase8otherdevice'; const token = 'vc_phase8_token';
let prisma: PrismaClient; let app: ReturnType<typeof Fastify>;

before(async () => {
  prisma = new PrismaClient({ datasources: { db: { url } } }); await prisma.$connect();
  const { hardwareRoutes } = await import('../src/modules/hardware/routes.js');
  app = Fastify(); await app.register(hardwareRoutes, { prefix: '/api' }); await app.ready();
  await prisma.device.createMany({ data: [{ id: deviceId, name: 'Hardware', os: 'linux', hostname: 'hardware', commandToken: token }, { id: otherId, name: 'Other', os: 'linux', hostname: 'other', commandToken: 'vc_other' }] });
  await prisma.telemetry.create({ data: { deviceId, sample: { deviceId, timestamp: new Date().toISOString(), hardware: { capabilities: { gpu: false, gpuVram: false, cpuTemperature: false, gpuTemperature: false, fanSpeed: false, battery: false, charging: false } }, networkTelemetry: { capabilities: { gateway: false, dns: false, wifiSignal: false, latency: false, packetLoss: false, throughput: false }, connected: false } } } });
});
after(async () => { await app.close(); await prisma.$disconnect(); rmSync(dir, { recursive: true, force: true }); });

describe('phase 8 hardware controls', { concurrency: false }, () => {
  test('rejects unauthenticated, cross-device, malformed and out-of-range controls', async () => {
    const missing = await app.inject({ method: 'POST', url: `/api/hardware/${deviceId}/controls`, payload: { control: 'WIFI', value: true } });
    const cross = await app.inject({ method: 'POST', url: `/api/hardware/${deviceId}/controls`, headers: { 'x-vigilia-command-token': 'vc_other' }, payload: { control: 'WIFI', value: true } });
    const range = await app.inject({ method: 'POST', url: `/api/hardware/${deviceId}/controls`, headers: { 'x-vigilia-command-token': token }, payload: { control: 'VOLUME', value: 101 } });
    const type = await app.inject({ method: 'POST', url: `/api/hardware/${deviceId}/controls`, headers: { 'x-vigilia-command-token': token }, payload: { control: 'CAMERA', value: 1 } });
    assert.equal(missing.statusCode, 401); assert.equal(cross.statusCode, 401); assert.equal(range.statusCode, 400); assert.equal(type.statusCode, 400);
  });
  test('returns audited explicit unsupported result and exposes read-only diagnostics', async () => {
    const control = await app.inject({ method: 'POST', url: `/api/hardware/${deviceId}/controls`, headers: { 'x-vigilia-command-token': token }, payload: { control: 'WIFI', value: true } });
    assert.equal(control.statusCode, 501); assert.deepEqual(control.json().result, { control: 'WIFI', status: 'UNSUPPORTED', reason: 'native control unavailable' });
    const audit = await prisma.hardwareAudit.findFirst({ where: { deviceId }, orderBy: { createdAt: 'desc' } });
    assert.equal(audit?.control, 'WIFI'); assert.equal(audit?.status, 'UNSUPPORTED');
    const hardware = await app.inject({ method: 'GET', url: `/api/hardware/${deviceId}` }); const network = await app.inject({ method: 'GET', url: `/api/network/${deviceId}/diagnostics` });
    assert.equal(hardware.statusCode, 200); assert.equal(network.statusCode, 200); assert.equal(network.json().diagnostic.connected, false);
  });
});
