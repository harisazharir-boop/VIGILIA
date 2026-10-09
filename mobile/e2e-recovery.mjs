/** Backend restart recovery against a disposable current-schema SQLite database. */
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');
const PORT = 3012;
const BASE = `http://127.0.0.1:${PORT}`;
const TMP = mkdtempSync(join(tmpdir(), 'vigilia-recovery-e2e-'));
const DB = join(TMP, 'recovery.db');
const TOKEN = join(TMP, 'agent.token');
const STATE = join(TMP, 'state');
let backend;
let agent;
let passed = 0;
let agentOutput = '';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function assert(condition, label) {
  if (!condition) throw new Error(label);
  passed += 1;
  console.log(`  PASS ${label}`);
}
async function waitFor(check, label, attempts = 120) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const value = await check();
      if (value) return value;
    } catch {}
    await sleep(250);
  }
  throw new Error(`timeout: ${label}`);
}
function startBackend() {
  return spawn(process.execPath, ['backend/dist/main.js'], {
    cwd: ROOT,
    env: { ...process.env, HOST: '127.0.0.1', PORT: String(PORT), DATABASE_URL: `file:${DB}`, VIGILIA_CORS_ORIGIN: BASE, LOG_LEVEL: 'error' },
    stdio: 'ignore',
  });
}
function stop(child) {
  return new Promise((resolve) => {
    if (!child || child.exitCode !== null || child.killed) return resolve();
    child.once('exit', resolve);
    child.kill('SIGTERM');
  });
}
async function health() {
  const response = await fetch(`${BASE}/api/health`);
  return response.ok ? response.json() : null;
}

async function main() {
  try {
    execFileSync(process.execPath, [join(ROOT, 'node_modules/prisma/build/index.js'), 'db', 'push', '--force-reset', '--skip-generate', `--schema=${join(ROOT, 'backend/prisma/schema.prisma')}`], {
      cwd: ROOT,
      env: { ...process.env, DATABASE_URL: `file:${DB}` },
      stdio: 'ignore',
    });
    backend = startBackend();
    const firstHealth = await waitFor(health, 'initial health');
    assert(firstHealth.status === 'ok' && firstHealth.version === '1.0.0' && firstHealth.protocolVersion === 1, 'initial backend health metadata');
    agent = spawn('python3', ['agents/linux/main.py'], {
      cwd: ROOT,
      env: { ...process.env, VIGILIA_BACKEND_URL: BASE, VIGILIA_AGENT_NAME: 'Recovery E2E Laptop', VIGILIA_AGENT_TOKEN_FILE: TOKEN, VIGILIA_STATE_DIR: STATE, VIGILIA_TELEMETRY_INTERVAL: '1', VIGILIA_HEARTBEAT_INTERVAL: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    agent.stdout.on('data', (chunk) => { agentOutput += chunk; });
    agent.stderr.on('data', (chunk) => { agentOutput += chunk; });
    const device = await waitFor(async () => {
      const response = await fetch(`${BASE}/api/devices`);
      return (await response.json()).devices?.find((item) => item.name === 'Recovery E2E Laptop' && item.status === 'online');
    }, 'agent registration and authenticated connection');
    await waitFor(() => existsSync(TOKEN), 'agent credential persistence');
    const initialTelemetry = await waitFor(async () => {
      const response = await fetch(`${BASE}/api/monitoring/${device.id}/latest`);
      return response.ok ? response.json() : null;
    }, 'initial telemetry');
    assert(Boolean(initialTelemetry.telemetry), 'authenticated agent telemetry stored before restart');
    const verify = await fetch(`${BASE}/api/auth/verify`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ deviceId: device.id, token: readFileSync(TOKEN, 'utf8').trim() }),
    });
    assert((await verify.json()).valid === true, 'stored agent credential authenticates before restart');
    await stop(backend);
    assert(backend.exitCode === 0, 'backend graceful SIGTERM shutdown');
    backend = startBackend();
    const recoveredHealth = await waitFor(health, 'restarted health');
    assert(recoveredHealth.status === 'ok' && recoveredHealth.protocolVersion === 1, 'backend health recovers after restart');
    const recoveredDevice = await waitFor(async () => {
      const response = await fetch(`${BASE}/api/devices/${device.id}`);
      if (!response.ok) return null;
      const body = await response.json();
      return body.status === 'online' ? body : null;
    }, 'agent reconnect');
    assert(recoveredDevice.id === device.id, 'agent reconnect preserves device identity');
    const recoveredTelemetry = await waitFor(async () => {
      const response = await fetch(`${BASE}/api/monitoring/${device.id}/latest`);
      if (!response.ok) return null;
      const body = await response.json();
      return new Date(body.timestamp).getTime() > new Date(initialTelemetry.timestamp).getTime() ? body : null;
    }, 'telemetry after reconnect');
    assert(Boolean(recoveredTelemetry.telemetry), 'telemetry resumes after backend restart');
    const integrity = execFileSync('python3', ['-c', "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(db.execute('PRAGMA integrity_check').fetchone()[0]); db.close()", DB], { encoding: 'utf8' }).trim();
    assert(integrity === 'ok', 'SQLite integrity check passes after recovery');
    console.log(`restart recovery e2e: PASS (${passed}/8 assertions)`);
  } finally {
    await stop(agent);
    await stop(backend);
    rmSync(TMP, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(`restart recovery e2e: FAIL ${error.message}\nagent: ${agentOutput.slice(-1200)}`); process.exitCode = 1; });
