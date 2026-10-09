/** Run privileged UI E2Es against one disposable backend and disposable agent. */
import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

const ROOT = resolve(import.meta.dirname, '..');
const PORT = 3011;
const BASE = `http://127.0.0.1:${PORT}`;
const TMP = mkdtempSync(join(tmpdir(), 'vigilia-privileged-e2e-'));
const DB = join(TMP, 'e2e.db');
const TOKEN = join(TMP, 'agent.token');
const HOME = join(TMP, 'home');
const CACHE = join(HOME, '.cache', 'vigilia-cleaner-e2e');
let backend;
let agent;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function waitFor(check, label) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const value = await check();
      if (value) return value;
    } catch {}
    await sleep(250);
  }
  throw new Error(`timeout: ${label}`);
}

function run(script, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], { cwd: ROOT, env, stdio: 'inherit' });
    child.once('error', reject);
    child.once('exit', (code) => code === 0 ? resolve() : reject(new Error(`${script} exited ${code}`)));
  });
}

async function main() {
  try {
    execFileSync(process.execPath, [join(ROOT, 'node_modules/prisma/build/index.js'), 'db', 'push', '--force-reset', '--skip-generate', `--schema=${join(ROOT, 'backend/prisma/schema.prisma')}`], {
      cwd: ROOT,
      env: { ...process.env, DATABASE_URL: `file:${DB}` },
      stdio: 'ignore',
    });
    backend = spawn(process.execPath, ['backend/dist/main.js'], {
      cwd: ROOT,
      env: { ...process.env, HOST: '127.0.0.1', PORT: String(PORT), DATABASE_URL: `file:${DB}`, VIGILIA_CORS_ORIGIN: BASE, LOG_LEVEL: 'error' },
      stdio: 'ignore',
    });
    await waitFor(async () => (await fetch(`${BASE}/api/health`)).ok, 'backend health');
    mkdirSync(CACHE, { recursive: true });
    writeFileSync(join(CACHE, 'seed.bin'), Buffer.alloc(1024, 0x61));
    agent = spawn('python3', ['agents/linux/main.py'], {
      cwd: ROOT,
      env: { ...process.env, HOME, XDG_CACHE_HOME: join(HOME, '.cache'), VIGILIA_BACKEND_URL: BASE, VIGILIA_AGENT_NAME: 'Privileged E2E Laptop', VIGILIA_AGENT_TOKEN_FILE: TOKEN, VIGILIA_STATE_DIR: join(TMP, 'state'), VIGILIA_TELEMETRY_INTERVAL: '1', VIGILIA_HEARTBEAT_INTERVAL: '1', VIGILIA_STORAGE_INTERVAL: '1' },
      stdio: 'ignore',
    });
    const device = await waitFor(async () => {
      const response = await fetch(`${BASE}/api/devices`);
      return (await response.json()).devices?.find((item) => item.name === 'Privileged E2E Laptop' && item.status === 'online');
    }, 'agent online');
    await waitFor(() => existsSync(TOKEN), 'agent token');
    const pair = await fetch(`${BASE}/api/devices/${device.id}/pair`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${readFileSync(TOKEN, 'utf8').trim()}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ deviceId: device.id }),
    });
    const commandToken = (await pair.json()).commandToken;
    if (!pair.ok || !commandToken) throw new Error('disposable device pairing failed');
    const testEnv = { ...process.env, HOME, XDG_CACHE_HOME: join(HOME, '.cache'), VIGILIA_BASE_URL: BASE, VIGILIA_DEVICE_ID: device.id, VIGILIA_COMMAND_TOKEN: commandToken, VIGILIA_CLEANER_E2E_ROOT: CACHE, VIGILIA_CLEANER_E2E_ACTION: 'CLEAN_USER_CACHE' };
    for (const script of ['mobile/e2e-applications.mjs', 'mobile/e2e-activity.mjs', 'mobile/e2e-cleaner.mjs', 'mobile/e2e-security.mjs', 'mobile/e2e-phase5.mjs']) {
      await run(script, testEnv);
    }
    console.log('privileged e2e: all 5 suites passed');
  } finally {
    if (agent && !agent.killed) agent.kill('SIGTERM');
    if (backend && !backend.killed) backend.kill('SIGTERM');
    await sleep(300);
    rmSync(TMP, { recursive: true, force: true });
  }
}

main().catch((error) => { console.error(`privileged e2e failed: ${error.message}`); process.exitCode = 1; });
