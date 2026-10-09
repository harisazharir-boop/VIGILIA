/** Disposable SQLite DELETE versus WAL concurrency benchmark. Never targets production. */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, existsSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { PrismaClient } from '@prisma/client';

const ROOT = resolve(import.meta.dirname, '..');
const DIR = mkdtempSync(join(tmpdir(), 'vigilia-sqlite-bench-'));
const RUNS = [{ devices: 1, operations: 120 }, { devices: 8, operations: 120 }];

const percentile = (values, p) => values.length ? values[Math.min(values.length - 1, Math.ceil(values.length * p) - 1)] : 0;
const stats = (values) => { const sorted = [...values].sort((a, b) => a - b); return { count: sorted.length, medianMs: Number(percentile(sorted, .5).toFixed(3)), p95Ms: Number(percentile(sorted, .95).toFixed(3)), p99Ms: Number(percentile(sorted, .99).toFixed(3)) }; };
const size = (path) => existsSync(path) ? statSync(path).size : 0;

async function run(mode, workload) {
  const db = join(DIR, `${mode}-${workload.devices}.db`);
  const url = `file:${db}`;
  execFileSync(process.execPath, [join(ROOT, 'node_modules/prisma/build/index.js'), 'db', 'push', '--force-reset', '--skip-generate', `--schema=${join(ROOT, 'backend/prisma/schema.prisma')}`], { cwd: ROOT, env: { ...process.env, DATABASE_URL: url }, stdio: 'ignore' });
  const setup = new PrismaClient({ datasources: { db: { url } } });
  await setup.$connect();
  await setup.$queryRawUnsafe(`PRAGMA journal_mode=${mode}`);
  await setup.$executeRawUnsafe('PRAGMA synchronous=NORMAL');
  await setup.device.createMany({ data: Array.from({ length: workload.devices }, (_, index) => ({ id: `cmbenchdevice${index}`, name: `bench-${index}`, os: 'linux', hostname: `bench-${index}` })) });
  await setup.$disconnect();
  const clients = Array.from({ length: workload.devices }, () => new PrismaClient({ datasources: { db: { url } } }));
  await Promise.all(clients.map((client) => client.$connect()));
  if (mode === 'WAL') await Promise.all(clients.map((client) => client.$queryRawUnsafe('PRAGMA wal_autocheckpoint=0')));
  const writes = []; const reads = []; let locked = 0;
  const started = performance.now();
  await Promise.all(clients.map(async (client, index) => {
    const deviceId = `cmbenchdevice${index}`;
    for (let operation = 0; operation < workload.operations; operation += 1) {
      const writeStart = performance.now();
      try {
        await client.$transaction([
          client.telemetry.upsert({ where: { deviceId }, create: { deviceId, sample: { cpu: operation % 100, ram: 50, storage: 40, timestamp: new Date().toISOString() } }, update: { sample: { cpu: operation % 100, ram: 50, storage: 40, timestamp: new Date().toISOString() } } }),
          client.device.update({ where: { id: deviceId }, data: { status: 'online', lastSeen: new Date() } }),
          client.aiAudit.create({ data: { requestId: `req_bench_${mode}_${index}_${operation}`, deviceId, tool: 'INSPECT_HEALTH', status: 'SUCCEEDED', reason: 'benchmark' } }),
          client.aiMessage.create({ data: { deviceId, role: 'assistant', content: 'benchmark' } }),
        ]);
      } catch (error) {
        if (/busy|locked/i.test(String(error))) locked += 1;
        else throw error;
      }
      writes.push(performance.now() - writeStart);
      const readStart = performance.now();
      await client.telemetry.findUnique({ where: { deviceId } });
      reads.push(performance.now() - readStart);
    }
  }));
  const elapsedMs = performance.now() - started;
  const beforeCheckpointWalBytes = size(`${db}-wal`);
  const checkpointResult = mode === 'WAL' ? await clients[0].$queryRawUnsafe('PRAGMA wal_checkpoint(TRUNCATE)') : [];
  const afterCheckpointWalBytes = size(`${db}-wal`);
  await Promise.all(clients.map((client) => client.$disconnect()));
  const checkpoint = new PrismaClient({ datasources: { db: { url } } });
  await checkpoint.$connect();
  const journalMode = await checkpoint.$queryRawUnsafe('PRAGMA journal_mode');
  const integrity = await checkpoint.$queryRawUnsafe('PRAGMA integrity_check');
  await checkpoint.$disconnect();
  const reopened = new PrismaClient({ datasources: { db: { url } } });
  await reopened.$connect();
  const recovered = await reopened.device.count();
  await reopened.$disconnect();
  return { mode, devices: workload.devices, operationsPerDevice: workload.operations, writes: stats(writes), reads: stats(reads), throughputOpsPerSecond: Number(((workload.devices * workload.operations) / (elapsedMs / 1000)).toFixed(2)), lockedErrors: locked, journalMode, integrity, walBytesBeforeCheckpoint: beforeCheckpointWalBytes, walBytesAfterCheckpoint: afterCheckpointWalBytes, checkpointResult, recoveredDevices: recovered };
}

try {
  const results = [];
  for (const workload of RUNS) for (const mode of ['DELETE', 'WAL']) results.push(await run(mode, workload));
  console.log(JSON.stringify({ methodology: { schema: 'current backend/prisma/schema.prisma', workload: 'per operation: telemetry upsert + device status update + AiAudit create + AiMessage create, followed by telemetry read', clients: 'one Prisma client per simulated device, concurrent device loops', tempDirectory: DIR }, results }, (_key, value) => typeof value === 'bigint' ? Number(value) : value, 2));
} finally {
  rmSync(DIR, { recursive: true, force: true });
}
